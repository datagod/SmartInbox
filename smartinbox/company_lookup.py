"""Background web lookup for sender company country of origin.

Used to refine Foreign Middleman / Indian middleman tags when local
heuristics are incomplete. Lookups are cached in SQLite and run off the
main mail-processing path (async worker; HTTP work can also use a
thread/subprocess helper).
"""

from __future__ import annotations

import html
import re
import sqlite3
import time
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import httpx

from smartinbox.email_middleman import (
    _COUNTRY_META,
    country_flag_and_name,
    extract_sender_domain,
    is_free_mail_domain,
)

DEFAULT_LOOKUP_TIMEOUT = 12.0
DEFAULT_CACHE_TTL_SEC = 30 * 86400  # 30 days
DEFAULT_MAX_BYTES = 400_000
DEFAULT_USER_AGENT = (
    "SmartInbox/0.1 (+local company-origin lookup; polite bot)"
)

# Patterns that often appear on About / Contact pages.
_HQ_LINE_RE = re.compile(
    r"(?is)("
    r"headquarter(?:s|ed)?|"
    r"based\s+(?:out\s+)?(?:of|in)|"
    r"registered\s+office|"
    r"corporate\s+office|"
    r"global\s+office|"
    r"our\s+office(?:s)?\s+in|"
    r"locations?\s*[:—-]|"
    r"address\s*[:—-]"
    r").{0,160}"
)

_TAG_RE = re.compile(r"(?is)<script[^>]*>.*?</script>|<style[^>]*>.*?</style>|<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Country name / city phrases → ISO code (for page text).
_COUNTRY_TEXT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("IN", re.compile(r"\b(india|bangalore|bengaluru|hyderabad|chennai|pune|mumbai|noida|delhi|gurgaon|gurugram)\b", re.I)),
    ("PH", re.compile(r"\b(philippines|manila|cebu|makati)\b", re.I)),
    ("PK", re.compile(r"\b(pakistan|karachi|lahore|islamabad)\b", re.I)),
    ("BD", re.compile(r"\b(bangladesh|dhaka)\b", re.I)),
    ("LK", re.compile(r"\b(sri\s+lanka|colombo)\b", re.I)),
    ("US", re.compile(r"\b(united\s+states|u\.s\.a\.|\busa\b|new\s+york|new\s+jersey|california|texas|chicago|atlanta)\b", re.I)),
    ("GB", re.compile(r"\b(united\s+kingdom|\buk\b|london|manchester|birmingham)\b", re.I)),
    ("AU", re.compile(r"\b(australia|sydney|melbourne|brisbane)\b", re.I)),
    ("CA", re.compile(r"\b(canada|toronto|vancouver|ottawa|calgary|montreal|ontario)\b", re.I)),
    ("PL", re.compile(r"\b(poland|warsaw|krakow|kraków)\b", re.I)),
    ("RO", re.compile(r"\b(romania|bucharest|cluj)\b", re.I)),
    ("UA", re.compile(r"\b(ukraine|kyiv|kiev|lviv)\b", re.I)),
    ("MX", re.compile(r"\b(mexico|guadalajara|monterrey)\b", re.I)),
    ("BR", re.compile(r"\b(brazil|s[aã]o\s+paulo|rio\s+de\s+janeiro)\b", re.I)),
    ("SG", re.compile(r"\b(singapore)\b", re.I)),
    ("AE", re.compile(r"\b(uae|dubai|abu\s+dhabi|united\s+arab\s+emirates)\b", re.I)),
    ("DE", re.compile(r"\b(germany|berlin|munich|frankfurt)\b", re.I)),
    ("IE", re.compile(r"\b(ireland|dublin)\b", re.I)),
    ("VN", re.compile(r"\b(vietnam|hanoi|ho\s+chi\s+minh)\b", re.I)),
    ("CN", re.compile(r"\b(china|shanghai|beijing|shenzhen)\b", re.I)),
    ("ZA", re.compile(r"\b(south\s+africa|johannesburg|cape\s+town)\b", re.I)),
]


