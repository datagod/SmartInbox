"""Detect third-party job-middleman recruiters (often India-based bench sales).

These senders typically:
- Have Indian-origin personal names
- Pitch C2C / vendor / "my client" roles in the US or Canada
- Are not the end hiring company

Detection is score-based so a name alone is never enough.
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

# Stable tag ids stored on emails.tags (JSON list).
TAG_POSSIBLE_INDIAN_MIDDLEMAN = "possible_indian_middleman"
TAG_INDIAN_MIDDLEMAN = "indian_middleman"
TAG_FOREIGN_MIDDLEMAN = "foreign_middleman"  # base; may be foreign_middleman_in etc.
TAG_LABELS: dict[str, str] = {
    TAG_POSSIBLE_INDIAN_MIDDLEMAN: "Possible Indian middleman 🇮🇳",
    TAG_INDIAN_MIDDLEMAN: "Indian Middleman 🇮🇳",
    TAG_FOREIGN_MIDDLEMAN: "Foreign Middleman",
}

# ISO-ish country code → flag emoji + display name for foreign-company origin.
_COUNTRY_META: dict[str, tuple[str, str]] = {
    "IN": ("🇮🇳", "India"),
    "PH": ("🇵🇭", "Philippines"),
    "PK": ("🇵🇰", "Pakistan"),
    "BD": ("🇧🇩", "Bangladesh"),
    "LK": ("🇱🇰", "Sri Lanka"),
    "US": ("🇺🇸", "United States"),
    "GB": ("🇬🇧", "United Kingdom"),
    "UK": ("🇬🇧", "United Kingdom"),
    "AU": ("🇦🇺", "Australia"),
    "PL": ("🇵🇱", "Poland"),
    "RO": ("🇷🇴", "Romania"),
    "UA": ("🇺🇦", "Ukraine"),
    "MX": ("🇲🇽", "Mexico"),
    "BR": ("🇧🇷", "Brazil"),
    "SG": ("🇸🇬", "Singapore"),
    "AE": ("🇦🇪", "UAE"),
    "ZA": ("🇿🇦", "South Africa"),
    "CN": ("🇨🇳", "China"),
    "NG": ("🇳🇬", "Nigeria"),
    "EG": ("🇪🇬", "Egypt"),
    "DE": ("🇩🇪", "Germany"),
    "FR": ("🇫🇷", "France"),
    "IE": ("🇮🇪", "Ireland"),
    "NL": ("🇳🇱", "Netherlands"),
    "ES": ("🇪🇸", "Spain"),
    "IT": ("🇮🇹", "Italy"),
    "JP": ("🇯🇵", "Japan"),
    "KR": ("🇰🇷", "South Korea"),
    "VN": ("🇻🇳", "Vietnam"),
    "MY": ("🇲🇾", "Malaysia"),
    "ID": ("🇮🇩", "Indonesia"),
    "TH": ("🇹🇭", "Thailand"),
    "NP": ("🇳🇵", "Nepal"),
    "KE": ("🇰🇪", "Kenya"),
    "GH": ("🇬🇭", "Ghana"),
}

# Free-mail domains: confirm exact sender only (never auto-tag whole domain).
_FREE_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.in",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "protonmail.com",
        "proton.me",
        "pm.me",
        "mail.com",
        "ymail.com",
        "rediffmail.com",
    }
)

# Common Indian surnames and given names seen in IT bench-sales mail.
# Not exhaustive; used only as one weak signal combined with recruiting language.
_INDIAN_SURNAMES = frozenset(
    {
        "agarwal",
        "agrawal",
        "ahluwalia",
        "anand",
        "arora",
        "bajaj",
        "banerjee",
        "bansal",
        "bhat",
        "bhatt",
        "bhattacharya",
        "bose",
        "chakraborty",
        "chandrasekhar",
        "chatterjee",
        "chaudhary",
        "chaudhuri",
        "chopra",
        "choudhary",
        "das",
        "desai",
        "dutta",
        "gandhi",
        "ghosh",
        "gupta",
        "hegde",
        "iyengar",
        "iyer",
        "jain",
        "joshi",
        "kapoor",
        "khanna",
        "khatri",
        "krishnan",
        "krishnamurthy",
        "kulkarni",
        "kumar",
        "malhotra",
        "mehta",
        "menon",
        "mishra",
        "mukherjee",
        "nair",
        "naidu",
        "nayak",
        "pandey",
        "patel",
        "pillai",
        "prakash",
        "prasad",
        "rajan",
        "rao",
        "reddy",
        "roy",
        "saxena",
        "sen",
        "shah",
        "sharma",
        "shetty",
        "singh",
        "sinha",
        "srivastava",
        "subramanian",
        "thakur",
        "tiwari",
        "trivedi",
        "varma",
        "venkatesh",
        "verma",
        "yadav",
    }
)

_INDIAN_GIVEN = frozenset(
    {
        "abhishek",
        "aditya",
        "ajay",
        "akash",
        "amit",
        "anil",
        "ankit",
        "ananya",
        "anurag",
        "arjun",
        "ashok",
        "deepak",
        "divya",
        "gaurav",
        "harish",
        "kiran",
        "manish",
        "manoj",
        "mohit",
        "naveen",
        "neha",
        "nikhil",
        "nitin",
        "pooja",
        "pradeep",
        "prakash",
        "priya",
        "rahul",
        "raj",
        "rajes",
        "rajesh",
        "ramesh",
        "ravi",
        "rohit",
        "sachin",
        "sandeep",
        "sanjay",
        "shreya",
        "suresh",
        "swati",
        "varun",
        "vijay",
        "vikas",
        "vinod",
        "vishal",
        "vivek",
    }
)

_RECRUITING_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"\bc2c\b", re.I), 3, "c2c"),
    (re.compile(r"\bcorp[\s\-]?to[\s\-]?corp\b", re.I), 3, "corp_to_corp"),
    (re.compile(r"\b(w2|1099)\b", re.I), 2, "w2_1099"),
    (re.compile(r"\bbench\s*(sales|sales\s*team|resources?)?\b", re.I), 3, "bench"),
    (re.compile(r"\b(end\s*client|my\s*client|our\s*client|client\s*is\s*looking)\b", re.I), 3, "client_pitch"),
    (re.compile(r"\b(implementation\s*partner|preferred\s*vendor|vendor\s*partner)\b", re.I), 3, "vendor"),
    (re.compile(r"\b(hot|urgent|immediate)\s+(requirement|req|opening|need)\b", re.I), 2, "urgent_req"),
    (re.compile(r"\b(looking\s+for\s+(resources?|consultants?|candidates?))\b", re.I), 2, "looking_resources"),
    (
        re.compile(
            r"\b(came\s+across\s+your\s+profile|your\s+profile\s+on|profile\s+on\s+(dice|monster|linkedin|indeed))\b",
            re.I,
        ),
        3,
        "profile_scrape",
    ),
    (re.compile(r"\b(share\s+your\s+(updated\s+)?(resume|cv|rate))\b", re.I), 2, "ask_resume"),
    (re.compile(r"\b(bill\s*rate|pay\s*rate|\$\s*/\s*hr|\$\d+\s*/\s*h(ou)?r)\b", re.I), 2, "rate_card"),
    (re.compile(r"\b(contract\s*(to\s*hire|role|position)|contract\s+opportunity)\b", re.I), 1, "contract_role"),
    (re.compile(r"\b(third[\s\-]?party\s+recruiter|staffing\s+(partner|agency|firm))\b", re.I), 2, "staffing"),
    (re.compile(r"\b(open\s+for\s+new\s+roles?|available\s+for\s+(a\s+)?(new\s+)?(role|opportunity))\b", re.I), 1, "availability"),
    (re.compile(r"\b(sub[\s\-]?vendor|multi[\s\-]?vendor|layer(ed)?\s+vendor)\b", re.I), 3, "subvendor"),
]

_INDIA_GEO = re.compile(
    r"\b(india|indian|bangalore|bengaluru|hyderabad|chennai|pune|noida|gurgaon|gurugram|"
    r"mumbai|delhi|kolkata|ahmedabad|kochi|trivandrum|thiruvananthapuram|"
    r"offshore|based\s+(out\s+)?(of\s+)?india|\+91)\b",
    re.I,
)

_US_CA_GEO = re.compile(
    r"\b(usa|u\.s\.a\.|united\s+states|canada|canadian|toronto|vancouver|ottawa|"
    r"calgary|montreal|mississauga|brampton|greater\s+toronto|gta|"
    r"remote\s*[-–—]?\s*us|us[\s\-]?based\s+client|client\s+in\s+(the\s+)?(us|usa|canada))\b",
    re.I,
)

# Free-mail domains often used by solo bench-sales recruiters.
_PERSONAL_MAIL = re.compile(
    r"@(gmail|yahoo|hotmail|outlook|live|aol|protonmail|icloud)\.",
    re.I,
)

_WORD_RE = re.compile(r"[A-Za-z]+")


def tag_label(tag_id: str) -> str:
    tid = str(tag_id or "").strip()
    if tid in TAG_LABELS:
        return TAG_LABELS[tid]
    # foreign_middleman_in → "Foreign Middleman 🇮🇳"
    if tid.startswith(f"{TAG_FOREIGN_MIDDLEMAN}_") or tid == TAG_FOREIGN_MIDDLEMAN:
        cc = parse_foreign_middleman_country(tid)
        flag, name = country_flag_and_name(cc)
        if flag:
            return f"Foreign Middleman {flag}"
        if name:
            return f"Foreign Middleman ({name})"
        return TAG_LABELS[TAG_FOREIGN_MIDDLEMAN]
    return tid.replace("_", " ").title()


def parse_foreign_middleman_country(tag_id: str | None) -> str | None:
    tid = str(tag_id or "").strip().lower()
    if not tid.startswith(TAG_FOREIGN_MIDDLEMAN):
        return None
    if tid == TAG_FOREIGN_MIDDLEMAN:
        return None
    suffix = tid[len(TAG_FOREIGN_MIDDLEMAN) + 1 :].upper()
    return suffix or None


def foreign_middleman_tag_id(country_code: str | None) -> str:
    cc = (country_code or "").strip().upper()
    if cc == "UK":
        cc = "GB"
    if cc and cc in _COUNTRY_META:
        return f"{TAG_FOREIGN_MIDDLEMAN}_{cc.lower()}"
    return TAG_FOREIGN_MIDDLEMAN


def country_flag_and_name(country_code: str | None) -> tuple[str, str]:
    cc = (country_code or "").strip().upper()
    if cc == "UK":
        cc = "GB"
    meta = _COUNTRY_META.get(cc)
    if not meta:
        return "", ""
    return meta[0], meta[1]


def is_foreign_middleman_tag(tag_id: str | None) -> bool:
    tid = str(tag_id or "").strip()
    return tid == TAG_FOREIGN_MIDDLEMAN or tid.startswith(f"{TAG_FOREIGN_MIDDLEMAN}_")


def strip_foreign_middleman_tags(tags: set[str] | list[str]) -> set[str]:
    return {t for t in tags if not is_foreign_middleman_tag(t)}


def parse_email_tags(raw: Any) -> list[str]:
    """Normalize stored tags JSON/list/string into a clean list of tag ids."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            import json

            data = json.loads(text)
            if isinstance(data, list):
                return [str(t).strip() for t in data if str(t).strip()]
        except (json.JSONDecodeError, TypeError):
            pass
    return [t.strip() for t in text.split(",") if t.strip()]


