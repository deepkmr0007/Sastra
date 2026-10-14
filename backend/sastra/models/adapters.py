"""Model provider adapters. Provider-specific logic stays here; everything else
talks to ``ModelGateway``. Only documented APIs and local runtimes are used.
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Iterator


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ModelRequest:
    messages: list[ChatMessage]
    system: str = ""
    max_tokens: int = 1024
    temperature: float = 0.3
    data_class: str = "normal"
    complexity: str = "normal"  # simple | normal | hard
    purpose: str = "chat"
    task_id: str | None = None
    project_id: str | None = None
    json_mode: bool = False


@dataclass
class ModelResponse:
    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    ttft_ms: int | None = None
    notes: list[str] = field(default_factory=list)


class ProviderError(Exception):
    def __init__(self, msg: str, retryable: bool = False):
        super().__init__(msg)
        self.retryable = retryable


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _post_json(url: str, headers: dict, body: dict, timeout: int, stream: bool = False):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - URL comes from owner config, not users
    except urllib.error.HTTPError as e:
        detail = e.read(2000).decode("utf-8", "replace")
        retry = e.code in (408, 409, 425, 429, 500, 502, 503, 504, 529)
        raise ProviderError(f"HTTP {e.code}: {detail[:300]}", retryable=retry) from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise ProviderError(f"Network error: {e}", retryable=True) from e


def _sse_lines(resp) -> Iterator[str]:
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if line.startswith("data:"):
            yield line[5:].strip()


class Adapter(ABC):
    name: str = "base"
    is_local: bool = False

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def model_for(self, complexity: str) -> str: ...

    @abstractmethod
    def stream(self, req: ModelRequest, model: str) -> Iterator[str | dict]:
        """Yield text chunks; finally yield {"tokens_in": n, "tokens_out": m}."""

    def complete(self, req: ModelRequest, model: str) -> tuple[str, int, int]:
        parts, usage = [], {}
        for chunk in self.stream(req, model):
            if isinstance(chunk, dict):
                usage = chunk
            else:
                parts.append(chunk)
        text = "".join(parts)
        return text, usage.get("tokens_in", 0), usage.get("tokens_out", estimate_tokens(text))

    def describe(self) -> dict:
        return {"name": self.name, "local": self.is_local, "available": self.available()}


# --------------------------------------------------------------------- offline
class OfflineAdapter(Adapter):
    """Always-available fallback. It is NOT a language model: it answers what it can
    deterministically (arithmetic, retrieved memory) and says plainly what it cannot."""

    name = "offline"
    is_local = True

    def __init__(self, calculator: Callable[[str], str] | None = None):
        self.calculator = calculator

    def available(self) -> bool:
        return True

    def model_for(self, complexity: str) -> str:
        return "sastra-offline-rules"

    def stream(self, req: ModelRequest, model: str) -> Iterator[str | dict]:
        last = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
        text = self.answer(last, req.system)
        for i in range(0, len(text), 64):
            yield text[i:i + 64]
        yield {"tokens_in": 0, "tokens_out": 0}

    def answer(self, question: str, system: str) -> str:
        q = question.strip()
        expr = re.sub(r"(?i)^(what is|what's|calculate|compute|=)\s*", "", q).rstrip("?= ")
        if self.calculator and re.fullmatch(r"[\d\s.+\-*/()%^,eπpisqrtlogcosintanabsroundfloorceil]+", expr) and re.search(r"\d", expr):
            try:
                return f"{expr} = {self.calculator(expr)}\n\n(Computed locally — no model was used.)"
            except Exception:  # noqa: BLE001
                pass
        memory = ""
        m = re.search(r"<relevant_memory>(.*?)</relevant_memory>", system, re.S)
        if m and m.group(1).strip():
            memory = "\n\nHere is what I have saved that looks relevant:\n" + m.group(1).strip()
        return ("I'm in offline mode: no language model is reachable or allowed right now, so I can't reason "
                "about this request properly. I can still do arithmetic, search your saved memory, and queue the "
                "request as a task to run when a model is available." + memory)


# ---------------------------------------------------------- OpenAI-compatible
class OpenAICompatAdapter(Adapter):
    """OpenAI, OpenRouter, Groq, Together, Ollama (/v1), llama.cpp server, LM Studio…"""

    name = "openai_compat"

    def __init__(self, base_url: str | None, api_key: str | None, model: str | None, is_local: bool,
                 fast_model: str | None = None, timeout: int = 120, embed_model: str | None = None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fast_model = fast_model or model
        self.is_local = is_local
        self.timeout = timeout
        self.embed_model = embed_model
        if is_local:
            self.name = "local"

    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def model_for(self, complexity: str) -> str:
        return (self.fast_model if complexity == "simple" else self.model) or ""

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def stream(self, req: ModelRequest, model: str) -> Iterator[str | dict]:
        msgs = ([{"role": "system", "content": req.system}] if req.system else []) + \
               [{"role": m.role, "content": m.content} for m in req.messages]
        body = {"model": model, "messages": msgs, "max_tokens": req.max_tokens, "temperature": req.temperature,
                "stream": True, "stream_options": {"include_usage": True}}
        if req.json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = _post_json(self.base_url + "/chat/completions", self._headers(), body, self.timeout)
        usage = None
        out_chars = 0
        with resp:
            for data in _sse_lines(resp):
                if data == "[DONE]":
                    break
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if evt.get("usage"):
                    usage = evt["usage"]
                for ch in evt.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content")
                    if piece:
                        out_chars += len(piece)
                        yield piece
        prompt_chars = sum(len(m["content"]) for m in msgs)
        yield {"tokens_in": (usage or {}).get("prompt_tokens", prompt_chars // 4),
               "tokens_out": (usage or {}).get("completion_tokens", out_chars // 4)}

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not (self.available() and self.embed_model):
            return None
        resp = _post_json(self.base_url + "/embeddings", self._headers(), {"model": self.embed_model, "input": texts}, self.timeout)
        with resp:
            data = json.loads(resp.read())
        return [d["embedding"] for d in data["data"]]


# ------------------------------------------------------------------- Anthropic
class AnthropicAdapter(Adapter):
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str | None, model: str, fast_model: str, timeout: int = 120, url: str | None = None):
        self.api_key = api_key
        self.model = model
        self.fast_model = fast_model
        self.timeout = timeout
        self.url = url or self.URL

    def available(self) -> bool:
        return bool(self.api_key)

    def model_for(self, complexity: str) -> str:
        return self.fast_model if complexity == "simple" else self.model

    def stream(self, req: ModelRequest, model: str) -> Iterator[str | dict]:
        body = {"model": model, "max_tokens": req.max_tokens, "temperature": req.temperature, "stream": True,
                "messages": [{"role": m.role, "content": m.content} for m in req.messages]}
        if req.system:
            body["system"] = req.system
        headers = {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"}
        resp = _post_json(self.url, headers, body, self.timeout)
        tin = tout = 0
        with resp:
            for data in _sse_lines(resp):
                try:
                    evt = json.loads(data)
                except json.JSONDecodeError:
                    continue
                t = evt.get("type")
                if t == "message_start":
                    tin = evt["message"].get("usage", {}).get("input_tokens", 0)
                elif t == "content_block_delta" and evt.get("delta", {}).get("type") == "text_delta":
                    yield evt["delta"]["text"]
                elif t == "message_delta":
                    tout = evt.get("usage", {}).get("output_tokens", tout)
                elif t == "error":
                    raise ProviderError(str(evt.get("error")), retryable=evt.get("error", {}).get("type") == "overloaded_error")
        yield {"tokens_in": tin, "tokens_out": tout}


class CircuitBreaker:
    """Skip a provider for `cooldown` seconds after `threshold` consecutive failures."""

    def __init__(self, threshold: int = 3, cooldown: float = 60.0):
        self.threshold, self.cooldown = threshold, cooldown
        self.failures: dict[str, int] = {}
        self.open_until: dict[str, float] = {}

    def allow(self, name: str) -> bool:
        return time.time() >= self.open_until.get(name, 0)

    def success(self, name: str) -> None:
        self.failures[name] = 0

    def failure(self, name: str) -> None:
        self.failures[name] = self.failures.get(name, 0) + 1
        if self.failures[name] >= self.threshold:
            self.open_until[name] = time.time() + self.cooldown
            self.failures[name] = 0

    def state(self, name: str) -> str:
        return "open" if not self.allow(name) else "closed"
