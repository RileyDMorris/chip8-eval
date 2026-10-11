"""Harness tests (milestone M3): provider clients, agent loop, sandbox isolation, end-to-end run."""

import json
import socket
import subprocess

import pytest

from harness.agent_loop import TOOLS, Budget, run_episode
from harness.providers import (AnthropicClient, OpenAIChatClient, OpenAIResponsesClient, Price, ProviderError,
                               ScriptedClient, Usage, make_client)
from harness.sandbox import SandboxError, resolve_workspace_path, truncate
from grader.run_candidate import docker_available
from tests.mock_servers import (MockServer, anthropic_stream, chat_tool_call, error_response, json_response,
                                responses_output)

needs_docker = pytest.mark.skipif(not docker_available(), reason="Docker daemon not available")


class FakeSandbox:
    """In-memory stand-in for the Docker sandbox."""

    def __init__(self):
        self.files = {}
        self.commands = []

    def bash(self, command, timeout=60):
        self.commands.append(command)
        return 0, f"ran: {command}", False

    def read_file(self, path):
        full = resolve_workspace_path(path)
        if full not in self.files:
            raise SandboxError(f"cat: {full}: No such file or directory")
        return self.files[full]

    def write_file(self, path, content):
        full = resolve_workspace_path(path)
        self.files[full] = content
        return full


def scripted(*turns):
    return ScriptedClient([{"text": "", "tool_calls": [{"name": n, "args": a} for n, a in calls]} for calls in turns])


# ---------------------------------------------------------------- helpers

def test_truncate_keeps_head_and_tail():
    text = "A" * 6000 + "B" * 6000
    out = truncate(text, 10_000)
    assert len(out) < 10_100
    assert out.startswith("A" * 100) and out.endswith("B" * 100)
    assert "omitted" in out


@pytest.mark.parametrize("path,ok", [("emulator.py", True), ("/workspace/a/b.py", True), ("../etc/passwd", False),
                                     ("/etc/passwd", False), ("/workspace/../root", False), ("", False)])
def test_workspace_path_resolution(path, ok):
    if ok:
        assert resolve_workspace_path(path).startswith("/workspace/")
    else:
        with pytest.raises(SandboxError):
            resolve_workspace_path(path)


def test_price_cost():
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_tokens=600_000, cache_write_tokens=100_000)
    price = Price(input=4.0, output=20.0, cache_read=0.2, cache_write=5.0)
    assert price.cost(usage) == pytest.approx(0.3 * 4.0 + 0.6 * 0.2 + 0.1 * 5.0 + 0.1 * 20.0)


# ---------------------------------------------------------------- agent loop

def test_episode_runs_tools_and_submits(tmp_path):
    client = scripted([("bash", {"command": "ls"})],
                      [("write_file", {"path": "emulator.py", "content": "print(1)"})],
                      [("read_file", {"path": "emulator.py"})],
                      [("submit", {})])
    sandbox = FakeSandbox()
    summary = run_episode(client, sandbox, "do it", transcript_path=tmp_path / "t.jsonl")
    assert summary["end_reason"] == "submitted"
    assert summary["tool_calls"] == 4
    assert sandbox.files["/workspace/emulator.py"] == "print(1)"
    events = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert events[0]["type"] == "start" and events[-1]["type"] == "end"
    results = [e for e in events if e["type"] == "tool_result"]
    assert results[2]["content"] == "print(1)"
    # tool results sent back to the model carry the remaining budget
    sent = [h for h in client.history if h["role"] == "tool"]
    assert "[budget: 1/150 tool calls used" in sent[0]["results"][0][1]


def test_tool_call_budget_ends_episode():
    client = scripted(*[[("bash", {"command": f"echo {i}"})] for i in range(10)])
    summary = run_episode(client, FakeSandbox(), "x", budget=Budget(max_tool_calls=3))
    assert summary["end_reason"] == "max_tool_calls"
    assert summary["tool_calls"] == 3


def test_parallel_calls_past_budget_are_not_executed():
    client = scripted([("bash", {"command": "a"}), ("bash", {"command": "b"}), ("bash", {"command": "c"})])
    sandbox = FakeSandbox()
    summary = run_episode(client, sandbox, "x", budget=Budget(max_tool_calls=2))
    assert sandbox.commands == ["a", "b"]
    assert summary["end_reason"] == "max_tool_calls"
    last = [h for h in client.history if h["role"] == "tool"][-1]["results"]
    assert last[2][1].startswith("Not executed")