def serialize_email_tags(tags: list[str]) -> str:
    import json

    clean = sorted({str(t).strip() for t in tags if str(t).strip()})
    return json.dumps(clean)


def _sender_name_parts(sender: str | None) -> list[str]:
    text = (sender or "").strip()
    if not text:
        return []
    # Prefer display name before <email>
    if "<" in text:
        text = text.split("<", 1)[0]
    text = text.replace('"', " ").replace("'", " ")
    # Drop domain-like tokens
    parts = [p.lower() for p in _WORD_RE.findall(text) if len(p) > 1]
    return parts


def _has_indian_name_signal(sender: str | None) -> tuple[bool, list[str]]:
    parts = _sender_name_parts(sender)
    if not parts:
        return False, []
    hits: list[str] = []
    for part in parts:
        if part in _INDIAN_SURNAMES:
            hits.append(f"surname:{part}")
        elif part in _INDIAN_GIVEN:
            hits.append(f"given:{part}")
    # Gmail local-part often is first.last
    email_match = re.search(r"<([^>]+)>", sender or "")
    local = ""
    if email_match:
        local = email_match.group(1).split("@", 1)[0].lower()
    elif sender and "@" in (sender or "") and "<" not in (sender or ""):
        local = (sender or "").split("@", 1)[0].lower()
    for token in re.split(r"[._+\-]+", local):
        if token in _INDIAN_SURNAMES:
            hits.append(f"email_surname:{token}")
        elif token in _INDIAN_GIVEN:
            hits.append(f"email_given:{token}")
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return bool(unique), unique


