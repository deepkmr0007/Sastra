"""Test helpers: isolated App instances and a fake OpenAI-compatible / Anthropic
HTTP server so the real HTTP adapter code paths are exercised."""
from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sastra.config import load_settings
from sastra.core import App
from sastra.security.crypto import generate_data_key


class FakeLLM:
    """Scripted model server. `replies` is a list of strings or callables(request_json)->str
    consumed in order (the last one repeats). `fail_first` makes the first N calls return 503."""

    def __init__(self, replies=None, fail_first: int = 0, usage=(100, 50)):
        self.replies = list(replies or ["Hello from the fake model."])
        self.fail_first = fail_first
        self.usage = usage
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                outer.requests.append({"path": self.path, "body": body, "headers": {k.lower(): v for k, v in self.headers.items()}})
                if outer.fail_first > 0:
                    outer.fail_first -= 1
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b'{"error":"overloaded"}')
                    return
                reply = outer.replies[0] if len(outer.replies) == 1 else outer.replies.pop(0)
                text = reply(body) if callable(reply) else reply
                if self.path.endswith("/embeddings"):
                    data = [{"embedding": [float(len(s) % 7), float(s.count("a")), 1.0]} for s in body["input"]]
                    raw = json.dumps({"data": data}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunks = [text[i:i + 20] for i in range(0, len(text), 20)] or [""]
                if self.path.endswith("/messages"):  # Anthropic format
                    self._sse({"type": "message_start", "message": {"usage": {"input_tokens": outer.usage[0]}}})
                    for c in chunks:
                        self._sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": c}})
                    self._sse({"type": "message_delta", "usage": {"output_tokens": outer.usage[1]}})
                    self._sse({"type": "message_stop"})
                else:
                    for c in chunks:
                        self._sse({"choices": [{"delta": {"content": c}}]})
                    self._sse({"choices": [], "usage": {"prompt_tokens": outer.usage[0], "completion_tokens": outer.usage[1]}})
                    self.wfile.write(b"data: [DONE]\n\n")

            def _sse(self, obj):
                self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def make_app(*, adapters=None, providers=None, fetcher=None, sandbox=None, with_key: bool = True, **env) -> tuple[App, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="sastra-test-"))
    import os
    old = dict(os.environ)
    try:
        os.environ["SASTRA_DATA_DIR"] = str(tmp)
        os.environ["SASTRA_JWT_SECRET"] = "test-secret-" + "x" * 40
        if with_key:
            os.environ["SASTRA_DATA_KEY"] = generate_data_key()
        else:
            os.environ.pop("SASTRA_DATA_KEY", None)
        for k, v in env.items():
            os.environ[f"SASTRA_{k}"] = str(v)
        settings = load_settings()
    finally:
        os.environ.clear()
        os.environ.update(old)
    app = App(settings, adapters=adapters if adapters is not None else [], search_providers=providers or [],
              fetcher=fetcher, sandbox=sandbox)
    return app, tmp


def openai_adapter(url: str, local: bool = False, model: str = "fake-model", embed: str | None = None):
    from sastra.models.adapters import OpenAICompatAdapter
    return OpenAICompatAdapter(url + "/v1", "test-key", model, local, timeout=10, embed_model=embed)


def priced(app: App, provider: str = "openai_compat", model: str = "fake-model", pin: float = 1.0, pout: float = 2.0):
    app.budget.set_price(f"{provider}:{model}", pin, pout)
