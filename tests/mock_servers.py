"""Local mock model APIs for testing the provider clients without network or keys."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockServer:
    """Serves scripted responses in order; records every request body.

    ``responses`` is a list of callables ``(request_json) -> (status, headers, body_bytes)``.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                server.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                responder = server.responses.pop(0) if server.responses else error_response(500, "no more responses")
                status, headers, payload = responder(body)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def json_response(obj, status=200, headers=None):
    return lambda _req: (status, {"Content-Type": "application/json", **(headers or {})}, json.dumps(obj).encode())


def error_response(status, message, headers=None):
    return json_response({"error": {"message": message}}, status=status, headers=headers)


# ----------------------------------------------------------------------------- OpenAI chat

def chat_tool_call(calls, content="", usage=(100, 20), finish="tool_calls"):
    message = {"role": "assistant", "content": content,
               "tool_calls": [{"id": f"call_{i}", "type": "function",
                               "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
                              for i, (name, args) in enumerate(calls)]}
    if not calls:
        message.pop("tool_calls")
    return json_response({"id": "chatcmpl-1", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                          "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                                    "prompt_tokens_details": {"cached_tokens": usage[0] // 2}}})


# ----------------------------------------------------------------------------- OpenAI responses

def responses_output(items, usage=(100, 20), status="completed"):
    return json_response({"id": "resp_1", "status": status, "output": items,
                          "usage": {"input_tokens": usage[0], "output_tokens": usage[1],
                                    "input_tokens_details": {"cached_tokens": 10},
                                    "output_tokens_details": {"reasoning_tokens": 5}}})


# ----------------------------------------------------------------------------- Anthropic (SSE)

def anthropic_stream(blocks, stop_reason="tool_use", usage=None):
    """Build an SSE stream for messages.stream(). ``blocks`` are complete content blocks."""
    usage = usage or {"input_tokens": 50, "output_tokens": 1, "cache_read_input_tokens": 30,
                      "cache_creation_input_tokens": 20}
    events = [("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": usage}})]
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            events.append(("content_block_start", {"type": "content_block_start", "index": index,
                                                   "content_block": {"type": "text", "text": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": index,
                                                   "delta": {"type": "text_delta", "text": block["text"]}}))
        elif block["type"] == "thinking":
            events.append(("content_block_start", {"type": "content_block_start", "index": index,
                                                   "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": index,
                                                   "delta": {"type": "thinking_delta", "thinking": block["thinking"]}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": index,
                                                   "delta": {"type": "signature_delta", "signature": block["signature"]}}))
        elif block["type"] == "tool_use":
            events.append(("content_block_start", {"type": "content_block_start", "index": index,
                                                   "content_block": {"type": "tool_use", "id": block["id"],
                                                                     "name": block["name"], "input": {}}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": index,
                                                   "delta": {"type": "input_json_delta",
                                                             "partial_json": json.dumps(block["input"])}}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
    events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                     "usage": {"output_tokens": 42}}))
    events.append(("message_stop", {"type": "message_stop"}))
    body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()
    return lambda _req: (200, {"Content-Type": "text/event-stream"}, body)