def init_company_lookup_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_lookups (
            cache_key TEXT PRIMARY KEY,
            domain TEXT,
            company_name TEXT,
            country_code TEXT,
            country_name TEXT,
            country_flag TEXT,
            source TEXT,
            evidence TEXT,
            confidence REAL NOT NULL DEFAULT 0,
            fetched_at REAL NOT NULL,
            raw_notes TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_lookups_domain ON company_lookups(domain)"
    )
    conn.commit()


def lookup_cache_key(*, domain: str | None, company_name: str | None) -> str:
    d = (domain or "").strip().lower()
    n = re.sub(r"\s+", " ", (company_name or "").strip().lower())
    if d and not is_free_mail_domain(d):
        return f"domain:{d}"
    if n:
        return f"name:{n[:120]}"
    return f"domain:{d}" if d else ""


def get_cached_lookup(
    conn: sqlite3.Connection,
    *,
    domain: str | None = None,
    company_name: str | None = None,
    ttl_sec: float = DEFAULT_CACHE_TTL_SEC,
) -> dict[str, Any] | None:
    key = lookup_cache_key(domain=domain, company_name=company_name)
    if not key:
        return None
    row = conn.execute(
        "SELECT * FROM company_lookups WHERE cache_key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    age = time.time() - float(data.get("fetched_at") or 0)
    if age > ttl_sec:
        return None
    return data


def save_lookup(
    conn: sqlite3.Connection,
    *,
    domain: str | None,
    company_name: str | None,
    country_code: str | None,
    source: str,
    evidence: str,
    confidence: float,
    raw_notes: str | None = None,
) -> dict[str, Any]:
    key = lookup_cache_key(domain=domain, company_name=company_name)
    if not key:
        raise ValueError("domain or company_name required")
    cc = (country_code or "").strip().upper() or None
    if cc == "UK":
        cc = "GB"
    flag, name = country_flag_and_name(cc)
    now = time.time()
    payload = {
        "cache_key": key,
        "domain": (domain or "").strip().lower() or None,
        "company_name": (company_name or "").strip() or None,
        "country_code": cc,
        "country_name": name or None,
        "country_flag": flag or None,
        "source": source,
        "evidence": (evidence or "")[:2000],
        "confidence": float(confidence),
        "fetched_at": now,
        "raw_notes": (raw_notes or "")[:4000] if raw_notes else None,
    }
    conn.execute(
        """
        INSERT INTO company_lookups (
            cache_key, domain, company_name, country_code, country_name,
            country_flag, source, evidence, confidence, fetched_at, raw_notes
        ) VALUES (
            :cache_key, :domain, :company_name, :country_code, :country_name,
            :country_flag, :source, :evidence, :confidence, :fetched_at, :raw_notes
        )
        ON CONFLICT(cache_key) DO UPDATE SET
            domain = excluded.domain,
            company_name = excluded.company_name,
            country_code = excluded.country_code,
            country_name = excluded.country_name,
            country_flag = excluded.country_flag,
            source = excluded.source,
            evidence = excluded.evidence,
            confidence = excluded.confidence,
            fetched_at = excluded.fetched_at,
            raw_notes = excluded.raw_notes
        """,
        payload,
    )
    conn.commit()
    return payload


def html_to_text(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def infer_country_from_text(text: str) -> tuple[str | None, str, float]:
    """Return (country_code, evidence_snippet, confidence)."""
    blob = text or ""
    if not blob:
        return None, "", 0.0

    # Prefer headquarters-style lines.
    best: tuple[str, str, float] | None = None
    for match in _HQ_LINE_RE.finditer(blob):
        window = match.group(0)
        for cc, pattern in _COUNTRY_TEXT_PATTERNS:
            if pattern.search(window):
                conf = 0.85 if cc != "CA" else 0.8
                # Stronger if "headquartered" etc.
                if re.search(r"headquarter", window, re.I):
                    conf = min(0.95, conf + 0.05)
                snippet = window[:220].strip()
                if best is None or conf > best[2]:
                    best = (cc, snippet, conf)
    if best:
        return best[0], best[1], best[2]

    # Fallback: count country mentions in full text (weak).
    counts: dict[str, int] = {}
    for cc, pattern in _COUNTRY_TEXT_PATTERNS:
        hits = pattern.findall(blob)
        if hits:
            counts[cc] = counts.get(cc, 0) + len(hits)
    if not counts:
        return None, "", 0.0
    # Prefer non-Canada if both appear (job pages often list Canada clients).
    ranked = sorted(counts.items(), key=lambda kv: (kv[1], kv[0] != "CA"), reverse=True)
    top_cc, top_n = ranked[0]
    if top_n < 2 and top_cc == "CA":
        return "CA", f"weak canada mentions ({top_n})", 0.35
    conf = min(0.7, 0.35 + 0.1 * top_n)
    return top_cc, f"text mentions ({top_n})", conf


def company_name_from_sender(sender: str | None) -> str | None:
    """Best-effort company label from From header display name."""
    text = (sender or "").strip()
    if not text:
        return None
    if "<" in text:
        text = text.split("<", 1)[0]
    text = text.replace('"', " ").strip()
    if not text or "@" in text:
        return None
    # Drop personal-looking "First Last"
    parts = text.split()
    if len(parts) <= 2 and all(p[:1].isupper() and p[1:].islower() for p in parts if p.isalpha()):
        return None
    return text[:120] if len(text) > 2 else None


def should_lookup_company(
    *,
    sender: str | None,
    foreign_result: dict[str, Any] | None = None,
    canada_job: bool | None = None,
    job_or_contract: bool | None = None,
) -> bool:
    """Whether a web company lookup is worth queuing."""
    domain = extract_sender_domain(sender)
    foreign = foreign_result or {}
    is_canada_job = (
        bool(canada_job)
        if canada_job is not None
        else bool(foreign.get("canada_job"))
    )
    is_job = (
        bool(job_or_contract)
        if job_or_contract is not None
        else bool(foreign.get("job_or_contract"))
    )
    if not (is_canada_job and is_job):
        return False
    # Already high-confidence foreign from local heuristics — still OK to skip web.
    if foreign.get("matched") and float(foreign.get("foreign_score") or 0) >= 5:
        return False
    if domain and not is_free_mail_domain(domain):
        return True
    # Free-mail: only if we have a company-like display name.
    return company_name_from_sender(sender) is not None


async def fetch_url_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> str:
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code >= 400:
            return ""
        content_type = (resp.headers.get("content-type") or "").lower()
        if "html" not in content_type and "text" not in content_type and content_type:
            return ""
        raw = resp.content[:max_bytes].decode(resp.encoding or "utf-8", errors="replace")
        return html_to_text(raw)
    except Exception:
        return ""


async def lookup_company_on_web(
    *,
    domain: str | None = None,
    company_name: str | None = None,
    timeout: float = DEFAULT_LOOKUP_TIMEOUT,
    max_pages: int = 3,
) -> dict[str, Any]:
    """Fetch public web pages and infer company country.

    Returns a result dict (does not write to DB).
    """
    domain = (domain or "").strip().lower() or None
    company_name = (company_name or "").strip() or None
    if domain and is_free_mail_domain(domain):
        domain = None
    if not domain and not company_name:
        return {
            "ok": False,
            "error": "no domain or company name",
            "country_code": None,
            "confidence": 0.0,
            "source": "none",
            "evidence": "",
        }

    timeout = max(3.0, float(timeout))
    texts: list[str] = []
    sources: list[str] = []
    notes: list[str] = []

    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        max_redirects=5,
    ) as client:
        if domain:
            bases = [f"https://{domain}/", f"http://{domain}/"]
            paths = ["", "about", "about-us", "company", "contact", "locations"]
            tried = 0
            for base in bases:
                if tried >= max_pages:
                    break
                for path in paths:
                    if tried >= max_pages:
                        break
                    url = urljoin(base, path)
                    # Only allow http(s) to same host-ish
                    parsed = urlparse(url)
                    if parsed.scheme not in ("http", "https"):
                        continue
                    text = await fetch_url_text(client, url)
                    tried += 1
                    if text and len(text) > 80:
                        texts.append(text[:50000])
                        sources.append(url)
                        notes.append(f"fetched {url} ({len(text)} chars)")

        # DuckDuckGo Lite fallback for headquarters.
        if company_name or domain:
            q = f"{company_name or domain} company headquarters location"
            ddg = f"https://lite.duckduckgo.com/lite/?q={quote_plus(q)}"
            ddg_text = await fetch_url_text(client, ddg)
            if ddg_text and len(ddg_text) > 80:
                texts.append(ddg_text[:50000])
                sources.append("duckduckgo-lite")
                notes.append("duckduckgo lite search")

    combined = "\n".join(texts)
    cc, evidence, conf = infer_country_from_text(combined)
    if not cc and domain:
        # Domain TLD last resort (already partly handled elsewhere).
        from smartinbox.email_middleman import _country_from_domain

        tld_cc, why = _country_from_domain(domain)
        if tld_cc:
            cc, evidence, conf = tld_cc, why or "domain tld", 0.55

    flag, name = country_flag_and_name(cc)
    return {
        "ok": True,
        "domain": domain,
        "company_name": company_name,
        "country_code": cc,
        "country_name": name,
        "country_flag": flag,
        "confidence": conf,
        "source": ",".join(sources[:5]) if sources else "none",
        "evidence": evidence,
        "raw_notes": "; ".join(notes)[:4000],
        "pages_fetched": len(sources),
    }


def lookup_company_on_web_sync(**kwargs: Any) -> dict[str, Any]:
    """Synchronous wrapper for subprocess/thread pool execution."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Nested: run in a fresh loop in this thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(lookup_company_on_web(**kwargs))).result()
        return loop.run_until_complete(lookup_company_on_web(**kwargs))
    except RuntimeError:
        return asyncio.run(lookup_company_on_web(**kwargs))


def apply_web_country_to_foreign_result(
    foreign: dict[str, Any],
    *,
    country_code: str | None,
    confidence: float,
    source: str,
    evidence: str,
) -> dict[str, Any]:
    """Merge a web country into a foreign-middleman classification dict."""
    from smartinbox.email_middleman import (
        foreign_middleman_tag_id,
        tag_label,
    )

    out = dict(foreign)
    cc = (country_code or "").strip().upper() or None
    if cc == "UK":
        cc = "GB"
    if not cc or confidence < 0.45:
        out["web_lookup"] = {
            "applied": False,
            "country_code": cc,
            "confidence": confidence,
            "source": source,
            "evidence": evidence,
        }
        return out

    flag, name = country_flag_and_name(cc)
    out["web_lookup"] = {
        "applied": True,
        "country_code": cc,
        "confidence": confidence,
        "source": source,
        "evidence": evidence,
    }
    # Canadian company from web → not a foreign middleman.
    if cc == "CA":
        out["matched"] = False
        out["canada_company"] = True
        out["country_code"] = "CA"
        out["country_name"] = name
        out["country_flag"] = flag
        out["tags"] = []
        out["tag_labels"] = []
        out["tag_id"] = None
        out["foreign_score"] = max(int(out.get("foreign_score") or 0), 0)
        reasons = list(out.get("reasons") or [])
        reasons.append("web_canada_company")
        out["reasons"] = reasons
        return out

    # Foreign country + Canada job/contract → flag.
    if out.get("canada_job") and out.get("job_or_contract"):
        out["matched"] = True
        out["country_code"] = cc
        out["country_name"] = name
        out["country_flag"] = flag
        out["foreign_score"] = max(int(out.get("foreign_score") or 0), 4)
        tag_id = foreign_middleman_tag_id(cc)
        out["tag_id"] = tag_id
        out["tags"] = [tag_id]
        out["tag_labels"] = [tag_label(tag_id)]
        reasons = list(out.get("reasons") or [])
        reasons.append(f"web_origin:{cc.lower()}")
        out["reasons"] = reasons
    return out