def _score_patterns(
    blob: str, patterns: list[tuple[re.Pattern[str], int, str]]
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    for pattern, points, label in patterns:
        if pattern.search(blob):
            score += points
            reasons.append(label)
    return score, reasons


def classify_possible_indian_middleman(
    *,
    sender: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    snippet: str | None = None,
) -> dict[str, Any]:
    """Return classification for a single email.

    A hit requires recruiting/middleman language plus either an Indian-origin
    name signal or India + US/Canada geo framing. Name alone is never enough.
    """
    sender_text = sender or ""
    subject_text = subject or ""
    body_text = body or snippet or ""
    blob = f"{sender_text}\n{subject_text}\n{body_text}"

    recruiting_score, recruiting_reasons = _score_patterns(blob, _RECRUITING_PATTERNS)
    name_hit, name_reasons = _has_indian_name_signal(sender_text)
    india_geo = bool(_INDIA_GEO.search(blob))
    us_ca_geo = bool(_US_CA_GEO.search(blob))
    personal_mail = bool(_PERSONAL_MAIL.search(sender_text))

    score = recruiting_score
    reasons = list(recruiting_reasons)

    if name_hit:
        score += 2
        reasons.extend(name_reasons)
    if india_geo:
        score += 2
        reasons.append("india_geo")
    if us_ca_geo:
        score += 1
        reasons.append("us_ca_job_geo")
    if personal_mail and recruiting_score >= 2:
        score += 1
        reasons.append("personal_email_recruiter")

    # Require recruiting substance; then name and/or India↔NA framing.
    matched = False
    if recruiting_score >= 3 and name_hit:
        matched = True
    elif recruiting_score >= 4 and (india_geo or us_ca_geo):
        matched = True
    elif recruiting_score >= 2 and name_hit and (india_geo or us_ca_geo):
        matched = True
    elif recruiting_score >= 5 and name_hit and personal_mail:
        matched = True

    # Hard floor: never tag without recruiting language.
    if recruiting_score < 2:
        matched = False

    tags = [TAG_POSSIBLE_INDIAN_MIDDLEMAN] if matched else []
    return {
        "matched": matched,
        "score": score,
        "recruiting_score": recruiting_score,
        "reasons": reasons,
        "reason_labels": [reason_label(r) for r in reasons],
        "name_hit": name_hit,
        "india_geo": india_geo,
        "us_ca_geo": us_ca_geo,
        "personal_mail": personal_mail,
        "tags": tags,
        "tag_labels": [tag_label(t) for t in tags],
    }


# --- Foreign middleman: Canada job/contract + company based abroad ---

_CANADA_JOB_RE = re.compile(
    r"\b("
    r"canada|canadian|"
    r"toronto|vancouver|ottawa|calgary|montreal|mississauga|brampton|"
    r"edmonton|winnipeg|halifax|victoria|waterloo|kitchener|"
    r"greater\s+toronto|gta|"
    r"ontario|alberta|quebec|manitoba|saskatchewan|"
    r"british\s+columbia|\bbc\b|"
    r"remote\s*[-–—/]?\s*canada|canada\s*[-–—/]?\s*remote|"
    r"hybrid\s*[-–—:].{0,40}(toronto|vancouver|ottawa|canada|calgary|montreal)|"
    r"(toronto|vancouver|ottawa|calgary|montreal).{0,40}(hybrid|onsite|on[\s\-]?site)|"
    r"location\s*[:\-]\s*.{0,40}(canada|toronto|vancouver|ottawa)"
    r")\b",
    re.I,
)

_JOB_OR_CONTRACT_RE = re.compile(
    r"\b("
    r"contract(?:or|ing)?|c2c|corp[\s\-]?to[\s\-]?corp|"
    r"full[\s\-]?time|part[\s\-]?time|opening|position|role|"
    r"requirement|job|hiring|hire(?:d)?|consultant|opportunity|"
    r"w2|1099|permanent|temp\s+to\s+perm|staffing|"
    r"bill\s*rate|pay\s*rate|bench|"
    r"preferred\s+vendor|human\s+capital|talent\s+acquisition|"
    r"looking\s+for\s+(an?\s+)?[A-Za-z]"
    r")\b",
    re.I,
)

_CANADA_COMPANY_RE = re.compile(
    r"\b("
    r"based\s+(out\s+)?(of\s+)?canada|"
    r"canadian\s+(company|firm|office|headquarters|hq)|"
    r"headquartered\s+in\s+canada|"
    r"our\s+(office|team)\s+in\s+(toronto|vancouver|ottawa|calgary|montreal|canada)|"
    r"\.ca\b"
    r")\b",
    re.I,
)

# (country_code, weight, pattern) — higher weight = stronger company-origin signal.
_FOREIGN_COMPANY_PATTERNS: list[tuple[str, int, re.Pattern[str]]] = [
    # India
    ("IN", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?india|headquartered\s+in\s+india|indian\s+(company|firm|office)|office\s+in\s+(india|bangalore|bengaluru|hyderabad|chennai|pune|noida|mumbai|delhi))\b", re.I)),
    ("IN", 2, re.compile(r"\b(bangalore|bengaluru|hyderabad|chennai|pune|noida|gurgaon|gurugram|mumbai|delhi|kolkata|ahmedabad|kochi)\b", re.I)),
    ("IN", 2, re.compile(r"\+91\b")),
    # Philippines
    ("PH", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?(the\s+)?philippines|manila|cebu|makati|quezon\s+city)\b", re.I)),
    ("PH", 2, re.compile(r"\+63\b")),
    # Pakistan
    ("PK", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?pakistan|karachi|lahore|islamabad)\b", re.I)),
    ("PK", 2, re.compile(r"\+92\b")),
    # Bangladesh
    ("BD", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?bangladesh|dhaka)\b", re.I)),
    ("BD", 2, re.compile(r"\+880\b")),
    # Sri Lanka
    ("LK", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?sri\s+lanka|colombo)\b", re.I)),
    # US — HQ wording, street addresses (City, ST ZIP), phone, multi-country footers
    ("US", 3, re.compile(
        r"\b(based\s+(out\s+)?(of\s+)?(the\s+)?(usa|u\.s\.a\.|united\s+states)|"
        r"headquartered\s+in\s+(the\s+)?(usa|u\.s\.a\.|united\s+states)|"
        r"us[\s\-]based\s+(company|firm|staffing)|"
        r"corporate\s+(hq|headquarters|office)\s*[,:]?\s*(usa|u\.s\.a\.|united\s+states))\b",
        re.I,
    )),
    # e.g. Chantilly, VA 20151  /  Somerset, NJ  /  New York, NY 10001
    # ZIP optional — many staffing sigs omit it ("Somerset, NJ, Somerset").
    ("US", 4, re.compile(
        r"\b[A-Za-z][A-Za-z .'-]{1,40},\s*"
        r"(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|"
        r"NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY|DC)"
        r"(?:\s+\d{5}(?:-\d{4})?)?\b",
        re.I,
    )),
    # Street line often near state: "285 Davidson Avenue … Somerset, NJ"
    ("US", 2, re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9 .'-]{2,50}\s+"
        r"(?:Avenue|Ave\.?|Street|St\.?|Drive|Dr\.?|Road|Rd\.?|Boulevard|Blvd\.?|"
        r"Lane|Ln\.?|Way|Court|Ct\.?|Parkway|Pkwy\.?|Suite|Ste\.?)\b",
        re.I,
    )),
    # Multi-country signature listing USA first: "USA | Canada | India | …"
    ("US", 3, re.compile(
        r"\bUSA\s*[|/,·•]\s*(?:Canada|India|Philippines|Singapore|UK|U\.?K\.?|Mexico|Dubai)",
        re.I,
    )),
    ("US", 2, re.compile(r"\b(united\s+states\s+of\s+america|\bu\.s\.a\.\b|\busa\b)\b", re.I)),
    ("US", 2, re.compile(
        r"\b(new\s+york|new\s+jersey|texas|california|chicago|atlanta|dallas|austin|"
        r"seattle|boston|virginia|maryland|chantilly|herndon|reston|sterling|"
        r"arlington|alexandria|mclean|plano|irving|iselin|edison|princeton)\b",
        re.I,
    )),
    # +1-703-291-4860 / (703) 291-4860 — NANP phone (US/Canada shared; weak alone)
    ("US", 1, re.compile(
        r"(?:\+1[-.\s]?)?(?:\([2-9]\d{2}\)|[2-9]\d{2})[-.\s]?\d{3}[-.\s]?\d{4}\b"
    )),
    # UK
    ("GB", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?(the\s+)?(uk|u\.k\.|united\s+kingdom)|london|manchester|birmingham)\b", re.I)),
    ("GB", 2, re.compile(r"\+44\b")),
    # Australia
    ("AU", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?australia|sydney|melbourne|brisbane)\b", re.I)),
    # Poland / Romania / Ukraine (common outsourcing)
    ("PL", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?poland|warsaw|krakow|kraków)\b", re.I)),
    ("RO", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?romania|bucharest|cluj)\b", re.I)),
    ("UA", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?ukraine|kyiv|kiev|lviv)\b", re.I)),
    # Mexico / Brazil / LatAm
    ("MX", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?mexico|mexico\s+city|guadalajara|monterrey)\b", re.I)),
    ("BR", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?brazil|sao\s+paulo|são\s+paulo|rio\s+de\s+janeiro)\b", re.I)),
    # Singapore / UAE / others
    ("SG", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?singapore)\b", re.I)),
    ("AE", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?(uae|dubai|abu\s+dhabi)|united\s+arab\s+emirates)\b", re.I)),
    ("ZA", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?south\s+africa|johannesburg|cape\s+town)\b", re.I)),
    ("CN", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?china|shanghai|beijing|shenzhen)\b", re.I)),
    ("VN", 3, re.compile(r"\b(based\s+(out\s+)?(of\s+)?vietnam|ho\s+chi\s+minh|hanoi)\b", re.I)),
    ("DE", 2, re.compile(r"\b(based\s+(out\s+)?(of\s+)?germany|berlin|munich|frankfurt)\b", re.I)),
    ("IE", 2, re.compile(r"\b(based\s+(out\s+)?(of\s+)?ireland|dublin)\b", re.I)),
    ("NG", 2, re.compile(r"\b(based\s+(out\s+)?(of\s+)?nigeria|lagos)\b", re.I)),
]

