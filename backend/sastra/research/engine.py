"""Research & evidence engine: search → fetch → extract → compare → cited report.

Guarantees:
  * every citation in a report refers to a source that was actually fetched;
    citations to anything else are stripped and flagged (no fabricated cites)
  * each source records title, URL, access timestamp, content hash and a short
    supporting passage
  * claims are labelled (statistic / estimate / interpretation / hypothesis …)
  * numeric disagreements between independent sources are surfaced as conflicts
  * all fetched content is treated as untrusted data (wrapped + scanned)
"""
from __future__ import annotations

import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from ..budget import Budget, BudgetExceeded
from ..controls import Controls
from ..db import Database, dumps, new_id, now
from ..models.adapters import ChatMessage, ModelRequest
from ..models.gateway import ModelGateway
from ..privacy import PrivacyLedger
from ..security.crypto import sha256_hex
from ..security.netguard import FetchResult, NetworkPolicyError
from ..security.untrusted import UNTRUSTED_POLICY, scan_injection, wrap_untrusted
from .extract import Claim, extract, mine_claims
from .search import SearchError, SearchProvider, SearchResult

_PRIMARY_HINTS = (".gov", ".gov.in", ".nic.in", ".edu", ".ac.in", ".int", "who.int", "rbi.org.in", "sebi.gov.in",
                  "data.gov", "europa.eu", "un.org", "worldbank.org", "imf.org", "nature.com", "science.org",
                  "arxiv.org", "nih.gov", "doi.org", "github.com", "docs.")
_LOW_QUALITY = ("pinterest.", "quora.com", "facebook.com", "instagram.com", "tiktok.com")


class ResearchUnavailable(Exception):
    pass


@dataclass
class Source:
    index: int
    url: str
    title: str
    accessed_at: float
    content_hash: str
    published: str | None
    kind: str
    primary: bool
    injection_score: float
    claims: list[Claim] = field(default_factory=list)
    text: str = ""
    warnings: list[str] = field(default_factory=list)

    def public(self) -> dict:
        support = self.claims[0].text if self.claims else self.text[:200]
        words = support.split()
        return {"index": self.index, "url": self.url, "title": self.title or self.url,
                "accessed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.accessed_at)),
                "content_hash": self.content_hash, "published": self.published, "kind": self.kind,
                "primary_source": self.primary, "injection_risk": round(self.injection_score, 2),
                "supporting_passage": " ".join(words[:30]) + (" …" if len(words) > 30 else ""),
                "claims": [{"text": c.text, "kind": c.kind} for c in self.claims[:8]], "warnings": self.warnings}


def _domain(url: str) -> str:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-3:]) if len(parts) > 2 and parts[-2] in ("gov", "co", "ac", "nic", "org") else ".".join(parts[-2:])


def _values(numbers: list[str]) -> set[str]:
    """Numbers that carry the claim's value (years are context, not values)."""
    return {n.replace(",", "") for n in numbers if not re.fullmatch(r"(19|20)\d{2}", n.replace(",", ""))}


def _is_primary(url: str) -> bool:
    u = url.lower()
    return any(h in u for h in _PRIMARY_HINTS)