def test_wall_clock_budget():
    ticks = iter(range(0, 10_000, 1000))
    client = scripted(*[[("bash", {"command": "x"})] for _ in range(10)])
    summary = run_episode(client, FakeSandbox(), "x", budget=Budget(max_wall_clock_s=2500), clock=lambda: next(ticks))
    assert summary["end_reason"] == "max_wall_clock"


def test_cost_budget():
    class Pricey(ScriptedClient):
        def step(self):
            turn = super().step()
            turn.usage = Usage(input_tokens=1_000_000, output_tokens=0)
            return turn

    client = Pricey([{"tool_calls": [{"name": "bash", "args": {"command": "x"}}]}] * 10)
    summary = run_episode(client, FakeSandbox(), "x", budget=Budget(max_cost_usd=2.5), price=Price(input=1.0))
    assert summary["end_reason"] == "max_cost"
    assert summary["turns"] == 3


def test_no_tool_turns_nudge_then_end():
    client = ScriptedClient([{"text": "thinking..."}] * 5)
    summary = run_episode(client, FakeSandbox(), "x", budget=Budget(max_no_tool_turns=3))
    assert summary["end_reason"] == "no_tool_calls"
    assert sum(1 for h in client.history if h["role"] == "user") == 3   # prompt + 2 nudges


def test_invalid_tool_arguments_are_reported_not_run():
    client = scripted([("bash", {"cmd": "ls"})], [("read_file", {"path": "../../etc/passwd"})],
                      [("nonexistent_tool", {})], [("submit", {})])
    sandbox = FakeSandbox()
    summary = run_episode(client, sandbox, "x")
    assert summary["end_reason"] == "submitted"
    assert sandbox.commands == []
    errors = [h["results"][0] for h in client.history if h["role"] == "tool"][:3]
    assert all(is_error for _, _, is_error in errors)
    assert "missing required argument 'command'" in errors[0][1]
    assert "inside /workspace" in errors[1][1]
    assert "unknown tool" in errors[2][1]


# ---------------------------------------------------------------- OpenAI chat client

def test_openai_chat_client_round_trip():
    responses = [
        error_response(429, "slow down", headers={"retry-after": "0"}),
        chat_tool_call([("bash", {"command": "ls"}), ("write_file", '{"path": "a.py", "content": "x"}')]),
        chat_tool_call([("bash", "{not json")]),
        chat_tool_call([], content="done thinking", finish="stop"),
    ]
    with MockServer(responses) as server:
        client = OpenAIChatClient("test-model", server.url, "sk-test", params={"temperature": 0})
        client.start("sys", "user prompt", TOOLS)
        turn = client.step()
        assert [c.name for c in turn.tool_calls] == ["bash", "write_file"]
        assert turn.tool_calls[1].args == {"path": "a.py", "content": "x"}
        assert turn.usage.input_tokens == 100 and turn.usage.cache_read_tokens == 50
        client.add_tool_results([("call_0", "out0", False), ("call_1", "out1", False)])
        turn = client.step()
        assert turn.tool_calls[0].error and "not valid JSON" in turn.tool_calls[0].error
        client.add_tool_results([("call_0", "Error", True)])
        turn = client.step()
        assert turn.tool_calls == [] and turn.text == "done thinking"
    body = server.requests[2]["body"]
    assert server.requests[1]["headers"]["Authorization"] == "Bearer sk-test"
    assert body["model"] == "test-model" and body["temperature"] == 0 and body["tool_choice"] == "auto"
    assert {t["function"]["name"] for t in body["tools"]} == {"bash", "read_file", "write_file", "submit"}
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "tool"]
    assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "bash"   # assistant turn echoed verbatim
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "call_0", "content": "out0"}


def test_openai_chat_client_gives_up_on_bad_request():
    with MockServer([error_response(400, "bad model")]) as server:
        client = OpenAIChatClient("m", server.url, "k")
        client.start("s", "u", TOOLS)
        with pytest.raises(ProviderError):
            client.step()