# Domain suffix → country (longest match first).
_DOMAIN_COUNTRY_SUFFIXES: list[tuple[str, str]] = [
    (".co.in", "IN"),
    (".com.in", "IN"),
    (".in", "IN"),
    (".co.uk", "GB"),
    (".org.uk", "GB"),
    (".uk", "GB"),
    (".com.au", "AU"),
    (".au", "AU"),
    (".com.ph", "PH"),
    (".ph", "PH"),
    (".pk", "PK"),
    (".bd", "BD"),
    (".lk", "LK"),
    (".com.sg", "SG"),
    (".sg", "SG"),
    (".ae", "AE"),
    (".co.za", "ZA"),
    (".za", "ZA"),
    (".com.br", "BR"),
    (".br", "BR"),
    (".com.mx", "MX"),
    (".mx", "MX"),
    (".pl", "PL"),
    (".ro", "RO"),
    (".ua", "UA"),
    (".vn", "VN"),
    (".cn", "CN"),
    (".de", "DE"),
    (".fr", "FR"),
    (".ie", "IE"),
    (".nl", "NL"),
    (".us", "US"),
]


def _country_from_domain(domain: str | None) -> tuple[str | None, str | None]:
    d = (domain or "").strip().lower()
    if not d or is_free_mail_domain(d):
        return None, None
    # Canadian company domain — not foreign.
    if d.endswith(".ca"):
        return None, "canada_domain"
    for suffix, cc in _DOMAIN_COUNTRY_SUFFIXES:
        if d == suffix.lstrip(".") or d.endswith(suffix):
            # Avoid false positive: something.info matching .in
            if suffix == ".in" and (
                d.endswith(".info")
                or d.endswith(".ink")
                or d.endswith(".industries")
                or d.endswith(".international")
            ):
                continue
            return cc, f"domain_tld:{suffix}"
    return None, None


def _score_foreign_company(
    *, sender: str, blob: str
) -> tuple[str | None, int, list[str]]:
    """Return (country_code, score, reasons) for non-Canadian company origin."""
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}

    def add(cc: str, points: int, reason: str) -> None:
        scores[cc] = scores.get(cc, 0) + points
        reasons.setdefault(cc, []).append(reason)

    domain = extract_sender_domain(sender)
    cc_dom, why = _country_from_domain(domain)
    if cc_dom and why:
        add(cc_dom, 3, why)

    for cc, points, pattern in _FOREIGN_COMPANY_PATTERNS:
        if pattern.search(blob):
            add(cc, points, f"text:{cc.lower()}")

    if not scores:
        return None, 0, []

    # Prefer highest score; tie-break by first pattern order in _COUNTRY_META.
    best_cc = max(
        scores.keys(),
        key=lambda c: (scores[c], -list(_COUNTRY_META.keys()).index(c) if c in _COUNTRY_META else 0),
    )
    return best_cc, scores[best_cc], reasons.get(best_cc, [])


