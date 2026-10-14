"""Text extraction and claim mining from HTML, PDF and plain text."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe", "button"}
_BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "td", "blockquote"}


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in _SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"]
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


@dataclass
class Extracted:
    title: str
    text: str
    published: str | None = None
    kind: str = "html"
    warnings: list[str] = field(default_factory=list)


def extract(body: bytes, content_type: str, url: str = "") -> Extracted:
    ct = (content_type or "").lower()
    if "pdf" in ct or url.lower().endswith(".pdf") or body[:5] == b"%PDF-":
        return _pdf(body)
    if "html" in ct or b"<html" in body[:2000].lower():
        p = _HTMLText()
        p.feed(body.decode("utf-8", "replace"))
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        pub = next((p.meta[k] for k in ("article:published_time", "datepublished", "date", "dc.date", "pubdate",
                                        "article:modified_time") if k in p.meta), None)
        return Extracted(title=p.title.strip()[:300], text=text, published=pub)
    return Extracted(title=url.rsplit("/", 1)[-1][:200], text=body.decode("utf-8", "replace"), kind="text")


def _pdf(body: bytes) -> Extracted:
    try:
        from pypdf import PdfReader
    except ImportError:
        return Extracted(title="PDF", text="", kind="pdf", warnings=["pypdf not installed — PDF text unavailable"])
    try:
        reader = PdfReader(io.BytesIO(body))
        pages = [(pg.extract_text() or "") for pg in reader.pages[:200]]
        meta = reader.metadata or {}
        title = (getattr(meta, "title", None) or "PDF document")
        created = getattr(meta, "creation_date", None)
        text = "\n\n".join(pages).strip()
        warns = [] if text else ["No extractable text (scanned PDF? OCR not run)"]
        return Extracted(title=str(title)[:300], text=text, published=str(created) if created else None, kind="pdf", warnings=warns)
    except Exception as e:  # noqa: BLE001
        return Extracted(title="PDF", text="", kind="pdf", warnings=[f"PDF parse error: {e}"])


_SENT = re.compile(r"(?<=[.!?।])\s+(?=[A-Z0-9\"'(ऀ-ॿ])")
_NUM = re.compile(r"\d")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_ESTIMATE = re.compile(r"(?i)\b(estimat\w*|approximately|about|around|roughly|projected|forecast\w*|expected to|could|may|might)\b")
_INTERP = re.compile(r"(?i)\b(suggest\w*|indicat\w*|likely|argu\w*|believ\w*|appears?|seems?|implies|according to)\b")
_HYPO = re.compile(r"(?i)\b(hypothes\w*|if\b.*\bthen|we propose|unproven|speculat\w*)\b")
_METHOD = re.compile(r"(?i)\b(survey\w*|sample\w*|randomi[sz]ed|study|trial|dataset|methodolog\w*|measured|n\s*=\s*\d+)\b")


@dataclass
class Claim:
    text: str
    kind: str  # statistic | date | estimate | interpretation | hypothesis | methodology | statement
    numbers: list[str]


def split_sentences(text: str) -> list[str]:
    out = []
    for para in text.split("\n"):
        para = para.strip()
        if len(para) < 30:
            continue
        out.extend(s.strip() for s in _SENT.split(para) if 30 <= len(s.strip()) <= 600)
    return out


def classify(sentence: str) -> str:
    if _HYPO.search(sentence):
        return "hypothesis"
    if _ESTIMATE.search(sentence) and _NUM.search(sentence):
        return "estimate"
    if re.search(r"\d+(\.\d+)?\s*(%|percent|per cent|crore|lakh|million|billion|thousand|gw|mw|kwh|km|kg|tonnes?|₹|\$|rs\.?|usd|inr)", sentence, re.I):
        return "statistic"
    if _METHOD.search(sentence):
        return "methodology"
    if _INTERP.search(sentence):
        return "interpretation"
    if _YEAR.search(sentence):
        return "date"
    return "statement"


def mine_claims(text: str, query_terms: set[str], limit: int = 25) -> list[Claim]:
    scored = []
    for s in split_sentences(text):
        words = set(re.findall(r"\w+", s.lower()))
        overlap = len(words & query_terms)
        informative = bool(_NUM.search(s)) + bool(_METHOD.search(s))
        score = overlap * 2 + informative
        if score > 0:
            nums = re.findall(r"\d[\d,]*(?:\.\d+)?", s)
            scored.append((score, Claim(s, classify(s), nums)))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]