def test_openai_chat_preserves_provider_extras():
    """Fields like Gemini thought signatures must be echoed back untouched."""
    message = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'},
         "extra_content": {"google": {"thought_signature": "SIG=="}}}]}
    first = json_response({"choices": [{"message": message, "finish_reason": "tool_calls"}], "usage": {}})
    with MockServer([first, chat_tool_call([], content="ok", finish="stop")]) as server:
        client = OpenAIChatClient("m", server.url, "k")
        client.start("s", "u", TOOLS)
        client.step()
        client.add_tool_results([("c1", "out", False)])
        client.step()
    echoed = server.requests[1]["body"]["messages"][2]["tool_calls"][0]
    assert echoed["extra_content"] == {"google": {"thought_signature": "SIG=="}}


# ---------------------------------------------------------------- OpenAI responses client

def test_openai_responses_client_passes_reasoning_back():
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC"}
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "bash", "arguments": '{"command": "ls"}'}
    message = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "all done"}]}
    with MockServer([responses_output([reasoning, call]), responses_output([message])]) as server:
        client = OpenAIResponsesClient("gpt-x", server.url, "k", params={"reasoning": {"effort": "high"}}, store=False)
        client.start("sys", "prompt", TOOLS)
        turn = client.step()
        assert turn.tool_calls[0].args == {"command": "ls"}
        assert turn.usage.reasoning_tokens == 5
        client.add_tool_results([("call_1", "listing", False)])
        turn = client.step()
        assert turn.text == "all done" and not turn.tool_calls
    first, second = (r["body"] for r in server.requests)
    assert first["instructions"] == "sys" and first["store"] is False
    assert first["include"] == ["reasoning.encrypted_content"]
    assert first["tools"][0] == {"type": "function", "name": "bash", "description": TOOLS[0].description,
                                 "parameters": TOOLS[0].parameters}
    assert second["input"][1] == reasoning and second["input"][2] == call
    assert second["input"][3] == {"type": "function_call_output", "call_id": "call_1", "output": "listing"}


# ---------------------------------------------------------------- Anthropic client

