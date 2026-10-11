"""Minimal, model-agnostic tool-using agent loop (SPEC Section 6.3).

The model gets four tools, executed inside its sandbox container:

    bash(command)               run a shell command in /workspace (60 s timeout, output truncated to 10k chars)
    read_file(path)             read a file under /workspace
    write_file(path, content)   write a file under /workspace
    submit()                    end the episode

The episode ends on submit, or when any budget is hit (tool calls, wall clock,
cost, tokens), or after repeated turns without tool calls. Every model turn,
tool call and tool result is logged to transcript.jsonl.
"""

import json
import time
from dataclasses import asdict, dataclass

from harness.providers import ContextOverflow, Price, ProviderError, ToolSpec, Usage
from harness.sandbox import BASH_TIMEOUT, MAX_OUTPUT_CHARS, MAX_READ_CHARS, SandboxError, truncate

TOOLS = [
    ToolSpec("bash", f"Run a shell command (bash -c) in /workspace inside the sandbox. Times out after {BASH_TIMEOUT} "
                     f"seconds; combined stdout/stderr is truncated to {MAX_OUTPUT_CHARS} characters. No network.",
             {"type": "object", "properties": {"command": {"type": "string", "description": "The command to run."}},
              "required": ["command"]}),
    ToolSpec("read_file", "Read a text file under /workspace. Relative paths are relative to /workspace.",
             {"type": "object", "properties": {"path": {"type": "string", "description": "File path."}},
              "required": ["path"]}),
    ToolSpec("write_file", "Create or overwrite a text file under /workspace with the given content "
                           "(parent directories are created).",
             {"type": "object", "properties": {"path": {"type": "string", "description": "File path."},
                                               "content": {"type": "string", "description": "Full file content."}},
              "required": ["path", "content"]}),
    ToolSpec("submit", "Finish the task. Call this once /workspace/emulator.py is complete. This ends the episode.",
             {"type": "object", "properties": {}, "required": []}),
]

SYSTEM_PROMPT = """You are an autonomous software engineer working alone in a sandboxed Linux container \
(Python 3.12, standard library only, no internet access). Nobody will answer questions: make reasonable \
decisions and keep going until the task is complete.

You act only through tools: bash, read_file, write_file, and submit. Your working directory is /workspace.

Budget for this task: at most {max_tool_calls} tool calls and {wall_clock} of wall-clock time. \
Every tool result shows how much budget is left. When the budget runs out the episode ends and \
/workspace/emulator.py is graded as it is, so save working progress early and often.

When you are confident the task is complete, call submit."""

NUDGE = ("You did not call a tool. Keep working on the task using the tools (bash, read_file, write_file). "
         "When /workspace/emulator.py is complete, call submit.")


@dataclass
class Budget:
    max_tool_calls: int = 150
    max_wall_clock_s: float = 7200.0
    max_cost_usd: float = None
    max_total_tokens: int = None
    max_no_tool_turns: int = 3


def format_duration(seconds):
    minutes = int(round(seconds / 60))
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


class Transcript:
    def __init__(self, path):
        self.path = path
        self.file = open(path, "a", buffering=1) if path else None

    def log(self, event):
        if self.file and not self.file.closed:
            event = {"t": round(time.time(), 3), **event}
            self.file.write(json.dumps(event, default=str) + "\n")

    def close(self):
        if self.file:
            self.file.close()


def execute_tool(sandbox, call):
    """Run one tool call. Returns (content, is_error, submitted)."""
    if call.error:
        return f"Error: {call.error}", True, False
    try:
        if call.name == "bash":
            code, output, timed_out = sandbox.bash(call.args["command"])
            note = f"[timed out after {BASH_TIMEOUT} s] " if timed_out else ""
            return f"{truncate(output)}\n{note}[exit code {code}]", False, False
        if call.name == "read_file":
            content = sandbox.read_file(call.args["path"])
            if len(content) > MAX_READ_CHARS:
                content = content[:MAX_READ_CHARS] + f"\n[... truncated: file has {len(content)} characters; use bash to read the rest]"
            return content, False, False
        if call.name == "write_file":
            path = sandbox.write_file(call.args["path"], call.args["content"])
            return f"Wrote {len(call.args['content'].encode())} bytes to {path}", False, False
        if call.name == "submit":
            return "Submitted. The episode is over.", False, True
        return f"Error: unknown tool '{call.name}'", True, False
    except SandboxError as e:
        return f"Error: {e}", True, False
    except Exception as e:  # sandbox infrastructure failure: report it to the model, keep going
        return f"Error: tool failed: {e!r}", True, False