def classify_foreign_middleman(
    *,
    sender: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    snippet: str | None = None,
) -> dict[str, Any]:
    """Canada job/contract pitched by a company based outside Canada.

    Requires:
    - Job/contract language
    - Canada as the job location
    - Company origin signals for a non-Canadian country (stronger than Canada HQ)
    """
    sender_text = sender or ""
    subject_text = subject or ""
    body_text = body or snippet or ""
    blob = f"{sender_text}\n{subject_text}\n{body_text}"

    canada_job = bool(_CANADA_JOB_RE.search(blob))
    job_or_contract = bool(_JOB_OR_CONTRACT_RE.search(blob))
    # Extra weight when Canada appears in the subject (role location).
    canada_in_subject = bool(_CANADA_JOB_RE.search(subject_text))
    canada_company = bool(_CANADA_COMPANY_RE.search(blob))
    domain = extract_sender_domain(sender_text)
    if domain.endswith(".ca"):
        canada_company = True

    country, foreign_score, foreign_reasons = _score_foreign_company(
        sender=sender_text, blob=blob
    )
    canada_company_score = 3 if canada_company else 0

    matched = bool(
        canada_job
        and job_or_contract
        and foreign_score >= 2
        and foreign_score > canada_company_score
        and country
    )
    # Free-mail alone is not enough to claim company country without text signals.
    if matched and is_free_mail_domain(domain) and foreign_score < 3:
        # Require stronger text signals when domain is gmail etc.
        if not any(r.startswith("text:") for r in foreign_reasons):
            matched = False

    tag_id = foreign_middleman_tag_id(country) if matched else None
    flag, country_name = country_flag_and_name(country)
    reasons = list(foreign_reasons)
    if canada_job:
        reasons.append("canada_job_location")
    if canada_in_subject:
        reasons.append("canada_in_subject")
    if job_or_contract:
        reasons.append("job_or_contract")
    if canada_company:
        reasons.append("canada_company_signals")

    return {
        "matched": matched,
        "score": foreign_score + (2 if canada_job else 0) + (1 if job_or_contract else 0),
        "foreign_score": foreign_score,
        "canada_job": canada_job,
        "job_or_contract": job_or_contract,
        "canada_company": canada_company,
        "country_code": country if matched else (country if foreign_score else None),
        "country_name": country_name if matched else "",
        "country_flag": flag if matched else "",
        "reasons": reasons,
        "reason_labels": [reason_label(r) for r in reasons],
        "tags": [tag_id] if tag_id else [],
        "tag_labels": [tag_label(tag_id)] if tag_id else [],
        "tag_id": tag_id,
    }


def format_foreign_middleman_activity_messages(
    result: dict[str, Any],
    *,
    subject: str,
    sender: str | None = None,
    context: str = "tagged",
    source: str = "",
) -> list[str]:
    """Activity-log lines for foreign-middleman classification."""
    subj = (subject or "(no subject)").strip() or "(no subject)"
    from_line = (sender or "").strip()
    matched = bool(result.get("matched"))
    src = (source or "").strip()
    src_note = f" ({src})" if src else ""
    flag = str(result.get("country_flag") or "")
    cname = str(result.get("country_name") or "")
    origin = f"{flag} {cname}".strip() if (flag or cname) else "unknown country"

    if not matched and context == "checked":
        lines = [f"Foreign middleman check{src_note}: not flagged — {subj}"]
        if from_line:
            lines.append(f"  Sender: {from_line}")
        bits: list[str] = []
        if result.get("canada_job"):
            bits.append("Canada job location yes")
        else:
            bits.append("Canada job location no")
        if result.get("job_or_contract"):
            bits.append("job/contract language yes")
        else:
            bits.append("job/contract language no")
        fs = int(result.get("foreign_score") or 0)
        bits.append(f"foreign company score {fs}")
        if result.get("country_code"):
            bits.append(f"hint {result.get('country_code')}")
        lines.append(f"  {'; '.join(bits)}")
        return lines

    if not matched:
        return []

    verb = {
        "tagged": f"Foreign middleman check{src_note}: FLAGGED",
        "confirmed": f"Foreign middleman check{src_note}: still flagged",
        "checked": f"Foreign middleman check{src_note}: flagged",
    }.get(context, f"Foreign middleman check{src_note}: flagged")

    lines = [f"{verb} — Foreign Middleman {flag} — {subj}".replace("  ", " ").strip()]
    if from_line:
        lines.append(f"  Sender: {from_line}")
    lines.append(f"  Company origin: {origin}")
    lines.append(
        f"  Canada job/contract role; foreign score {int(result.get('foreign_score') or 0)}"
    )
    summary = "; ".join(
        lb
        for lb in (result.get("reason_labels") or [])
        if lb and not str(lb).startswith("canada_")
    )
    if summary:
        lines.append(f"  Signals: {summary}")
    return lines


# Machine reason keys → activity-log phrasing.
_REASON_LABELS: dict[str, str] = {
    "c2c": "C2C / corp-to-corp language",
    "corp_to_corp": "corp-to-corp language",
    "w2_1099": "W2/1099 contract terms",
    "bench": "bench sales / bench resources",
    "client_pitch": "third-party client pitch (my/our/end client)",
    "vendor": "implementation partner / preferred vendor",
    "urgent_req": "urgent/hot requirement language",
    "looking_resources": "looking for resources/consultants",
    "profile_scrape": "profile scrape (came across your profile)",
    "ask_resume": "asks for resume/CV/rate",
    "rate_card": "bill/pay rate language",
    "contract_role": "contract role language",
    "staffing": "staffing agency / third-party recruiter",
    "availability": "availability / open for roles",
    "subvendor": "sub-vendor / multi-vendor layer",
    "india_geo": "India / offshore geo signals",
    "us_ca_job_geo": "US or Canada job-market geo",
    "personal_email_recruiter": "personal free-mail address (Gmail/Yahoo/…)",
    "canada_job_location": "Canada job location",
    "canada_in_subject": "Canada mentioned in subject",
    "job_or_contract": "job/contract language",
    "canada_company_signals": "Canadian company signals",
}


def reason_label(reason: str) -> str:
    key = str(reason or "").strip()
    if not key:
        return ""
    if key in _REASON_LABELS:
        return _REASON_LABELS[key]
    if key.startswith("surname:"):
        return f"Indian-origin surname ({key.split(':', 1)[1]})"
    if key.startswith("given:"):
        return f"Indian-origin given name ({key.split(':', 1)[1]})"
    if key.startswith("email_surname:"):
        return f"Indian-origin surname in email ({key.split(':', 1)[1]})"
    if key.startswith("email_given:"):
        return f"Indian-origin given name in email ({key.split(':', 1)[1]})"
    if key.startswith("domain_tld:"):
        return f"sender domain TLD ({key.split(':', 1)[1]})"
    if key.startswith("text:"):
        cc = key.split(":", 1)[1].upper()
        flag, name = country_flag_and_name(cc)
        label = name or cc
        return f"company origin text ({flag} {label})".strip()
    if key.startswith("web_origin:"):
        cc = key.split(":", 1)[1].upper()
        flag, name = country_flag_and_name(cc)
        label = name or cc
        return f"web company lookup ({flag} {label})".strip()
    if key == "web_canada_company":
        return "web company lookup (🇨🇦 Canada)"
    return key.replace("_", " ")


def format_middleman_reason_summary(result: dict[str, Any], *, max_reasons: int = 8) -> str:
    """Compact human-readable list of why a message was flagged."""
    labels = list(result.get("reason_labels") or [])
    if not labels:
        labels = [reason_label(r) for r in (result.get("reasons") or [])]
    labels = [lb for lb in labels if lb]
    if not labels:
        return "no signal details"
    shown = labels[: max(1, int(max_reasons))]
    extra = len(labels) - len(shown)
    text = "; ".join(shown)
    if extra > 0:
        text = f"{text}; +{extra} more"
    return text


