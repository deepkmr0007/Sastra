"""Model gateway: routing, consent, budgets, retries, fallback, accounting.

Routing order for a request:
  1. owner preference order (default: local → anthropic → openai_compat)
  2. drop providers that are unavailable, circuit-open, disabled by the cloud
     kill switch, or not consented for the request's data class
  3. pick the fast model for simple requests, the strong model otherwise
  4. pre-check the projected cost against the hard caps (skip if over)
  5. the offline adapter is always the final fallback
"""
from __future__ import annotations

import time
from typing import Iterator

from ..budget import Budget, BudgetExceeded, UnknownPrice
from ..controls import Controls
from ..db import KV
from ..metrics import Metrics
from ..privacy import PrivacyLedger
from .adapters import (Adapter, AnthropicAdapter, CircuitBreaker, ModelRequest, ModelResponse, OfflineAdapter,
                       OpenAICompatAdapter, ProviderError, estimate_tokens)

DEFAULT_ORDER = ["local", "anthropic", "openai_compat"]


class NoModelAvailable(Exception):
    pass


class ModelGateway:
    def __init__(self, adapters: list[Adapter], controls: Controls, budget: Budget, privacy: PrivacyLedger,
                 metrics: Metrics, kv: KV, max_attempts: int = 2):
        self.adapters = {a.name: a for a in adapters}
        if "offline" not in self.adapters:
            from ..tools.calculator import calculate
            self.adapters["offline"] = OfflineAdapter(calculate)
        self.controls, self.budget, self.privacy, self.metrics, self.kv = controls, budget, privacy, metrics, kv
        self.breaker = CircuitBreaker()
        self.max_attempts = max_attempts

    # ------------------------------------------------------------- routing
    @property
    def preference(self) -> list[str]:
        return self.kv.get("model_preference") or DEFAULT_ORDER

    def set_preference(self, order: list[str]) -> None:
        unknown = [n for n in order if n not in self.adapters]
        if unknown:
            raise ValueError(f"Unknown providers: {unknown}")
        self.kv.set("model_preference", [n for n in order if n != "offline"])

    def candidates(self, req: ModelRequest) -> tuple[list[tuple[Adapter, str]], list[str]]:
        notes: list[str] = []
        out: list[tuple[Adapter, str]] = []
        cloud_ok = bool(self.controls.get("cloud_models_enabled"))
        for name in self.preference:
            a = self.adapters.get(name)
            if not a or not a.available():
                continue
            if not a.is_local and not cloud_ok:
                notes.append(f"{name}: cloud models switched off")
                continue
            consent_key = "local" if a.is_local else name
            if not self.controls.provider_allows(consent_key, req.data_class):
                notes.append(f"{name}: not allowed to receive '{req.data_class}' data")
                continue
            if not self.breaker.allow(name):
                notes.append(f"{name}: temporarily skipped after repeated failures")
                continue
            out.append((a, a.model_for(req.complexity)))
        out.append((self.adapters["offline"], "sastra-offline-rules"))
        return out, notes

    def _projected_cost(self, a: Adapter, model: str, req: ModelRequest) -> float:
        prompt = req.system + "".join(m.content for m in req.messages)
        return self.budget.price_tokens(a.name, model, estimate_tokens(prompt), req.max_tokens, a.is_local)

    # -------------------------------------------------------------- calls
    def stream(self, req: ModelRequest) -> Iterator[str | ModelResponse]:
        """Yield text chunks, then exactly one ModelResponse with accounting."""
        cands, notes = self.candidates(req)
        last_err = None
        for adapter, model in cands:
            try:
                projected = self._projected_cost(adapter, model, req)
                self.budget.check(projected)
            except (BudgetExceeded, UnknownPrice) as e:
                notes.append(f"{adapter.name}: {e}")
                continue
            for attempt in range(1, self.max_attempts + 1):
                started = time.perf_counter()
                ttft = None
                chunks: list[str] = []
                usage: dict = {}
                emitted = False
                try:
                    for piece in adapter.stream(req, model):
                        if isinstance(piece, dict):
                            usage = piece
                            continue
                        if ttft is None:
                            ttft = int((time.perf_counter() - started) * 1000)
                        chunks.append(piece)
                        emitted = True
                        yield piece
                except ProviderError as e:
                    self.breaker.failure(adapter.name)
                    last_err = e
                    notes.append(f"{adapter.name} attempt {attempt}: {e}")
                    if emitted or not e.retryable:
                        break  # never retry a half-streamed answer
                    time.sleep(min(8.0, 0.5 * 2 ** (attempt - 1)))
                    continue
                self.breaker.success(adapter.name)
                text = "".join(chunks)
                tin = usage.get("tokens_in", 0)
                tout = usage.get("tokens_out", estimate_tokens(text))
                cost = self.budget.price_tokens(adapter.name, model, tin, tout, adapter.is_local)
                latency = int((time.perf_counter() - started) * 1000)
                if adapter.name != "offline":
                    self.budget.record(adapter.name, model, req.purpose, cost, tin, tout, req.task_id, req.project_id)
                    sent = len((req.system + "".join(m.content for m in req.messages)).encode())
                    self.privacy.record(adapter.name, "local" if adapter.is_local else adapter.name, req.data_class,
                                        sent, req.purpose, req.task_id)
                self.metrics.observe("model.latency_ms", latency, provider=adapter.name)
                if ttft is not None:
                    self.metrics.observe("model.ttft_ms", ttft, provider=adapter.name)
                yield ModelResponse(text=text, provider=adapter.name, model=model, tokens_in=tin, tokens_out=tout,
                                    cost_usd=cost, latency_ms=latency, ttft_ms=ttft, notes=notes)
                return
        raise NoModelAvailable(f"No provider could answer: {last_err}")  # pragma: no cover (offline always answers)

    def complete(self, req: ModelRequest) -> ModelResponse:
        final = None
        for piece in self.stream(req):
            if isinstance(piece, ModelResponse):
                final = piece
        assert final is not None
        return final

    def has_reasoning_model(self, data_class: str = "normal") -> bool:
        cands, _ = self.candidates(ModelRequest(messages=[], data_class=data_class))
        return any(a.name != "offline" for a, _ in cands)

    def embed(self, texts: list[str], data_class: str = "private") -> list[list[float]] | None:
        a = self.adapters.get("local") or self.adapters.get("openai_compat")
        if not isinstance(a, OpenAICompatAdapter) or not a.embed_model:
            return None
        consent = "local" if a.is_local else a.name
        if not self.controls.provider_allows(consent, data_class):
            return None
        try:
            vecs = a.embed(texts)
        except ProviderError:
            return None
        self.privacy.record(a.name, consent, data_class, sum(len(t) for t in texts), "embedding")
        return vecs

    def health(self) -> list[dict]:
        out = []
        for name, a in self.adapters.items():
            d = a.describe()
            d["circuit"] = self.breaker.state(name)
            d["consent"] = (self.controls.get("provider_consent") or {}).get("local" if a.is_local and name != "offline" else name)
            out.append(d)
        return out


def build_adapters(settings, calculator) -> list[Adapter]:
    adapters: list[Adapter] = [OfflineAdapter(calculator)]
    if settings.anthropic_api_key:
        adapters.append(AnthropicAdapter(settings.anthropic_api_key, settings.anthropic_model, settings.anthropic_fast_model))
    if settings.openai_compat_base_url and settings.openai_compat_model:
        adapters.append(OpenAICompatAdapter(settings.openai_compat_base_url, settings.openai_compat_api_key,
                                            settings.openai_compat_model, settings.openai_compat_is_local,
                                            embed_model=getattr(settings, "embed_model", None)))
    return adapters