def run_episode(client, sandbox, prompt, budget=None, price=None, transcript_path=None, transcript=None,
                clock=time.monotonic):
    """Drive one episode to completion. Returns a JSON-serializable summary.

    Pass either ``transcript_path`` (the episode owns the file) or an open
    ``transcript`` shared with the client's own logging (the caller closes it).
    """
    budget = budget or Budget()
    price = price or Price()
    owns_transcript = transcript is None
    transcript = transcript or Transcript(transcript_path)
    start = clock()
    usage = Usage()
    tool_calls = 0
    turns = 0
    no_tool_turns = 0
    end_reason = None
    tool_counts = {}

    def elapsed():
        return clock() - start

    def footer():
        return (f"\n[budget: {tool_calls}/{budget.max_tool_calls} tool calls used, "
                f"{format_duration(elapsed())} of {format_duration(budget.max_wall_clock_s)} elapsed]")

    def over_budget():
        if tool_calls >= budget.max_tool_calls:
            return "max_tool_calls"
        if elapsed() >= budget.max_wall_clock_s:
            return "max_wall_clock"
        if budget.max_cost_usd is not None and price.cost(usage) >= budget.max_cost_usd:
            return "max_cost"
        if budget.max_total_tokens is not None and usage.input_tokens + usage.output_tokens >= budget.max_total_tokens:
            return "max_tokens"
        return None

    system = SYSTEM_PROMPT.format(max_tool_calls=budget.max_tool_calls,
                                  wall_clock=format_duration(budget.max_wall_clock_s))
    client.start(system, prompt, TOOLS)
    transcript.log({"type": "start", "system": system, "prompt": prompt, "tools": [asdict(t) for t in TOOLS],
                    "budget": asdict(budget)})
    try:
        while end_reason is None:
            end_reason = over_budget()
            if end_reason:
                break
            turns += 1
            request_start = time.monotonic()
            try:
                turn = client.step()
            except ContextOverflow as e:
                transcript.log({"type": "error", "kind": "context_overflow", "detail": str(e)[:2000]})
                end_reason = "context_overflow"
                break
            except ProviderError as e:
                transcript.log({"type": "error", "kind": "api_error", "detail": str(e)[:2000]})
                end_reason = "api_error"
                break
            usage.add(turn.usage)
            transcript.log({"type": "model_turn", "turn": turns, "latency_s": round(time.monotonic() - request_start, 2),
                            "stop_reason": turn.stop_reason, "text": turn.text,
                            "tool_calls": [asdict(c) for c in turn.tool_calls], "usage": asdict(turn.usage),
                            "raw": turn.raw})
            if turn.refusal:
                end_reason = "refusal"
                # answer any tool calls so the conversation stays well-formed, then stop
                if turn.tool_calls:
                    client.add_tool_results([(c.id, "Not executed.", True) for c in turn.tool_calls])
                break
            if not turn.tool_calls:
                no_tool_turns += 1
                if no_tool_turns >= budget.max_no_tool_turns:
                    end_reason = "no_tool_calls"
                    break
                client.add_user_message(NUDGE)
                transcript.log({"type": "nudge", "text": NUDGE})
                continue
            no_tool_turns = 0
            results = []
            submitted = False
            for call in turn.tool_calls:
                if submitted or over_budget() in ("max_tool_calls", "max_wall_clock"):
                    results.append((call.id, "Not executed: the episode has ended.", True))
                    continue
                tool_start = time.monotonic()
                content, is_error, did_submit = execute_tool(sandbox, call)
                tool_calls += 1
                tool_counts[call.name] = tool_counts.get(call.name, 0) + 1
                submitted = submitted or did_submit
                transcript.log({"type": "tool_result", "turn": turns, "call_id": call.id, "name": call.name,
                                "is_error": is_error, "duration_s": round(time.monotonic() - tool_start, 2),
                                "content": content})
                results.append((call.id, content + ("" if did_submit else footer()), is_error))
            client.add_tool_results(results)
            if submitted:
                end_reason = "submitted"
    finally:
        summary = {
            "end_reason": end_reason or "unknown",
            "submitted": end_reason == "submitted",
            "turns": turns,
            "tool_calls": tool_calls,
            "tool_counts": tool_counts,
            "wall_clock_s": round(elapsed(), 1),
            "usage": asdict(usage),
            "cost_usd": round(price.cost(usage), 4),
        }
        transcript.log({"type": "end", **summary})
        if owns_transcript:
            transcript.close()
    return summary