def format_middleman_activity_messages(
    result: dict[str, Any],
    *,
    subject: str,
    sender: str | None = None,
    context: str = "tagged",
    source: str = "",
) -> list[str]:
    """Activity-log lines for a third-party recruiter classification.

    ``context`` is one of: tagged | confirmed | cleared | checked.
    ``source`` is an optional label like "new mail" or "re-scan".
    """
    subj = (subject or "(no subject)").strip() or "(no subject)"
    from_line = (sender or "").strip()
    score = int(result.get("score") or 0)
    recruiting = int(result.get("recruiting_score") or 0)
    matched = bool(result.get("matched"))
    src = (source or "").strip()
    src_note = f" ({src})" if src else ""

    if context == "cleared":
        lines = [
            f"Middleman check{src_note}: cleared middleman tag — {subj}",
        ]
        if from_line:
            lines.append(f"  Sender: {from_line}")
        lines.append(
            f"  No longer matches (score {score}, recruiting {recruiting})"
        )
        return lines

    if not matched and context == "checked":
        lines = [
            f"Middleman check{src_note}: not flagged — {subj}",
        ]
        if from_line:
            lines.append(f"  Sender: {from_line}")
        if score or recruiting:
            lines.append(
                f"  Weak signals only (score {score}, recruiting {recruiting}): "
                f"{format_middleman_reason_summary(result)}"
            )
        else:
            lines.append(
                "  No recruiting/middleman signals "
                "(needs C2C/client pitch/etc. plus name or geo cues)"
            )
        return lines

    if not matched:
        return []

    verb = {
        "tagged": f"Middleman check{src_note}: FLAGGED possible Indian middleman",
        "confirmed": f"Middleman check{src_note}: still flagged (possible)",
        "checked": f"Middleman check{src_note}: flagged",
    }.get(context, f"Middleman check{src_note}: flagged")

    lines = [f"{verb} — {subj}"]
    if from_line:
        lines.append(f"  Sender: {from_line}")
    lines.append(
        f"  Score {score} (recruiting {recruiting}): "
        f"{format_middleman_reason_summary(result)}"
    )
    flags: list[str] = []
    if result.get("name_hit"):
        flags.append("Indian-origin name")
    if result.get("india_geo"):
        flags.append("India/offshore")
    if result.get("us_ca_geo"):
        flags.append("US/Canada job market")
    if result.get("personal_mail"):
        flags.append("personal email domain")
    if flags:
        lines.append(f"  Profile: {', '.join(flags)}")
    return lines


def extract_sender_email(sender: str | None) -> str:
    """Return normalized email address from a From header, or empty string."""
    from smartinbox.important_senders import normalize_sender

    return normalize_sender(sender) or ""


def extract_sender_domain(sender: str | None) -> str:
    """Return lowercased domain from a From header, or empty string."""
    email_addr = extract_sender_email(sender)
    if "@" not in email_addr:
        return ""
    return email_addr.rsplit("@", 1)[-1].strip().lower()


def is_free_mail_domain(domain: str | None) -> bool:
    d = (domain or "").strip().lower()
    return d in _FREE_MAIL_DOMAINS


def domain_auto_tag_allowed(domain: str | None) -> bool:
    """True when confirming should fan out to all mail from this domain."""
    d = (domain or "").strip().lower()
    if not d or "." not in d:
        return False
    return not is_free_mail_domain(d)


def tags_for_email(
    *,
    sender: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    snippet: str | None = None,
    existing_tags: list[str] | None = None,
    confirmed: bool = False,
    confirmed_tags: list[str] | None = None,
) -> list[str]:
    """Merge detector results into an existing tag list (idempotent).

    If ``confirmed`` is true (legacy: Indian confirmed list), forces
    ``indian_middleman``. ``confirmed_tags`` forces any stored DB tags
    (Indian and/or Foreign) from the middlemen table.
    Also applies heuristic foreign-middleman (Canada job + foreign company) tags.
    """
    indian = classify_possible_indian_middleman(
        sender=sender,
        subject=subject,
        body=body,
        snippet=snippet,
    )
    foreign = classify_foreign_middleman(
        sender=sender,
        subject=subject,
        body=body,
        snippet=snippet,
    )
    merged = set(existing_tags or [])
    # Refresh detector tags based on current content / confirmation.
    merged.discard(TAG_POSSIBLE_INDIAN_MIDDLEMAN)
    merged.discard(TAG_INDIAN_MIDDLEMAN)
    merged = strip_foreign_middleman_tags(merged)

    forced = list(confirmed_tags or [])
    if confirmed and TAG_INDIAN_MIDDLEMAN not in forced:
        forced.append(TAG_INDIAN_MIDDLEMAN)

    has_forced_indian = TAG_INDIAN_MIDDLEMAN in forced
    has_forced_foreign = any(is_foreign_middleman_tag(t) for t in forced)

    if has_forced_indian:
        merged.add(TAG_INDIAN_MIDDLEMAN)
    else:
        for tag in indian["tags"]:
            merged.add(tag)

    if has_forced_foreign:
        for tag in forced:
            if is_foreign_middleman_tag(tag):
                merged.add(tag)
    else:
        for tag in foreign["tags"]:
            merged.add(tag)

    # Any other forced tags (future kinds)
    for tag in forced:
        if tag and tag not in (
            TAG_POSSIBLE_INDIAN_MIDDLEMAN,
            TAG_INDIAN_MIDDLEMAN,
        ) and not is_foreign_middleman_tag(tag):
            merged.add(tag)
    return sorted(merged)


def flag_image_path(country_code: str | None) -> str:
    """Local Twemoji SVG path for a country code, or empty if unknown."""
    cc = (country_code or "").strip().lower()
    if cc == "uk":
        cc = "gb"
    if not cc or len(cc) != 2 or not cc.isalpha():
        return ""
    # Served from smartinbox/web/static/img/flags/{cc}.svg
    return f"/static/img/flags/{cc}.svg"


