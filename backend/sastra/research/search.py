"""Search provider adapters (authorized APIs only — no scraping of search engines)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: str

    def to_dict(self) -> dict:
        return asdict(self)


class SearchError(Exception):
    pass


def _get_json(url: str, headers: dict, timeout: int = 20, data: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Accept": "application/json", **headers,
                                                         **({"Content-Type": "application/json"} if body else {})},
                                 method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - owner-configured endpoint
            return json.loads(resp.read(5_000_000))
    except urllib.error.HTTPError as e:
        raise SearchError(f"HTTP {e.code} from search provider") from e
    except Exception as e:  # noqa: BLE001
        raise SearchError(f"Search failed: {e}") from e


class SearchProvider(ABC):
    name = "base"

    @abstractmethod
    def search(self, query: str, count: int = 8, freshness_days: int | None = None) -> list[SearchResult]: ...


class BraveSearch(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query, count=8, freshness_days=None):
        params = {"q": query, "count": min(count, 20)}
        if freshness_days:
            params["freshness"] = "pd" if freshness_days <= 1 else "pw" if freshness_days <= 7 else "pm" if freshness_days <= 31 else "py"
        data = _get_json("https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params),
                         {"X-Subscription-Token": self.api_key})
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("description", ""), self.name)
                for r in (data.get("web") or {}).get("results", [])]


class TavilySearch(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query, count=8, freshness_days=None):
        body = {"query": query, "max_results": min(count, 20), "search_depth": "basic"}
        if freshness_days:
            body["days"] = freshness_days
        data = _get_json("https://api.tavily.com/search", {"Authorization": f"Bearer {self.api_key}"}, data=body)
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", "")[:400], self.name)
                for r in data.get("results", [])]


class SearxngSearch(SearchProvider):
    """Self-hosted SearXNG with JSON output enabled (free, private)."""
    name = "searxng"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def search(self, query, count=8, freshness_days=None):
        params = {"q": query, "format": "json"}
        if freshness_days:
            params["time_range"] = "day" if freshness_days <= 1 else "week" if freshness_days <= 7 else "month" if freshness_days <= 31 else "year"
        data = _get_json(self.base_url + "/search?" + urllib.parse.urlencode(params), {})
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", ""), self.name)
                for r in data.get("results", [])[:count]]


class StaticSearch(SearchProvider):
    """Deterministic provider for tests and offline demos."""
    name = "static"

    def __init__(self, results: list[SearchResult]):
        self.results = results

    def search(self, query, count=8, freshness_days=None):
        return self.results[:count]


def build_search_providers(settings) -> list[SearchProvider]:
    out: list[SearchProvider] = []
    if settings.searxng_url:
        out.append(SearxngSearch(settings.searxng_url))
    if settings.brave_api_key:
        out.append(BraveSearch(settings.brave_api_key))
    if settings.tavily_api_key:
        out.append(TavilySearch(settings.tavily_api_key))
    return out
