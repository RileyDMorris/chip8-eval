"""Model clients for the agent loop.

Each client keeps the conversation in its provider's native format and is
append-only: assistant turns are stored exactly as returned (so reasoning
items, thinking blocks and thought signatures survive), tool results are
appended after them. The agent loop only sees ``Turn`` objects.

Providers:
    openai_chat       any OpenAI-compatible /chat/completions endpoint (OpenRouter, Gemini, OpenAI, ...)
    openai_responses  OpenAI Responses API (reasoning items passed back across turns)
    anthropic         Anthropic Messages API via the official SDK (streaming, prompt caching)
    scripted          replays a fixed list of tool calls (pipeline tests; no API)
"""

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class ProviderError(Exception):
    pass


class ContextOverflow(ProviderError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    error: str = None          # set when arguments could not be parsed/validated


@dataclass
class Usage:
    input_tokens: int = 0       # all prompt tokens, cached or not
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, other):
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class Turn:
    text: str
    tool_calls: list
    usage: Usage
    stop_reason: str
    raw: object = None          # provider response, JSON-serializable, for the transcript
    refusal: bool = False
    truncated: bool = False     # output hit the token limit


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict            # JSON schema (object)


@dataclass
class Price:
    """USD per million tokens."""
    input: float = 0.0
    output: float = 0.0
    cache_read: float = None    # defaults to input
    cache_write: float = None   # defaults to input

    def cost(self, usage):
        cache_read = self.input if self.cache_read is None else self.cache_read
        cache_write = self.input if self.cache_write is None else self.cache_write
        uncached = max(0, usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
        return (uncached * self.input + usage.cache_read_tokens * cache_read
                + usage.cache_write_tokens * cache_write + usage.output_tokens * self.output) / 1e6


def validate_args(spec, args):
    """Minimal JSON-schema check for our flat string-typed tool schemas."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    props = spec.parameters.get("properties", {})
    for name in spec.parameters.get("required", []):
        if name not in args:
            return f"missing required argument '{name}'"
    for name, value in args.items():
        if name not in props:
            return f"unexpected argument '{name}'"
        if props[name].get("type") == "string" and not isinstance(value, str):
            return f"argument '{name}' must be a string"
    return None


def parse_tool_args(spec_by_name, call_id, name, raw_args):
    spec = spec_by_name.get(name)
    if spec is None:
        return ToolCall(call_id, name, {}, error=f"unknown tool '{name}'")
    if isinstance(raw_args, dict):
        args = raw_args
    else:
        try:
            args = json.loads(raw_args) if raw_args not in (None, "") else {}
        except (json.JSONDecodeError, TypeError) as e:
            return ToolCall(call_id, name, {}, error=f"arguments are not valid JSON ({e}); raw: {str(raw_args)[:300]}")
    problem = validate_args(spec, args)
    return ToolCall(call_id, name, args if isinstance(args, dict) else {}, error=problem)


def post_json(url, payload, headers, timeout=600, max_attempts=8, log=None):
    """POST JSON with retries on rate limits, overloads and network errors."""
    body = json.dumps(payload).encode()
    delay = 2.0
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:4000]
            lowered = detail.lower()
            if e.code in (400, 413) and ("context" in lowered and ("length" in lowered or "window" in lowered)
                                         or "too long" in lowered or "maximum context" in lowered):
                raise ContextOverflow(detail) from e
            if e.code not in RETRYABLE_STATUS or attempt == max_attempts:
                raise ProviderError(f"HTTP {e.code}: {detail}") from e
            retry_after = e.headers.get("retry-after") if e.headers else None
            wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else delay
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            if attempt == max_attempts:
                raise ProviderError(f"request failed: {e!r}") from e
            wait = delay
        if log:
            log({"type": "retry", "attempt": attempt, "wait_s": round(wait, 1)})
        time.sleep(min(wait, 120) + random.uniform(0, 1))
        delay = min(delay * 2, 120)


# ----------------------------------------------------------------------------- OpenAI chat

class OpenAIChatClient:
    """OpenAI-compatible Chat Completions (OpenRouter, Gemini's OpenAI endpoint, OpenAI, local servers)."""

    def __init__(self, model, base_url, api_key, params=None, max_output_tokens=32000,
                 max_tokens_field="max_tokens", extra_headers=None, compact_after_tokens=None, log=None):
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}", **(extra_headers or {})}
        self.params = dict(params or {})
        self.max_output_tokens = max_output_tokens
        self.max_tokens_field = max_tokens_field
        self.compact_after_tokens = compact_after_tokens
        self.log = log
        self.messages = []
        self.tools = []
        self.specs = {}

    def start(self, system, user, tools):
        self.specs = {t.name: t for t in tools}
        self.tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                        "parameters": t.parameters}} for t in tools]
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def step(self):
        payload = {"model": self.model, "messages": self.messages, "tools": self.tools, "tool_choice": "auto",
                   self.max_tokens_field: self.max_output_tokens, **self.params}
        data = post_json(self.url, payload, self.headers, log=self.log)
        if "choices" not in data or not data["choices"]:
            raise ProviderError(f"no choices in response: {json.dumps(data)[:1000]}")
        choice = data["choices"][0]
        message = {k: v for k, v in choice["message"].items() if v is not None}
        message.setdefault("content", "")
        self.messages.append(message)
        calls = [parse_tool_args(self.specs, c.get("id") or f"call_{i}", c["function"]["name"],
                                 c["function"].get("arguments"))
                 for i, c in enumerate(message.get("tool_calls") or [])]
        raw_usage = data.get("usage") or {}
        usage = Usage(
            input_tokens=raw_usage.get("prompt_tokens", 0) or 0,
            output_tokens=raw_usage.get("completion_tokens", 0) or 0,
            cache_read_tokens=((raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
            reasoning_tokens=((raw_usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
        )
        finish = choice.get("finish_reason") or ""
        if self.compact_after_tokens and usage.input_tokens > self.compact_after_tokens:
            self.compact()
        text = message.get("content") if isinstance(message.get("content"), str) else ""
        return Turn(text=text or "", tool_calls=calls, usage=usage, stop_reason=finish, raw=data,
                    refusal=bool(message.get("refusal")), truncated=finish == "length")

    def add_tool_results(self, results):
        for call_id, content, _is_error in results:
            self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content})

    def add_user_message(self, text):
        self.messages.append({"role": "user", "content": text})

    def compact(self, keep_last=12):
        """Elide old tool outputs (never assistant turns) to stay under the context window."""
        tool_positions = [i for i, m in enumerate(self.messages) if m.get("role") == "tool"]
        for i in tool_positions[:-keep_last]:
            content = self.messages[i]["content"]
            if len(content) > 300 and not content.startswith("[elided"):
                self.messages[i]["content"] = f"[elided to save context: {len(content)} chars]\n" + content[:200]
        if self.log:
            self.log({"type": "compaction", "elided_before_index": tool_positions[-keep_last] if len(tool_positions) > keep_last else 0})


# ----------------------------------------------------------------------------- OpenAI Responses

class OpenAIResponsesClient:
    """OpenAI Responses API. Output items (incl. reasoning) are appended to the input list each turn."""

    def __init__(self, model, base_url, api_key, params=None, max_output_tokens=64000, store=False,
                 extra_headers=None, log=None):
        self.model = model
        self.url = base_url.rstrip("/") + "/responses"
        self.headers = {"Authorization": f"Bearer {api_key}", **(extra_headers or {})}
        self.params = dict(params or {})
        self.max_output_tokens = max_output_tokens
        self.store = store
        self.log = log
        self.instructions = ""
        self.items = []
        self.tools = []
        self.specs = {}

    def start(self, system, user, tools):
        self.specs = {t.name: t for t in tools}
        self.tools = [{"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
                      for t in tools]
        self.instructions = system
        self.items = [{"role": "user", "content": user}]

    def step(self):
        payload = {"model": self.model, "instructions": self.instructions, "input": self.items, "tools": self.tools,
                   "tool_choice": "auto", "max_output_tokens": self.max_output_tokens, "store": self.store,
                   **self.params}
        if not self.store:
            payload.setdefault("include", ["reasoning.encrypted_content"])
        data = post_json(self.url, payload, self.headers, log=self.log)
        output = data.get("output") or []
        self.items.extend(output)
        calls = []
        texts = []
        refusal = False
        for item in output:
            if item.get("type") == "function_call":
                calls.append(parse_tool_args(self.specs, item.get("call_id"), item.get("name"), item.get("arguments")))
            elif item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        texts.append(part.get("text", ""))
                    elif part.get("type") == "refusal":
                        refusal = True
        raw_usage = data.get("usage") or {}
        usage = Usage(
            input_tokens=raw_usage.get("input_tokens", 0) or 0,
            output_tokens=raw_usage.get("output_tokens", 0) or 0,
            cache_read_tokens=((raw_usage.get("input_tokens_details") or {}).get("cached_tokens") or 0),
            reasoning_tokens=((raw_usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0),
        )
        status = data.get("status", "")
        incomplete = (data.get("incomplete_details") or {}).get("reason", "")
        return Turn(text="\n".join(texts), tool_calls=calls, usage=usage, stop_reason=incomplete or status, raw=data,
                    refusal=refusal, truncated=incomplete == "max_output_tokens")

    def add_tool_results(self, results):
        for call_id, content, _is_error in results:
            self.items.append({"type": "function_call_output", "call_id": call_id, "output": content})

    def add_user_message(self, text):
        self.items.append({"role": "user", "content": text})


# ----------------------------------------------------------------------------- Anthropic

class AnthropicClient:
    """Anthropic Messages API via the official SDK.

    Streams each turn (large tool inputs such as whole files), caches the
    growing prefix automatically (top-level cache_control), and appends
    ``response.content`` unchanged so thinking blocks are preserved. History is
    never edited (preserved thinking requires an append-only conversation).
    """

    def __init__(self, model, api_key=None, params=None, max_output_tokens=64000, effort="high",
                 fallbacks=None, base_url=None, log=None):
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url, max_retries=8, timeout=900.0)
        self.model = model
        self.params = dict(params or {})
        self.max_output_tokens = max_output_tokens
        self.effort = effort
        self.fallbacks = fallbacks     # off by default: a fallback would grade a different model
        self.log = log
        self.system = ""
        self.messages = []
        self.tools = []
        self.specs = {}
        self.pending_text = None

    def start(self, system, user, tools):
        self.specs = {t.name: t for t in tools}
        self.tools = [{"name": t.name, "description": t.description, "input_schema": t.parameters,
                       "eager_input_streaming": True} for t in tools]
        self.system = system
        self.messages = [{"role": "user", "content": user}]

    def _request(self):
        kwargs = dict(model=self.model, max_tokens=self.max_output_tokens, system=self.system,
                      messages=self.messages, tools=self.tools, tool_choice={"type": "auto"},
                      cache_control={"type": "ephemeral"}, **self.params)
        if self.effort:
            kwargs["output_config"] = {**kwargs.get("output_config", {}), "effort": self.effort}
        if self.fallbacks:
            stream = self.client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"],
                                                      fallbacks=self.fallbacks, **kwargs)
        else:
            stream = self.client.messages.stream(**kwargs)
        with stream as s:
            return s.get_final_message()

    def step(self):
        for attempt in range(3):
            try:
                response = self._request()
                break
            except ValueError as e:      # unparseable streamed tool input: re-issue the request
                if self.log:
                    self.log({"type": "retry", "reason": f"invalid streamed tool input: {e}"[:300]})
                if attempt == 2:
                    raise ProviderError(f"model produced unparseable tool input 3 times: {e}") from e
            except self.anthropic.BadRequestError as e:
                message = str(e).lower()
                if "prompt is too long" in message or "context" in message and "window" in message:
                    raise ContextOverflow(str(e)) from e
                raise ProviderError(str(e)) from e
            except self.anthropic.APIError as e:
                raise ProviderError(str(e)) from e
        self.messages.append({"role": "assistant", "content": response.content})
        calls = []
        texts = []
        for block in response.content:
            if block.type == "tool_use":
                calls.append(parse_tool_args(self.specs, block.id, block.name, block.input))
            elif block.type == "text":
                texts.append(block.text)
        truncated = response.stop_reason == "max_tokens"
        refusal = response.stop_reason == "refusal"
        if truncated:
            for call in calls:
                call.error = "your output hit the max_tokens limit, so this tool call was cut off and not run"
        u = response.usage
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        usage = Usage(input_tokens=(u.input_tokens or 0) + cache_read + cache_write, output_tokens=u.output_tokens or 0,
                      cache_read_tokens=cache_read, cache_write_tokens=cache_write)
        raw = response.model_dump(mode="json")
        return Turn(text="\n".join(texts), tool_calls=calls, usage=usage, stop_reason=response.stop_reason or "",
                    raw=raw, refusal=refusal, truncated=truncated)

    def add_tool_results(self, results):
        blocks = [{"type": "tool_result", "tool_use_id": call_id, "content": content, **({"is_error": True} if err else {})}
                  for call_id, content, err in results]
        self.messages.append({"role": "user", "content": blocks})

    def add_user_message(self, text):
        self.messages.append({"role": "user", "content": text})


# ----------------------------------------------------------------------------- scripted

class ScriptedClient:
    """Replays a list of turns: each turn is {"text": str, "tool_calls": [{"name", "args"}]}.

    Used for pipeline tests and smoke runs without any API access. Args values of
    the form {"$file": path} are replaced with that host file's contents, and
    {"$reference": "{variant}"} with the bundled reference emulator (test fixture only).
    """

    def __init__(self, turns, log=None, substitutions=None):
        self.turns = list(turns)
        self.log = log
        self.substitutions = substitutions or {}
        self.position = 0
        self.history = []
        self.specs = {}

    def start(self, system, user, tools):
        self.specs = {t.name: t for t in tools}
        self.history = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def step(self):
        if self.position >= len(self.turns):
            turn = {"text": "(script finished)", "tool_calls": [{"name": "submit", "args": {}}]}
        else:
            turn = self.turns[self.position]
        self.position += 1
        calls = []
        for i, call in enumerate(turn.get("tool_calls", [])):
            args = {}
            for key, value in call.get("args", {}).items():
                if isinstance(value, dict) and "$file" in value:
                    with open(value["$file"]) as f:
                        value = f.read()
                elif isinstance(value, dict) and "$reference" in value:
                    from reference.bundle import bundle_source
                    value = bundle_source(value["$reference"].format(**self.substitutions))
                args[key] = value
            calls.append(parse_tool_args(self.specs, f"call_{self.position}_{i}", call["name"], args))
        self.history.append({"role": "assistant", "turn": turn})
        return Turn(text=turn.get("text", ""), tool_calls=calls, usage=Usage(), stop_reason="scripted", raw=turn)

    def add_tool_results(self, results):
        self.history.append({"role": "tool", "results": [list(r) for r in results]})

    def add_user_message(self, text):
        self.history.append({"role": "user", "content": text})


# ----------------------------------------------------------------------------- factory

PROVIDER_DEFAULTS = {
    "openrouter": {"kind": "openai_chat", "base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "openai": {"kind": "openai_responses", "base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    "openai_chat": {"kind": "openai_chat", "base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    "gemini": {"kind": "openai_chat", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_env": "GEMINI_API_KEY"},
    "anthropic": {"kind": "anthropic", "key_env": "ANTHROPIC_API_KEY"},
    "openai_compat": {"kind": "openai_chat", "key_env": None},   # any endpoint; set base_url (and key_env)
    "scripted": {"kind": "scripted"},
}


def make_client(model_cfg, log=None, variant="canonical"):
    """Build a client from a models.yaml entry (keys come from the host environment)."""
    provider = model_cfg["provider"]
    defaults = PROVIDER_DEFAULTS.get(provider)
    if defaults is None:
        raise ValueError(f"unknown provider {provider!r}")
    kind = model_cfg.get("kind", defaults["kind"])
    if kind == "scripted":
        with open(model_cfg["script"]) as f:
            return ScriptedClient(json.load(f)["turns"], log=log, substitutions={"variant": variant})
    key_env = model_cfg.get("key_env", defaults.get("key_env"))
    api_key = os.environ.get(key_env) if key_env else "unused"
    if not api_key:
        raise ProviderError(f"{key_env} is not set (add it to .env)")
    params = model_cfg.get("params") or {}
    max_out = model_cfg.get("max_output_tokens")
    if kind == "anthropic":
        return AnthropicClient(model_cfg["model"], api_key=api_key, params=params,
                               max_output_tokens=max_out or 64000, effort=model_cfg.get("effort", "high"),
                               fallbacks=model_cfg.get("fallbacks"), base_url=model_cfg.get("base_url"), log=log)
    base_url = model_cfg.get("base_url", defaults.get("base_url"))
    if kind == "openai_responses":
        return OpenAIResponsesClient(model_cfg["model"], base_url, api_key, params=params,
                                     max_output_tokens=max_out or 64000, store=model_cfg.get("store", False), log=log)
    return OpenAIChatClient(model_cfg["model"], base_url, api_key, params=params, max_output_tokens=max_out or 32000,
                            max_tokens_field=model_cfg.get("max_tokens_field", "max_tokens"),
                            compact_after_tokens=model_cfg.get("compact_after_tokens"), log=log)