def public_tag_entries(tag_ids: list[str]) -> list[dict[str, Any]]:
    """API/UI-friendly tag objects (clickable for possible-middleman).

    ``label_text`` is plain text; ``flag`` is the emoji character; ``flag_img``
    is a local Twemoji SVG so flags render even without system emoji fonts.
    """
    entries: list[dict[str, Any]] = []
    for t in tag_ids:
        entry: dict[str, Any] = {
            "id": t,
            "label": tag_label(t),
            "label_text": tag_label(t),
            "flag": "",
            "flag_img": "",
            "clickable": t == TAG_POSSIBLE_INDIAN_MIDDLEMAN,
        }
        if t == TAG_POSSIBLE_INDIAN_MIDDLEMAN:
            flag, _name = country_flag_and_name("IN")
            entry["flag"] = flag  # 🇮🇳 emoji
            entry["flag_img"] = flag_image_path("IN")
            entry["label_text"] = "Possible Indian middleman"
            entry["label"] = f"Possible Indian middleman {flag}".strip()
            entry["country_code"] = "IN"
            entry["country_flag"] = flag
            entry["clickable"] = True
            entry["confirm_action"] = "indian"
            entry["title"] = (
                "Click to confirm as Indian Middleman "
                "(saves sender and auto-tags same domain)"
            )
        elif t == TAG_INDIAN_MIDDLEMAN:
            flag, _name = country_flag_and_name("IN")
            entry["flag"] = flag
            entry["flag_img"] = flag_image_path("IN")
            entry["label_text"] = "Indian Middleman"
            entry["label"] = f"Indian Middleman {flag}".strip()
            entry["country_code"] = "IN"
            entry["country_flag"] = flag
            entry["title"] = "Confirmed Indian Middleman"
        elif is_foreign_middleman_tag(t):
            cc = parse_foreign_middleman_country(t)
            flag, name = country_flag_and_name(cc)
            origin = f"{flag} {name}".strip() or "foreign country"
            entry["flag"] = flag
            entry["flag_img"] = flag_image_path(cc)
            entry["label_text"] = "Foreign Middleman"
            entry["label"] = f"Foreign Middleman {flag}".strip() if flag else "Foreign Middleman"
            entry["clickable"] = True
            entry["confirm_action"] = "foreign"
            entry["title"] = (
                f"Click to save as confirmed Foreign Middleman ({origin}) "
                "— stores sender/domain and auto-tags same company domain"
            )
            entry["country_code"] = cc
            entry["country_flag"] = flag
            entry["country_name"] = name
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Confirmed middlemen persistence (Indian + Foreign)
# ---------------------------------------------------------------------------


def init_indian_middlemen_table(conn: sqlite3.Connection) -> None:
    """Legacy table + unified middlemen table (Indian and Foreign)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS indian_middlemen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_key TEXT NOT NULL,
            domain TEXT NOT NULL,
            display TEXT NOT NULL,
            email_address TEXT NOT NULL,
            source_email_id TEXT,
            source_subject TEXT,
            auto_domain INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at REAL NOT NULL,
            UNIQUE(sender_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_indian_middlemen_domain "
        "ON indian_middlemen(domain)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS middlemen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_key TEXT NOT NULL,
            domain TEXT NOT NULL,
            display TEXT NOT NULL,
            email_address TEXT NOT NULL,
            kind TEXT NOT NULL,
            country_code TEXT,
            country_name TEXT,
            country_flag TEXT,
            tag_id TEXT NOT NULL,
            source_email_id TEXT,
            source_subject TEXT,
            auto_domain INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at REAL NOT NULL,
            UNIQUE(sender_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_middlemen_domain ON middlemen(domain)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_middlemen_kind ON middlemen(kind)"
    )
    # Migrate legacy indian_middlemen rows into unified table.
    legacy = conn.execute(
        "SELECT sender_key, domain, display, email_address, source_email_id, "
        "source_subject, auto_domain, notes, created_at FROM indian_middlemen"
    ).fetchall()
    flag_in, name_in = country_flag_and_name("IN")
    for row in legacy:
        conn.execute(
            """
            INSERT INTO middlemen (
                sender_key, domain, display, email_address,
                kind, country_code, country_name, country_flag, tag_id,
                source_email_id, source_subject, auto_domain, notes, created_at
            ) VALUES (?, ?, ?, ?, 'indian', 'IN', ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sender_key) DO NOTHING
            """,
            (
                row["sender_key"],
                row["domain"],
                row["display"],
                row["email_address"],
                name_in,
                flag_in,
                TAG_INDIAN_MIDDLEMAN,
                row["source_email_id"],
                row["source_subject"],
                row["auto_domain"],
                row["notes"],
                row["created_at"],
            ),
        )
    conn.commit()


def list_indian_middlemen(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, sender_key, domain, display, email_address,
               source_email_id, source_subject, auto_domain, notes, created_at
        FROM indian_middlemen
        ORDER BY created_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def list_middlemen(
    conn: sqlite3.Connection, *, kind: str | None = None
) -> list[dict[str, Any]]:
    if kind:
        rows = conn.execute(
            """
            SELECT * FROM middlemen WHERE kind = ?
            ORDER BY created_at DESC
            """,
            (kind,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM middlemen ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_confirmed_sender_keys(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT sender_key FROM middlemen").fetchall()
    keys = {str(r["sender_key"]).lower() for r in rows if r["sender_key"]}
    # Legacy fallback
    rows2 = conn.execute("SELECT sender_key FROM indian_middlemen").fetchall()
    keys |= {str(r["sender_key"]).lower() for r in rows2 if r["sender_key"]}
    return keys


def get_confirmed_auto_domains(conn: sqlite3.Connection) -> set[str]:
    """Domains that should auto-tag all mail (non free-mail only)."""
    rows = conn.execute(
        "SELECT DISTINCT domain FROM middlemen WHERE auto_domain = 1"
    ).fetchall()
    domains = {str(r["domain"]).lower() for r in rows if r["domain"]}
    rows2 = conn.execute(
        "SELECT DISTINCT domain FROM indian_middlemen WHERE auto_domain = 1"
    ).fetchall()
    domains |= {str(r["domain"]).lower() for r in rows2 if r["domain"]}
    return domains


def get_confirmed_middleman_record(
    conn: sqlite3.Connection, sender: str | None
) -> dict[str, Any] | None:
    """Return the best matching confirmed middleman row for a sender."""
    key = extract_sender_email(sender)
    domain = extract_sender_domain(sender)
    if key:
        row = conn.execute(
            "SELECT * FROM middlemen WHERE lower(sender_key) = ?",
            (key.lower(),),
        ).fetchone()
        if row:
            return dict(row)
    if domain:
        row = conn.execute(
            """
            SELECT * FROM middlemen
            WHERE auto_domain = 1 AND lower(domain) = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (domain.lower(),),
        ).fetchone()
        if row:
            return dict(row)
    # Legacy indian-only domain match
    if key:
        row = conn.execute(
            "SELECT * FROM indian_middlemen WHERE lower(sender_key) = ?",
            (key.lower(),),
        ).fetchone()
        if row:
            data = dict(row)
            data.setdefault("kind", "indian")
            data.setdefault("tag_id", TAG_INDIAN_MIDDLEMAN)
            data.setdefault("country_code", "IN")
            return data
    if domain:
        row = conn.execute(
            """
            SELECT * FROM indian_middlemen
            WHERE auto_domain = 1 AND lower(domain) = ?
            LIMIT 1
            """,
            (domain.lower(),),
        ).fetchone()
        if row:
            data = dict(row)
            data.setdefault("kind", "indian")
            data.setdefault("tag_id", TAG_INDIAN_MIDDLEMAN)
            data.setdefault("country_code", "IN")
            return data
    return None


def confirmed_tags_for_sender(
    conn: sqlite3.Connection, sender: str | None
) -> list[str]:
    """Tags that should be forced on mail from a confirmed middleman."""
    rec = get_confirmed_middleman_record(conn, sender)
    if not rec:
        return []
    tag_id = str(rec.get("tag_id") or "").strip()
    kind = str(rec.get("kind") or "").strip().lower()
    if tag_id:
        return [tag_id]
    if kind == "indian":
        return [TAG_INDIAN_MIDDLEMAN]
    return []