class ResearchEngine:
    def __init__(self, db: Database, gateway: ModelGateway, providers: list[SearchProvider],
                 fetcher: Callable[[str], FetchResult], controls: Controls, budget: Budget, privacy: PrivacyLedger):
        self.db, self.gateway, self.providers, self.fetcher = db, gateway, providers, fetcher
        self.controls, self.budget, self.privacy = controls, budget, privacy

    # -------------------------------------------------------------- search
    def search(self, query: str, count: int = 8, freshness_days: int | None = None, task_id: str | None = None) -> tuple[list[SearchResult], list[str]]:
        if not self.controls.get("internet_enabled"):
            raise ResearchUnavailable("Internet access is switched off (Security → Internet). Offline alternative: "
                                      "ask me to answer from saved memory and your project files, or paste the sources.")
        notes: list[str] = []
        for p in self.providers:
            price = self.budget.request_pricing.get(p.name, 0.0)
            try:
                self.budget.check(price)
            except BudgetExceeded as e:
                notes.append(f"{p.name}: {e}")
                continue
            try:
                res = p.search(query, count, freshness_days)
            except SearchError as e:
                notes.append(f"{p.name}: {e}")
                continue
            if price:
                self.budget.record(p.name, "search", "search", price, task_id=task_id)
            self.privacy.record(p.name, p.name, "normal", len(query.encode()), "web search", task_id)
            if res:
                return res, notes
        if not self.providers:
            notes.append("No search provider configured (set SASTRA_SEARXNG_URL, SASTRA_BRAVE_API_KEY or SASTRA_TAVILY_API_KEY). "
                         "You can still research by supplying URLs.")
        return [], notes

    def _select(self, results: list[SearchResult], max_sources: int) -> list[str]:
        seen_domains: dict[str, int] = {}
        ranked = sorted(results, key=lambda r: (not _is_primary(r.url), any(b in r.url for b in _LOW_QUALITY)))
        urls = []
        for r in ranked:
            if not r.url.startswith(("http://", "https://")) or any(b in r.url for b in _LOW_QUALITY):
                continue
            d = _domain(r.url)
            if seen_domains.get(d, 0) >= 1:  # independence: one page per site
                continue
            seen_domains[d] = seen_domains.get(d, 0) + 1
            urls.append(r.url)
            if len(urls) >= max_sources:
                break
        return urls

    # --------------------------------------------------------------- fetch
    def gather(self, question: str, urls: list[str], task_id: str | None = None) -> tuple[list[Source], list[str]]:
        q_terms = set(re.findall(r"\w+", question.lower())) - {"the", "a", "of", "and", "in", "is", "what", "how", "to", "for"}
        sources: list[Source] = []
        notes: list[str] = []
        for url in urls:
            try:
                fr = self.fetcher(url)
            except NetworkPolicyError as e:
                notes.append(f"Skipped {url}: {e}")
                continue
            except Exception as e:  # noqa: BLE001
                notes.append(f"Could not fetch {url}: {e}")
                continue
            self.privacy.record(urllib.parse.urlparse(url).hostname or url, "web", "public", len(url), "fetch source", task_id)
            if fr.status >= 400:
                notes.append(f"{url} returned HTTP {fr.status} (not used)")
                continue
            ex = extract(fr.body, fr.content_type, fr.final_url)
            if len(ex.text) < 200:
                notes.append(f"{url}: too little readable text (login wall, paywall or script-only page?) — not used")
                continue
            inj = scan_injection(ex.text[:50000])
            src = Source(index=len(sources) + 1, url=fr.final_url, title=ex.title, accessed_at=now(),
                         content_hash=sha256_hex(fr.body), published=ex.published, kind=ex.kind,
                         primary=_is_primary(fr.final_url), injection_score=inj.score,
                         claims=mine_claims(ex.text, q_terms), text=ex.text[:60000],
                         warnings=ex.warnings + (["Page contains instruction-like text; treated strictly as data"] if inj.suspicious else []) +
                                  (["Content truncated at size limit"] if fr.truncated else []))
            sources.append(src)
        return sources, notes

    # ------------------------------------------------------------- compare
    @staticmethod
    def conflicts(sources: list[Source]) -> list[dict]:
        out = []
        stat_claims = [(s.index, c) for s in sources for c in s.claims if _values(c.numbers)]
        for i in range(len(stat_claims)):
            for j in range(i + 1, len(stat_claims)):
                (si, a), (sj, b) = stat_claims[i], stat_claims[j]
                if si == sj:
                    continue
                wa = set(re.findall(r"[a-z]{4,}", a.text.lower()))
                wb = set(re.findall(r"[a-z]{4,}", b.text.lower()))
                na, nb = _values(a.numbers), _values(b.numbers)
                if len(wa & wb) >= 4 and na and nb and not (na & nb):
                    out.append({"sources": [si, sj], "a": a.text[:300], "b": b.text[:300],
                                "note": "Similar claims with different figures — check definitions, dates and units."})
                    if len(out) >= 8:
                        return out
        return out

    # -------------------------------------------------------------- report
    def run(self, question: str, *, urls: list[str] | None = None, max_sources: int = 6, freshness_days: int | None = None,
            project_id: str | None = None, task_id: str | None = None) -> dict:
        started = time.perf_counter()
        notes: list[str] = []
        if not self.controls.get("internet_enabled"):
            raise ResearchUnavailable("Internet access is switched off. Offline alternative: answer from saved memory/files, "
                                      "or paste source text into the chat.")
        chosen = list(dict.fromkeys(urls or []))
        if len(chosen) < max_sources:
            results, n = self.search(question, count=max_sources * 2, freshness_days=freshness_days, task_id=task_id)
            notes += n
            for u in self._select(results, max_sources):
                if u not in chosen:
                    chosen.append(u)
        sources, n = self.gather(question, chosen[:max_sources], task_id)
        notes += n
        conflicts = self.conflicts(sources)
        if freshness_days:
            for s in sources:
                if not s.published:
                    s.warnings.append("Publication date unknown — freshness not verified")
        if not sources:
            report = (f"# Research: {question}\n\nNo usable sources could be retrieved, so no conclusions are drawn.\n\n"
                      "## What happened\n" + "\n".join(f"- {x}" for x in notes or ["No search results."]))
            synth_mode = "none"
        elif self.gateway.has_reasoning_model("normal"):
            report, extra = self._synthesize(question, sources, conflicts, task_id, project_id)
            notes += extra
            synth_mode = "model"
        else:
            report = self._extractive(question, sources, conflicts)
            synth_mode = "extractive"
        report += self._sources_section(sources)
        if notes:
            report += "\n\n## Process notes\n" + "\n".join(f"- {x}" for x in notes)
        rid = new_id()
        pub_sources = [s.public() for s in sources]
        self.db.insert("research_reports", {"id": rid, "project_id": project_id, "task_id": task_id, "question": question,
                                            "report_md": report, "sources": dumps(pub_sources), "created_at": now()})
        return {"id": rid, "question": question, "report_md": report, "sources": pub_sources, "conflicts": conflicts,
                "synthesis": synth_mode, "notes": notes, "duration_ms": int((time.perf_counter() - started) * 1000)}

    def _synthesize(self, question: str, sources: list[Source], conflicts: list[dict], task_id, project_id) -> tuple[str, list[str]]:
        blocks = []
        for s in sources:
            excerpt = "\n".join(f"- ({c.kind}) {c.text}" for c in s.claims[:12]) or s.text[:3000]
            blocks.append(f"[{s.index}] {s.title} — {s.url} (published: {s.published or 'unknown'}; primary: {s.primary})\n" +
                          wrap_untrusted(excerpt, source=s.url, max_chars=6000))
        system = (UNTRUSTED_POLICY + "\nYou are a careful research analyst. Write a report in Markdown with sections: "
                  "## Answer, ## Key findings, ## Where sources disagree, ## Gaps and uncertainty, ## Freshness. "
                  "Every factual sentence must cite sources as [n] using ONLY the numbers provided. "
                  "Label each key finding as (established), (estimate), (interpretation) or (hypothesis). "
                  "Prefer primary sources. If evidence is weak or missing say so plainly. Do not invent sources or facts.")
        conflict_txt = "\n".join(f"- sources {c['sources']}: {c['note']}" for c in conflicts) or "none detected automatically"
        user = f"Question: {question}\n\nSources:\n\n" + "\n\n".join(blocks) + f"\n\nAutomatically detected numeric conflicts:\n{conflict_txt}"
        try:
            resp = self.gateway.complete(ModelRequest(messages=[ChatMessage("user", user)], system=system, max_tokens=1800,
                                                      complexity="hard", purpose="research", task_id=task_id,
                                                      project_id=project_id, data_class="normal"))
        except Exception as e:  # noqa: BLE001
            return self._extractive(question, sources, conflicts), [f"Model synthesis failed ({e}); extractive report used"]
        if resp.provider == "offline":
            return self._extractive(question, sources, conflicts), ["No reasoning model available; extractive report used"]
        text, problems = self.validate_citations(resp.text, len(sources))
        return f"# Research: {question}\n\n" + text, problems + [f"Synthesised by {resp.provider}/{resp.model}"]

    @staticmethod
    def validate_citations(text: str, n_sources: int) -> tuple[str, list[str]]:
        problems = []
        valid = set(range(1, n_sources + 1))

        def fix(m: re.Match) -> str:
            nums = [int(x) for x in re.findall(r"\d+", m.group(0))]
            good = [x for x in nums if x in valid]
            bad = [x for x in nums if x not in valid]
            if bad:
                problems.append(f"Removed citation(s) to non-existent source(s): {bad}")
            return "[" + ", ".join(map(str, good)) + "]" if good else "[citation removed]"

        text = re.sub(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]", fix, text)
        findings = re.search(r"## Key findings(.*?)(\n## |\Z)", text, re.S)
        if findings:
            uncited = [ln for ln in findings.group(1).splitlines() if ln.strip().startswith(("-", "*", "1", "2", "3")) and not re.search(r"\[\d", ln)]
            if uncited:
                problems.append(f"{len(uncited)} key finding(s) without a citation — treat as unsupported")
        return text, problems

    @staticmethod
    def _extractive(question: str, sources: list[Source], conflicts: list[dict]) -> str:
        lines = [f"# Research: {question}", "",
                 "_No reasoning model was used: below are the most relevant passages found, grouped by type. "
                 "They are quotations of evidence, not conclusions._", "", "## Key evidence"]
        for kind in ("statistic", "estimate", "date", "methodology", "statement", "interpretation", "hypothesis"):
            items = [(s.index, c) for s in sources for c in s.claims if c.kind == kind][:6]
            if items:
                lines.append(f"\n**{kind.capitalize()}**")
                lines += [f"- {c.text} [{i}]" for i, c in items]
        lines.append("\n## Where sources disagree")
        lines += [f"- Sources {c['sources']}: “{c['a'][:160]}” vs “{c['b'][:160]}” — {c['note']}" for c in conflicts] or ["- No numeric conflicts detected automatically."]
        lines.append("\n## Gaps and uncertainty\n- Automatic extraction cannot judge source quality or context; verify key figures in the primary source.")
        return "\n".join(lines)

    @staticmethod
    def _sources_section(sources: list[Source]) -> str:
        if not sources:
            return ""
        out = ["\n\n## Sources"]
        for s in sources:
            p = s.public()
            tag = " · primary" if s.primary else ""
            out.append(f"{s.index}. [{p['title']}]({s.url}) — accessed {p['accessed_at']}{tag}"
                       + (f" · published {s.published}" if s.published else "")
                       + f"\n   > {p['supporting_passage']}")
        return "\n".join(out)

    def list_reports(self, project_id: str | None = None, limit: int = 50) -> list[dict]:
        if project_id:
            return self.db.query("SELECT id, question, created_at, project_id FROM research_reports WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit))
        return self.db.query("SELECT id, question, created_at, project_id FROM research_reports ORDER BY created_at DESC LIMIT ?", (limit,))