def test_anthropic_client_streams_caches_and_preserves_thinking():
    first = anthropic_stream([
        {"type": "thinking", "thinking": "plan", "signature": "SIG1"},
        {"type": "text", "text": "Let me look."},
        {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"command": "ls"}},
        {"type": "tool_use", "id": "toolu_2", "name": "write_file", "input": {"path": "e.py"}},
    ])
    second = anthropic_stream([{"type": "text", "text": "Done."}], stop_reason="end_turn")
    with MockServer([first, second]) as server:
        client = AnthropicClient("claude-opus-5-5", api_key="sk-ant-test", base_url=server.url, effort="high")
        client.start("sys", "prompt", TOOLS)
        turn = client.step()
        assert turn.text == "Let me look."
        assert [c.name for c in turn.tool_calls] == ["bash", "write_file"]
        assert turn.tool_calls[0].error is None
        assert "missing required argument 'content'" in turn.tool_calls[1].error   # validated, not run
        assert turn.usage.input_tokens == 100 and turn.usage.cache_read_tokens == 30
        client.add_tool_results([("toolu_1", "files", False), ("toolu_2", "Error: bad args", True)])
        turn = client.step()
        assert turn.stop_reason == "end_turn" and not turn.tool_calls
    body1, body2 = (r["body"] for r in server.requests)
    assert body1["model"] == "claude-opus-5-5" and body1["stream"] is True
    assert body1["cache_control"] == {"type": "ephemeral"}
    assert body1["output_config"] == {"effort": "high"}
    assert body1["tool_choice"] == {"type": "auto"}
    assert all(t["eager_input_streaming"] is True for t in body1["tools"])
    assert "thinking" not in body1          # Opus 5.5: adaptive thinking by default
    assistant = body2["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {"type": "thinking", "thinking": "plan", "signature": "SIG1"}
    tool_results = body2["messages"][2]["content"]
    assert [b["tool_use_id"] for b in tool_results] == ["toolu_1", "toolu_2"]
    assert tool_results[1]["is_error"] is True


def test_anthropic_truncated_tool_call_is_not_run():
    stream = anthropic_stream([{"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"command": "ls"}}],
                              stop_reason="max_tokens")
    with MockServer([stream]) as server:
        client = AnthropicClient("claude-opus-5-5", api_key="k", base_url=server.url)
        client.start("s", "p", TOOLS)
        turn = client.step()
    assert turn.truncated and "max_tokens" in turn.tool_calls[0].error


def test_make_client_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        make_client({"name": "x", "provider": "openai", "model": "gpt"})


# ---------------------------------------------------------------- docker sandbox

@needs_docker
@pytest.mark.docker
def test_agent_container_has_no_network():
    from harness.sandbox import Sandbox

    with Sandbox() as sandbox:
        code, output, _ = sandbox.bash(
            "python3 - <<'EOF'\n"
            "import socket\n"
            "for target in [('1.1.1.1', 443), ('8.8.8.8', 53)]:\n"
            "    try:\n"
            "        socket.create_connection(target, timeout=3); print('CONNECTED', target)\n"
            "    except OSError as e:\n"
            "        print('blocked', target, e.errno)\n"
            "try:\n"
            "    socket.getaddrinfo('pypi.org', 443); print('DNS RESOLVED')\n"
            "except OSError:\n"
            "    print('dns blocked')\n"
            "EOF\n"
            "pip download --no-deps --timeout 5 requests 2>&1 | tail -1")
        assert "CONNECTED" not in output and "DNS RESOLVED" not in output
        assert output.count("blocked") >= 3
        inspect = subprocess.run(["docker", "inspect", "-f", "{{.HostConfig.NetworkMode}}", sandbox.name],
                                 capture_output=True, text=True).stdout.strip()
        assert inspect == "none"


@needs_docker
@pytest.mark.docker
def test_sandbox_tools_and_isolation(tmp_path):
    from harness.sandbox import Sandbox
    from pathlib import Path

    task = Path(__file__).resolve().parent.parent / "tasks" / "canonical"
    sandbox = Sandbox()
    with sandbox:
        sandbox.copy_task(task)
        code, output, _ = sandbox.bash("id -u; ls; test -w prompt.md && echo writable")
        lines = output.split()
        assert lines[0] == "1000"
        assert {"prompt.md", "machine_spec.md", "public_tests", "run_public_tests.py"} <= set(lines)
        assert "writable" in lines
        assert sandbox.write_file("sub/dir/x.txt", "hello\n") == "/workspace/sub/dir/x.txt"
        assert sandbox.read_file("/workspace/sub/dir/x.txt") == "hello\n"
        with pytest.raises(SandboxError):
            sandbox.read_file("/etc/hostname")
        code, output, timed_out = sandbox.bash("sleep 10", timeout=1)
        assert timed_out and code == 124
        code, output, _ = sandbox.bash("ls /reference /tests_data 2>&1; ls / ")
        assert "No such file" in output and "tests_data" not in output.split("\n")[-2]
        assert sandbox.copy_out("sub/dir/x.txt", tmp_path / "x.txt") and (tmp_path / "x.txt").read_text() == "hello\n"
        assert not sandbox.copy_out("missing.py", tmp_path / "m.py")
    gone = subprocess.run(["docker", "inspect", sandbox.name], capture_output=True)
    assert gone.returncode != 0     # container removed


@needs_docker
@pytest.mark.docker
def test_end_to_end_scripted_run(tmp_path):
    from harness.run_eval import load_config, run_one

    config = load_config("harness/models.yaml")
    model = next(m for m in config["models"] if m["name"] == "scripted-reference")
    scores = run_one(model, "canonical", 1, config["defaults"], results_dir=tmp_path, log=lambda *_: None)
    run_dir = tmp_path / "scripted-reference__canonical__r1"
    for name in ("transcript.jsonl", "emulator.py", "scores.json", "outputs.json", "meta.json"):
        assert (run_dir / name).exists(), name
    assert scores["overall"] == 1.0
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["episode"]["end_reason"] == "submitted"


def test_client_retries_are_logged_in_the_episode_transcript(tmp_path):
    """Regression: the client's log callback must write into the live episode transcript."""
    from harness.agent_loop import Transcript

    responses = [error_response(429, "slow down", headers={"retry-after": "0"}),
                 chat_tool_call([("submit", {})])]
    with MockServer(responses) as server:
        transcript = Transcript(tmp_path / "t.jsonl")
        client = OpenAIChatClient("m", server.url, "k", log=transcript.log)
        summary = run_episode(client, FakeSandbox(), "x", transcript=transcript)
        transcript.close()
    assert summary["end_reason"] == "submitted"
    kinds = [json.loads(line)["type"] for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert kinds.count("retry") == 1 and kinds[0] == "start" and kinds[-1] == "end"