def is_confirmed_middleman_sender(
    conn: sqlite3.Connection, sender: str | None
) -> bool:
    return get_confirmed_middleman_record(conn, sender) is not None


def is_confirmed_indian_middleman_sender(
    conn: sqlite3.Connection, sender: str | None
) -> bool:
    rec = get_confirmed_middleman_record(conn, sender)
    if not rec:
        return False
    kind = str(rec.get("kind") or "").lower()
    tag_id = str(rec.get("tag_id") or "")
    return kind == "indian" or tag_id == TAG_INDIAN_MIDDLEMAN


def add_confirmed_middleman(
    conn: sqlite3.Connection,
    *,
    sender: str | None,
    kind: str,
    country_code: str | None = None,
    tag_id: str | None = None,
    source_email_id: str | None = None,
    source_subject: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Record a confirmed middleman (indian or foreign). Returns stored row."""
    from smartinbox.important_senders import display_sender

    email_address = extract_sender_email(sender)
    if not email_address:
        raise ValueError("Could not parse sender email address.")
    domain = extract_sender_domain(sender)
    display = display_sender(sender) or email_address
    auto_domain = 1 if domain_auto_tag_allowed(domain) else 0
    kind_norm = (kind or "foreign").strip().lower()
    if kind_norm not in ("indian", "foreign"):
        kind_norm = "foreign"
    cc = (country_code or "").strip().upper() or None
    if cc == "UK":
        cc = "GB"
    if kind_norm == "indian":
        cc = "IN"
        resolved_tag = TAG_INDIAN_MIDDLEMAN
    else:
        resolved_tag = (tag_id or "").strip() or foreign_middleman_tag_id(cc)
    flag, name = country_flag_and_name(cc)
    now = time.time()
    conn.execute(
        """
        INSERT INTO middlemen (
            sender_key, domain, display, email_address,
            kind, country_code, country_name, country_flag, tag_id,
            source_email_id, source_subject, auto_domain, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sender_key) DO UPDATE SET
            domain = excluded.domain,
            display = excluded.display,
            email_address = excluded.email_address,
            kind = excluded.kind,
            country_code = excluded.country_code,
            country_name = excluded.country_name,
            country_flag = excluded.country_flag,
            tag_id = excluded.tag_id,
            source_email_id = COALESCE(excluded.source_email_id, source_email_id),
            source_subject = COALESCE(excluded.source_subject, source_subject),
            auto_domain = excluded.auto_domain,
            notes = COALESCE(excluded.notes, notes)
        """,
        (
            email_address,
            domain,
            display,
            email_address,
            kind_norm,
            cc,
            name or None,
            flag or None,
            resolved_tag,
            source_email_id,
            source_subject,
            auto_domain,
            notes,
            now,
        ),
    )
    # Keep legacy indian table in sync for older code paths.
    if kind_norm == "indian":
        conn.execute(
            """
            INSERT INTO indian_middlemen (
                sender_key, domain, display, email_address,
                source_email_id, source_subject, auto_domain, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sender_key) DO UPDATE SET
                domain = excluded.domain,
                display = excluded.display,
                email_address = excluded.email_address,
                source_email_id = COALESCE(excluded.source_email_id, source_email_id),
                source_subject = COALESCE(excluded.source_subject, source_subject),
                auto_domain = excluded.auto_domain,
                notes = COALESCE(excluded.notes, notes)
            """,
            (
                email_address,
                domain,
                display,
                email_address,
                source_email_id,
                source_subject,
                auto_domain,
                notes,
                now,
            ),
        )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM middlemen WHERE sender_key = ?",
        (email_address,),
    ).fetchone()
    return dict(row) if row else {
        "sender_key": email_address,
        "domain": domain,
        "display": display,
        "email_address": email_address,
        "kind": kind_norm,
        "country_code": cc,
        "country_flag": flag,
        "tag_id": resolved_tag,
        "auto_domain": auto_domain,
    }


def add_confirmed_indian_middleman(
    conn: sqlite3.Connection,
    *,
    sender: str | None,
    source_email_id: str | None = None,
    source_subject: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Record a confirmed Indian middleman (wrapper)."""
    return add_confirmed_middleman(
        conn,
        sender=sender,
        kind="indian",
        country_code="IN",
        tag_id=TAG_INDIAN_MIDDLEMAN,
        source_email_id=source_email_id,
        source_subject=source_subject,
        notes=notes,
    )


def add_confirmed_foreign_middleman(
    conn: sqlite3.Connection,
    *,
    sender: str | None,
    country_code: str | None = None,
    tag_id: str | None = None,
    source_email_id: str | None = None,
    source_subject: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Record a confirmed foreign middleman from an inbox tag click."""
    return add_confirmed_middleman(
        conn,
        sender=sender,
        kind="foreign",
        country_code=country_code,
        tag_id=tag_id,
        source_email_id=source_email_id,
        source_subject=source_subject,
        notes=notes or "Confirmed from Foreign Middleman tag click",
    )


def emails_matching_middleman_scope(
    conn: sqlite3.Connection,
    *,
    sender_key: str,
    domain: str,
    auto_domain: bool,
) -> list[dict[str, Any]]:
    """Emails that should receive the confirmed middleman tag."""
    if auto_domain and domain:
        # Match by sender_key domain suffix or raw sender containing @domain
        rows = conn.execute(
            """
            SELECT id, sender, sender_key, subject, tags FROM emails
            WHERE lower(COALESCE(sender_key, '')) LIKE ?
               OR lower(COALESCE(sender, '')) LIKE ?
            """,
            (f"%@{domain.lower()}", f"%@{domain.lower()}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, sender, sender_key, subject, tags FROM emails
            WHERE lower(COALESCE(sender_key, '')) = ?
               OR lower(COALESCE(sender, '')) LIKE ?
            """,
            (sender_key.lower(), f"%<{sender_key.lower()}>%"),
        ).fetchall()
    return [dict(r) for r in rows]


def promote_tags_to_confirmed(existing_tags: list[str] | None) -> list[str]:
    """Replace possible tag with confirmed indian_middleman."""
    merged = set(existing_tags or [])
    merged.discard(TAG_POSSIBLE_INDIAN_MIDDLEMAN)
    merged.add(TAG_INDIAN_MIDDLEMAN)
    return sorted(merged)


def promote_tags_to_foreign(
    existing_tags: list[str] | None, *, tag_id: str
) -> list[str]:
    """Force a foreign middleman tag id onto the email's tag list."""
    resolved = (tag_id or "").strip() or TAG_FOREIGN_MIDDLEMAN
    merged = strip_foreign_middleman_tags(set(existing_tags or []))
    merged.add(resolved)
    return sorted(merged)
