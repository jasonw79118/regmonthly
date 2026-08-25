from __future__ import annotations

import json
import os
import re
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urldefrag, urlparse, parse_qs
from xml.etree import ElementTree as ET

from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning
from dateutil import parser as dtparser


# ============================
# CONFIG
# ============================

OUT_PATH = "docs/data/items.json"

# --- Copilot-friendly static exports (no JS required) ---
RAW_DIR = "docs/raw"
RAW_HTML_PATH = f"{RAW_DIR}/index.html"
RAW_MD_PATH = f"{RAW_DIR}/items.md"
RAW_TXT_PATH = f"{RAW_DIR}/items.txt"
RAW_NDJSON_PATH = f"{RAW_DIR}/items.ndjson"
RAW_ARRAY_JSON_PATH = f"{RAW_DIR}/items-array.json"
RAW_SMART_100_ARRAY_JSON_PATH = f"{RAW_DIR}/items-smart-100.json"  # existing Power Automate endpoint; now carries 150 items
RAW_ROBOTS_PATH = f"{RAW_DIR}/robots.txt"
RAW_SITEMAP_PATH = f"{RAW_DIR}/sitemap.xml"

# --- One big "print" page (single HTML file, no JS) ---
PRINT_DIR = "docs/print"
PRINT_HTML_PATH = f"{PRINT_DIR}/items.html"

# ✅ IMPORTANT: base for regdashboard (your live site)
PUBLIC_BASE = "https://jasonw79118.github.io/regmonthly"

# ✅ RegDashboard MUST be a rolling window (2 weeks)
WINDOW_DAYS = 14

# Bump caps so Visa/Mastercard can resolve dates for more listing links
MAX_LISTING_LINKS = 3500  # monthly: allow many listing links (full month coverage)
GLOBAL_DETAIL_FETCH_CAP = 2200  # monthly: allow many detail fetches (full month coverage)
REQUEST_DELAY_SEC = 0.12

PER_SOURCE_DETAIL_CAP: Dict[str, int] = {
    "IRS": 140,
    "Senate Banking": 160,
    "FinCEN": 220,
    "USDA Rural Development": 55,
    "Mastercard": 120,
    "Visa": 160,
    "FHLB MPF": 25,
    "Fannie Mae": 35,
    "Freddie Mac": 10,
    "FIS": 25,
    "Fiserv": 25,       # ✅ DO NOT CHANGE (your request)
    "Jack Henry": 25,
    "Finastra": 20,
    "TCS": 25,
    "OFAC": 220,
    "Treasury": 220,
    "OCC": 25,
    "FDIC": 25,
    "FRB": 30,
    "FRB Payments": 30,
    "NACHA": 80,
    "White House": 220,
    "Federal Register": 0,  # API only
    "BleepingComputer": 0,  # feed-only
    "Microsoft MSRC": 0,    # feed-only

    # New tiles/sources
    "CDIA": 25,
    "FASB": 25,
    "ABA": 120,
    "TBA": 25,
    "Wolters Kluwer": 120,
    "Bankers Online": 120,

    # International / regulatory-review sources
    "BIS": 0,          # official RSS feed
    "FATF": 160,      # listing items often need detail-page date confirmation
    "RegInfo.gov": 0, # official XML feeds
}

# Sources where we keep listing links but DO NOT fetch detail pages (to avoid blocks/timeouts)
SKIP_DETAIL_SOURCES = {"Visa", "Fannie Mae"}
DEFAULT_SOURCE_DETAIL_CAP = 15

UA = "regmonthly/1.0 (+https://github.com/jasonw79118/regmonthly)"


# ============================
# CATEGORY MAPPING (for tiles)
# ============================

CATEGORY_BY_SOURCE: Dict[str, str] = {
    "OFAC": "OFAC",
    "Treasury": "OFAC",
    "FinCEN": "OFAC",
    "IRS": "IRS",

    # Payments tile
    "NACHA": "Payments",
    "FRB Payments": "Payments",
    "FRB": "Banking",

    # Banking tile
    "OCC": "Banking",
    "FDIC": "Banking",

    # Mortgage tile
    "FHLB MPF": "Mortgage",
    "Fannie Mae": "Mortgage",
    "Freddie Mac": "Mortgage",

    # Legislative / Executive tiles
    "Senate Banking": "Legislative",
    "House Financial Services": "Legislative",
    "White House": "Executive",

    # Federal Register
    "Federal Register": "Federal Register",

    # USDA tile
    "USDA Rural Development": "Mortgage",

    # Fintech Watch tile
    "FIS": "Fintech Watch",
    "Fiserv": "Fintech Watch",
    "Jack Henry": "Fintech Watch",
    "Finastra": "Fintech Watch",
    "TCS": "Fintech Watch",

    # Payment Card Networks tile
    "Visa": "Payment Card Networks",
    "Mastercard": "Payment Card Networks",

    # InfoSec tile
    "BleepingComputer": "IS",
    "Microsoft MSRC": "IS",

    # Compliance Watch tile
    "CDIA": "Compliance Watch",
    "FASB": "Compliance Watch",
    "ABA": "Compliance Watch",
    "TBA": "Compliance Watch",
    "Wolters Kluwer": "Compliance Watch",
    "Bankers Online": "Compliance Watch",

    # International / regulatory-review sources
    "BIS": "International Banking",
    "FATF": "OFAC",
    "RegInfo.gov": "Regulatory Review",
}


# ============================
# FEDERAL REGISTER API (filters)
# ============================

FEDREG_API_BASE = "https://www.federalregister.gov/api/v1"

RAW_FEDREG_FILTERS: List[Dict[str, str]] = [
    {"kind": "topics", "value": "banks-banking"},
    {"kind": "topics", "value": "executive-orders"},
    {"kind": "topics", "value": "federal-reserve-system"},
    {"kind": "topics", "value": "national-banks"},
    {"kind": "topics", "value": "securities"},
    {"kind": "topics", "value": "mortgages"},
    {"kind": "topics", "value": "truth-lending"},
    {"kind": "topics", "value": "truth-savings"},

    {"kind": "agencies", "value": "consumer-financial-protection-bureau"},
    {"kind": "agencies", "value": "federal-deposit-insurance-corporation"},

    {"kind": "topics", "value": "child-labor"},
    {"kind": "topics", "value": "credit"},
    {"kind": "topics", "value": "credit-unions"},
    {"kind": "topics", "value": "currency"},
    {"kind": "topics", "value": "economic-statistics"},
    {"kind": "topics", "value": "employment"},
    {"kind": "topics", "value": "employment-taxes"},
    {"kind": "topics", "value": "fair-housing"},
    {"kind": "topics", "value": "federal-home-loan-banks"},
    {"kind": "topics", "value": "flood-insurance"},
    {"kind": "topics", "value": "foreign-banking"},
    {"kind": "topics", "value": "government-sponsored-enterprise"},
    {"kind": "topics", "value": "holding-companies"},
    {"kind": "topics", "value": "housing"},
    {"kind": "topics", "value": "income-taxes"},
    {"kind": "topics", "value": "insurance"},
    {"kind": "topics", "value": "investment-companies"},
    {"kind": "topics", "value": "investments"},
    {"kind": "topics", "value": "justice-department"},
    {"kind": "topics", "value": "loan-programs"},
    {"kind": "topics", "value": "loan-programs-agriculture"},
    {"kind": "topics", "value": "loan-programs-business"},
    {"kind": "topics", "value": "loan-programs-communications"},
    {"kind": "topics", "value": "loan-programs-education"},
    {"kind": "topics", "value": "manufactured-home"},
    {"kind": "topics", "value": "mortgage-insurance"},
    {"kind": "topics", "value": "personally-identifiable-information"},
    {"kind": "topics", "value": "savings-associations"},
    {"kind": "topics", "value": "trust-and-trustees"},
]


def normalize_fedreg_slug(raw: str) -> str:
    s = (raw or "").strip()
    s = s.replace("_", "-")
    s = re.sub(r"\s+", "-", s)
    s = s.strip("-")
    s = s.lower()
    s = re.sub(r"-{2,}", "-", s)
    return s


def build_fedreg_filters() -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for f in RAW_FEDREG_FILTERS:
        kind = (f.get("kind") or "").strip().lower()
        val = normalize_fedreg_slug(f.get("value") or "")
        if kind not in {"topics", "agencies", "sections"}:
            continue
        if not val:
            continue
        key = (kind, val)
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": kind, "value": val})
    return out


FEDREG_FILTERS = build_fedreg_filters()


# ============================
# HTTP SESSION
# ============================

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
)


# ============================
# RULES: keep scrapes focused
# ============================

SOURCE_RULES: Dict[str, Dict[str, Any]] = {
    "IRS": {
        "allow_domains": {"www.irs.gov"},
        "allow_path_prefixes": {"/newsroom/", "/downloads/rss", "/downloads/rss/"},
        "deny_domains": {"sa.www4.irs.gov"},
    },
    "FRB": {"deny_domains": {"www.facebook.com"}},
    "FRB Payments": {"deny_domains": {"www.facebook.com"}},

    "NACHA": {
        "allow_domains": {"www.nacha.org", "nacha.org"},
        # only real article pages live under /news/<slug>
        "allow_path_prefixes": {"/news/"},
    },


    "Freddie Mac": {
        "allow_domains": {"www.globenewswire.com"},
        "allow_path_prefixes": {
            "/search/organization/",
            "/en/search/organization/",
            "/news-release/",
            "/en/news-release/",
        },
    },

    "USDA Rural Development": {
        "allow_domains": {"content.govdelivery.com", "www.rd.usda.gov"},
        "allow_path_prefixes": {"/accounts/USDARD/bulletins", "/bulletins/", "/newsroom/"},
    },

    # OCC keeps its release details under the older /news-issuances path, while
    # its current index/year pages live under /news-events/newsroom.
    "OCC": {
        "allow_domains": {"www.occ.gov", "occ.gov"},
        "allow_path_prefixes": {
            "/news-issuances/news-releases/",
            "/news-events/newsroom/",
            "/news-events/",
        },
    },

    "OFAC": {
        "allow_domains": {"ofac.treasury.gov"},
        "allow_path_prefixes": {"/recent-actions/"},
    },

    "Treasury": {
        "allow_domains": {"home.treasury.gov"},
        "allow_path_prefixes": {"/news/press-releases"},
    },

    "FinCEN": {
        "allow_domains": {"www.fincen.gov", "fincen.gov"},
        # FinCEN migrated from /news-room/... to /news/... (and /newsroom on some pages)
        "allow_path_prefixes": {"/news", "/news-room", "/newsroom", "/files/news"},
    },
    "House Financial Services": {
        "allow_domains": {"financialservices.house.gov"},
        "allow_path_prefixes": {"/news/"},
    },

    "Senate Banking": {
        "allow_domains": {"www.banking.senate.gov", "banking.senate.gov"},
        "allow_path_prefixes": {"/newsroom", "/newsroom/", "/news", "/news/"},
    },

    

    "White House": {
        "allow_domains": {"www.whitehouse.gov"},
        "allow_path_prefixes": {
            "/news/",
            "/briefings-statements/",
            "/presidential-actions/",
            "/fact-sheets/",
            "/remarks/",
            "/research/",
            "/articles/",
        },
    },

    "Visa": {
        "allow_domains": {"usa.visa.com"},
        "allow_path_prefixes": {"/about-visa/newsroom/press-releases"},
    },

    "Mastercard": {
        "allow_domains": {"www.mastercard.com"},
        "allow_path_prefixes": {
            "/us/en/news-and-trends/press/",
            "/global/en/news-and-trends/press/",
            "/news-and-trends/press/",
            "/en/news-and-trends/press/",
            "/gb/en/news-and-trends/press/",
            "/mea/en/news-and-trends/press/",
            "/us/en/newsroom/press-releases/",
            "/global/en/newsroom/press-releases/",
            "/en/newsroom/press-releases/",
            "/gb/en/newsroom/press-releases/",
            "/mea/en/newsroom/press-releases/",
        },
    },

    "Federal Register": {
        "allow_domains": {"www.federalregister.gov"},
        "allow_path_prefixes": {"/documents/"},
    },

    "FIS": {"allow_domains": {"investor.fisglobal.com", "www.investor.fisglobal.com"}},
    "Fiserv": {"allow_domains": {"investors.fiserv.com"}},

    # ✅ Jack Henry links are often in tables; allow both press-releases and news-releases detail pages.
    "Jack Henry": {"allow_domains": {"ir.jackhenry.com", "jkhy.client.shareholder.com", "www.prnewswire.com"}},

    "Finastra": {"allow_domains": {"www.finastra.com"}},

    # ✅ TCS: add feedburner domains because many press releases advertise RSS via feeds2.feedburner.com
    "TCS": {"allow_domains": {"www.tcs.com", "feeds2.feedburner.com", "feedburner.com"}},

    "FHLB MPF": {
        "allow_domains": {"www.fhlbmpf.com"},
        "allow_path_prefixes": {"/program-guidelines/mpf-program-updates"},
    },

    "CDIA": {
        "allow_domains": {"www.cdiaonline.org"},
        "allow_path_prefixes": {"/news", "/news-events-blogs", "/events", "/blog", "/"},
    },

    "FASB": {
        "allow_domains": {"www.fasb.org", "fasb.org"},
        "allow_path_prefixes": {"/news-and-meetings/in-the-news", "/news-and-meetings/"},
    },

    "ABA": {
        "allow_domains": {"www.aba.com", "bankingjournal.aba.com"},
        "allow_path_prefixes": {"/news-research/", "/"},
    },
    "TBA": {
        "allow_domains": {"www.texasbankers.com"},
        "allow_path_prefixes": {"/news/", "/"},
    },
    "Wolters Kluwer": {
        "allow_domains": {"www.wolterskluwer.com"},
        "allow_path_prefixes": {"/en/news", "/en-gb/news", "/en/news/"},
    },
    "Bankers Online": {
        "allow_domains": {"www.bankersonline.com", "files.bankersonline.com"},
        "allow_path_prefixes": {"/topstory", "/cb/", "/bb/", "/tt/", "/security/", "/"},
    },

    "BIS": {
        "allow_domains": {"www.bis.org", "bis.org"},
    },
    "FATF": {
        "allow_domains": {"www.fatf-gafi.org", "fatf-gafi.org"},
        "allow_path_prefixes": {
            "/en/news/",
            "/en/publications/",
            "/content/fatf-gafi/en/publications/",
        },
    },
    "RegInfo.gov": {
        "allow_domains": {"www.reginfo.gov", "reginfo.gov"},
        "allow_path_prefixes": {"/public/"},
    },
}

GLOBAL_DENY_DOMAINS = {"www.facebook.com"}
GLOBAL_DENY_SCHEMES = {"mailto", "tel", "javascript"}


# ============================
# HELPERS
# ============================

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

LAST_RUN_PATH = "docs/data/last_run.json"


# ============================
# SCHEDULER GATE (GitHub Actions friendly)
#   ✅ Monthly: run only on the 1st of the month (CT) and only once per month.
#   Set FORCE_RUN=1 to override (useful for testing / re-runs).
# ============================

def _load_last_run_month() -> str:
    try:
        with open(LAST_RUN_PATH, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("month", "")
    except Exception:
        return ""


def _save_last_run_month(month_str: str) -> None:
    os.makedirs(os.path.dirname(LAST_RUN_PATH), exist_ok=True)
    with open(LAST_RUN_PATH, "w", encoding="utf-8") as f:
        json.dump({"month": month_str, "saved_at_utc": iso_z(utc_now())}, f)


def should_run_monthly_ct(target_hour: int = 7, window_minutes: int = 180) -> bool:
    """
    True if current CT time is within the target window AND today is the 1st,
    AND we haven't already run for this YYYY-MM.
    """
    now_ct = datetime.now(CENTRAL_TZ)
    if now_ct.day != 1:
        return False

    ym = now_ct.strftime("%Y-%m")
    if _load_last_run_month() == ym:
        return False

    start = now_ct.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=window_minutes)
    return start <= now_ct <= end


def force_run_enabled() -> bool:
    return os.getenv("FORCE_RUN", "").strip().lower() in {"1", "true", "yes"}


def running_in_github_actions() -> bool:
    return os.getenv("GITHUB_ACTIONS", "").strip().lower() == "true"


def _safe_central_tz():
    try:
        return ZoneInfo("America/Chicago")
    except Exception:
        return timezone(timedelta(hours=-6))


CENTRAL_TZ = _safe_central_tz()


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def canonical_url(url: str) -> str:
    url, _frag = urldefrag(url)
    return url.strip()


def clean_text(s: str, max_len: int = 320) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def title_from_url_slug(url: str, fallback: str = "Article") -> str:
    try:
        p = path(url).rstrip("/")
        if not p:
            return fallback
        slug = p.split("/")[-1]
        slug = re.sub(r"\.[a-z0-9]+$", "", slug, flags=re.I)
        slug = re.sub(r"^\d{1,2}-\d{1,2}-\d{2,4}[-_]?", "", slug)
        slug = re.sub(r"^\d{4}[-_/]\d{1,2}[-_/]\d{1,2}[-_/]?", "", slug)
        slug = slug.replace("%28", "(").replace("%29", ")")
        slug = re.sub(r"[-_]+", " ", slug)
        slug = re.sub(r"\s+", " ", slug).strip(" -_/\t\n\r")
        if not slug:
            return fallback
        return clean_text(slug[:1].upper() + slug[1:], 220)
    except Exception:
        return fallback


def _extract_xml_locs(xml_text: str) -> List[str]:
    out: List[str] = []
    if not xml_text or "<loc" not in xml_text.lower():
        return out
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
        for elem in root.iter():
            tag = (elem.tag or "")
            if tag.endswith("loc") and elem.text:
                u = canonical_url(elem.text.strip())
                if is_http_url(u):
                    out.append(u)
    except Exception:
        for m in re.finditer(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", xml_text, re.I):
            u = canonical_url(m.group(1))
            if is_http_url(u):
                out.append(u)
    seen: set[str] = set()
    dedup: List[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def _robots_sitemaps(base_url: str) -> List[str]:
    txt = polite_get(urljoin(base_url, "/robots.txt"), timeout=20)
    if not txt:
        return []
    out: List[str] = []
    for ln in txt.splitlines():
        m = re.match(r"\s*Sitemap:\s*(https?://\S+)", ln, re.I)
        if m:
            out.append(canonical_url(m.group(1)))
    seen: set[str] = set()
    dedup: List[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def sitemap_links_for_source(source: str) -> List[Tuple[str, str, Optional[datetime]]]:
    """Source-specific sitemap fallback used only when the normal listing page is thin/blocked."""
    cfg = {
        "FASB": {
            "base": "https://www.fasb.org",
            "fallback_sitemaps": ["https://www.fasb.org/sitemap.xml"],
            "allow_re": re.compile(r"/news-and-meetings/in-the-news/(?!$)[^/?#]+", re.I),
            "deny_re": re.compile(r"/news-and-meetings/in-the-news/?$", re.I),
            "title_fallback": "FASB In the News",
            "date_from_url_re": None,
        },
        "Senate Banking": {
            "base": "https://www.banking.senate.gov",
            "fallback_sitemaps": ["https://www.banking.senate.gov/sitemap.xml"],
            "allow_re": re.compile(r"/(?:newsroom/(?:majority|minority)(?:-press-releases)?(?:/\d{2}/\d{2}/\d{4})?/[^/?#]+|news/[^/?#]+)", re.I),
            "deny_re": re.compile(r"/(?:newsroom|newsroom/press-release-archive|newsroom/photos|newsroom/videos|newsroom/in-the-news|news(?:/press-releases)?)/?$", re.I),
            "title_fallback": "Senate Banking news",
            "date_from_url_re": re.compile(r"/(\d{2})/(\d{2})/(\d{4})/"),
        },
    }.get(source)
    if not cfg:
        return []

    sitemap_urls = _robots_sitemaps(cfg["base"]) or list(cfg["fallback_sitemaps"])
    if not sitemap_urls:
        return []

    discovered: List[str] = []
    seen_maps: set[str] = set()
    queue: List[str] = list(sitemap_urls)
    while queue and len(seen_maps) < 8 and len(discovered) < 2500:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        xml_text = polite_get(sm, timeout=35)
        if not xml_text:
            continue
        locs = _extract_xml_locs(xml_text)
        for loc in locs:
            if loc.endswith('.xml') and loc not in seen_maps and len(queue) < 20:
                queue.append(loc)
            else:
                discovered.append(loc)

    out: List[Tuple[str, str, Optional[datetime]]] = []
    seen_urls: set[str] = set()
    for url in discovered:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        if not allowed_for_source(source, url):
            continue
        pth = path(url)
        if cfg["deny_re"].search(pth):
            continue
        if not cfg["allow_re"].search(pth):
            continue

        dt = None
        d_re = cfg.get("date_from_url_re")
        if d_re:
            m = d_re.search(pth)
            if m:
                try:
                    mm, dd, yyyy = m.groups()
                    dt = datetime(int(yyyy), int(mm), int(dd), tzinfo=timezone.utc)
                except Exception:
                    dt = None

        title = title_from_url_slug(url, cfg["title_fallback"])
        if title.lower() in GENERIC_TITLES or len(title) < 8:
            title = cfg["title_fallback"]

        out.append((title, url, dt))
        if len(out) >= MAX_LISTING_LINKS:
            break

    return out


def is_http_url(url: str) -> bool:
    try:
        u = urlparse(url)
        return u.scheme.lower() in ("http", "https")
    except Exception:
        return False


def scheme(url: str) -> str:
    try:
        return urlparse(url).scheme.lower()
    except Exception:
        return ""


def host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def path(url: str) -> str:
    try:
        return urlparse(url).path or "/"
    except Exception:
        return "/"


def looks_like_error_html(html: str, url: str = "") -> bool:
    if not html:
        return True

    # SPECIAL CASE: Visa pages are often valid but may not include the HTML markers
    # used below (<title>/<main>/role="main"). Trust Visa HTML and allow parsing.
    if "visa.com" in (url or ""):
        return False

    s = html.lower()
    has_html = "<html" in s or "<!doctype html" in s
    has_title = "<title" in s
    has_main = "<main" in s or 'role="main"' in s

    if "<title>404" in s or "<title>page not found" in s:
        return True
    if re.search(r">(\s*)page not found(\s*)<", s):
        return True
    if re.search(r">(\s*)404(\s*)<", s) and "cloudflare" not in s:
        return True

    if has_html and (has_title or has_main):
        return False

    if ("page not found" in s or "404 not found" in s) and not has_html:
        return True

    return False


def allowed_for_source(source: str, url: str) -> bool:
    if not is_http_url(url):
        return False
    if scheme(url) in GLOBAL_DENY_SCHEMES:
        return False

    h = host(url)
    if h in GLOBAL_DENY_DOMAINS:
        return False

    rules = SOURCE_RULES.get(source, {})
    deny = set(rules.get("deny_domains", set()))
    if h in deny:
        return False

    allow_domains = rules.get("allow_domains")
    if allow_domains and h not in set(allow_domains):
        return False

    allow_paths = rules.get("allow_path_prefixes")
    if allow_paths:
        p = path(url)
        ok = any(p.startswith(pref) for pref in set(allow_paths))
        if not ok:
            return False

    return True


def parse_date(s: str, *, dayfirst: bool = False) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = dtparser.parse(str(s), fuzzy=True, dayfirst=dayfirst)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_slash_date_best(s: str) -> Optional[datetime]:
    if not s:
        return None

    now = utc_now()
    dt_dayfirst = parse_date(s, dayfirst=True)
    dt_monthfirst = parse_date(s, dayfirst=False)

    cands = [d for d in [dt_dayfirst, dt_monthfirst] if d is not None]
    if not cands:
        return None

    not_far_future = [d for d in cands if d <= (now + timedelta(days=30))]
    if len(not_far_future) == 1:
        return not_far_future[0]
    if len(not_far_future) > 1:
        return min(not_far_future, key=lambda d: abs((now - d).total_seconds()))

    return min(cands, key=lambda d: abs((now - d).total_seconds()))


def in_window(dt: datetime, start: datetime, end: datetime) -> bool:
    return start <= dt <= end


# ============================
# ✅ Proxy helper (r.jina.ai)
# ============================

def _jina_proxy_url(url: str) -> str:
    u = url.strip()
    if u.startswith("https://"):
        return "https://r.jina.ai/https://" + u[len("https://") :]
    if u.startswith("http://"):
        return "https://r.jina.ai/http://" + u[len("http://") :]
    return "https://r.jina.ai/http://" + u


def polite_get(url: str, timeout: int = 25) -> Optional[str]:
    if not is_http_url(url):
        return None

    h = host(url)
    read_timeout = timeout
    if "fanniemae.com" in h:
        read_timeout = 40
    if "federalreserve.gov" in h:
        read_timeout = 35
    if "irs.gov" in h:
        read_timeout = 35
    if "globenewswire.com" in h:
        read_timeout = 40
    if "federalregister.gov" in h:
        read_timeout = 35
    if "tcs.com" in h:
        read_timeout = 40

    try:
        time.sleep(REQUEST_DELAY_SEC)

        headers: Dict[str, str] = {}

        browser_ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )

        if "whitehouse.gov" in h:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.whitehouse.gov/",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": browser_ua,
            }

        if "globenewswire.com" in h:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.globenewswire.com/",
                "User-Agent": browser_ua,
            }

        if "ofac.treasury.gov" in h:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://ofac.treasury.gov/",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": browser_ua,
            }

        if "home.treasury.gov" in h:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://home.treasury.gov/",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": browser_ua,
            }

        if h == "usa.visa.com":
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://usa.visa.com/",
                "User-Agent": browser_ua,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }

        if h in {"www.nacha.org", "nacha.org"}:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nacha.org/news",
                "User-Agent": browser_ua,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }

        if h == "www.mastercard.com":
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.mastercard.com/",
                "User-Agent": browser_ua,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }

        if h in {"www.bis.org", "bis.org", "www.reginfo.gov", "reginfo.gov"}:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": browser_ua,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }

        # ✅ Helps some vendor sites behave more like a browser
        if h in {"ir.jackhenry.com", "www.tcs.com", "mambu.com", "www.finastra.com", "www.bankersonline.com"}:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": browser_ua,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }

        r = SESSION.get(
            url,
            headers=headers if headers else None,
            timeout=(10, read_timeout),
            allow_redirects=True,
        )

        # Additive proxy fallback for official sites that intermittently block
        # non-browser requests. Existing direct requests remain the first choice.
        if r.status_code == 403 and h in {
            "www.rd.usda.gov",
            "www.wolterskluwer.com",
            "www.tcs.com",
            "www.occ.gov",
            "occ.gov",
            "www.whitehouse.gov",
        }:
            print(f"[warn] GET 403: {url} (retrying via proxy)", flush=True)
            proxy_url = _jina_proxy_url(url)
            try:
                time.sleep(REQUEST_DELAY_SEC)
                pr = SESSION.get(
                    proxy_url,
                    headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                    timeout=(10, max(read_timeout, 45)),
                    allow_redirects=True,
                )
                if pr.status_code < 400:
                    txtp = pr.text or ""
                    if txtp.strip() and not looks_like_error_html(txtp, url):
                        return txtp
                    print(f"[warn] proxy returned empty/error-like content: {url}", flush=True)
                else:
                    print(f"[warn] proxy GET {pr.status_code}: {proxy_url}", flush=True)
            except Exception as e:
                print(f"[warn] proxy GET failed: {proxy_url} :: {e}", flush=True)
            return None

        # ✅ Mastercard: known 403 -> proxy retry
        if r.status_code == 403 and h == "www.mastercard.com":
            print(f"[warn] GET 403: {url} (retrying via proxy)", flush=True)
            proxy_url = _jina_proxy_url(url)
            try:
                time.sleep(REQUEST_DELAY_SEC)
                pr = SESSION.get(
                    proxy_url,
                    headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                    timeout=(10, max(read_timeout, 40)),
                    allow_redirects=True,
                )
                if pr.status_code < 400:
                    txtp = pr.text or ""
                    if not looks_like_error_html(txtp, url):
                        return txtp
                    else:
                        print(f"[warn] proxy returned error-like content: {url}", flush=True)
                else:
                    print(f"[warn] proxy GET {pr.status_code}: {proxy_url}", flush=True)
            except Exception as e:
                print(f"[warn] proxy GET failed: {proxy_url} :: {e}", flush=True)
            return None

        # ✅ Finastra: 403 is common -> proxy retry
        if r.status_code == 403 and h == "www.finastra.com":
            print(f"[warn] GET 403: {url} (retrying via proxy)", flush=True)
            proxy_url = _jina_proxy_url(url)
            try:
                time.sleep(REQUEST_DELAY_SEC)
                pr = SESSION.get(
                    proxy_url,
                    headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                    timeout=(10, max(read_timeout, 45)),
                    allow_redirects=True,
                )
                if pr.status_code < 400:
                    txtp = pr.text or ""
                    if not looks_like_error_html(txtp, url):
                        return txtp
                    else:
                        print(f"[warn] proxy returned error-like content: {url}", flush=True)
                else:
                    print(f"[warn] proxy GET {pr.status_code}: {proxy_url}", flush=True)
            except Exception as e:
                print(f"[warn] proxy GET failed: {proxy_url} :: {e}", flush=True)
            return None

        # ✅ FASB: the "In the News" page can return JS-light / incomplete HTML to requests.
        # Use the proxy only for FASB when the direct fetch is blocked or the page looks incomplete.
        if h in {"www.fasb.org", "fasb.org"} and (
            r.status_code == 403
            or looks_like_error_html(r.text or "", url)
            or looks_js_rendered(r.text or "")
        ):
            why = "403" if r.status_code == 403 else "incomplete html"
            print(f"[warn] GET {why}: {url} (retrying via proxy)", flush=True)
            proxy_url = _jina_proxy_url(url)
            try:
                time.sleep(REQUEST_DELAY_SEC)
                pr = SESSION.get(
                    proxy_url,
                    headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                    timeout=(10, max(read_timeout, 40)),
                    allow_redirects=True,
                )
                if pr.status_code < 400:
                    txtp = pr.text or ""
                    if txtp.strip():
                        return txtp
                    print(f"[warn] proxy returned empty content: {url}", flush=True)
                else:
                    print(f"[warn] proxy GET {pr.status_code}: {proxy_url}", flush=True)
            except Exception as e:
                print(f"[warn] proxy GET failed: {proxy_url} :: {e}", flush=True)
            return None

        # ✅ BankersOnline: 403 is common -> proxy retry
        if r.status_code == 403 and h == "www.bankersonline.com":
            print(f"[warn] GET 403: {url} (retrying via proxy)", flush=True)
            proxy_url = _jina_proxy_url(url)
            try:
                time.sleep(REQUEST_DELAY_SEC)
                pr = SESSION.get(
                    proxy_url,
                    headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                    timeout=(10, max(read_timeout, 40)),
                    allow_redirects=True,
                )
                if pr.status_code < 400:
                    txtp = pr.text or ""
                    if not looks_like_error_html(txtp, url):
                        return txtp
                    else:
                        print(f"[warn] proxy returned error-like content: {url}", flush=True)
                else:
                    print(f"[warn] proxy GET {pr.status_code}: {proxy_url}", flush=True)
            except Exception as e:
                print(f"[warn] proxy GET failed: {proxy_url} :: {e}", flush=True)
            return None

        if r.status_code >= 400:
            if h == "ir.jackhenry.com":
                print(f"[warn] GET {r.status_code}: {url} (retrying Jack Henry fallbacks)", flush=True)

                # 1) Proxy retry (r.jina.ai)
                proxy_url = _jina_proxy_url(url)
                try:
                    time.sleep(REQUEST_DELAY_SEC)
                    pr = SESSION.get(
                        proxy_url,
                        headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                        timeout=(10, max(read_timeout, 45)),
                        allow_redirects=True,
                    )
                    if pr.status_code < 400:
                        txtp = pr.text or ""
                        if txtp.strip():
                            return txtp
                    else:
                        print(f"[warn] proxy GET {pr.status_code}: {proxy_url}", flush=True)
                except Exception as e2:
                    print(f"[warn] proxy GET failed: {proxy_url} :: {e2}", flush=True)

                # 2) Fallback to shareholder.com listing (often more reliable than ir.jackhenry.com)
                try:
                    fallback = "https://jkhy.client.shareholder.com/press-releases?mobile=1&view=all"
                    print(f"[warn] Jack Henry fallback listing: {fallback}", flush=True)
                    time.sleep(REQUEST_DELAY_SEC)
                    fr = SESSION.get(
                        fallback,
                        headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                        timeout=(10, max(read_timeout, 45)),
                        allow_redirects=True,
                    )
                    if fr.status_code < 400:
                        txtf = fr.text or ""
                        if txtf.strip() and not looks_like_error_html(txtf, fallback):
                            return txtf
                    else:
                        print(f"[warn] fallback GET {fr.status_code}: {fallback}", flush=True)
                except Exception as e3:
                    print(f"[warn] fallback GET failed: {fallback} :: {e3}", flush=True)

            print(f"[warn] GET {r.status_code}: {url}", flush=True)
            return None

        txt = r.text or ""
        if looks_like_error_html(txt, url):
            print(f"[warn] looks-like-error HTML: {url}", flush=True)
            return None

        return txt
    except Exception as e:
        # Additive timeout/connection fallback for selected official sites. This
        # does not replace or alter the normal direct request path.
        if h in {
            "www.rd.usda.gov",
            "www.wolterskluwer.com",
            "www.tcs.com",
            "www.occ.gov",
            "occ.gov",
            "www.whitehouse.gov",
        }:
            print(f"[warn] GET failed: {url} :: {e} (retrying via proxy)", flush=True)
            proxy_url = _jina_proxy_url(url)
            try:
                time.sleep(REQUEST_DELAY_SEC)
                pr = SESSION.get(
                    proxy_url,
                    headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                    timeout=(10, max(read_timeout, 45)),
                    allow_redirects=True,
                )
                if pr.status_code < 400:
                    txtp = pr.text or ""
                    if txtp.strip() and not looks_like_error_html(txtp, url):
                        return txtp
                else:
                    print(f"[warn] proxy GET {pr.status_code}: {proxy_url}", flush=True)
            except Exception as e2:
                print(f"[warn] proxy GET failed: {proxy_url} :: {e2}", flush=True)
            return None

        # Retry via r.jina.ai proxy, and if that still fails, fall back to the shareholder.com
        # mirror of the same IR press-release listing. This change is *only* for Jack Henry.
        if h == "ir.jackhenry.com":
            print(f"[warn] GET failed: {url} :: {e}", flush=True)

            # 1) Proxy retry (r.jina.ai)
            try:
                print(f"[warn] Jack Henry GET failed (retrying via proxy): {url}", flush=True)
                proxy_url = _jina_proxy_url(url)
                time.sleep(REQUEST_DELAY_SEC)
                pr = SESSION.get(
                    proxy_url,
                    headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                    timeout=(10, max(read_timeout, 45)),
                    allow_redirects=True,
                )
                if pr.status_code < 400:
                    txtp = pr.text or ""
                    if txtp.strip():
                        return txtp
                else:
                    print(f"[warn] proxy GET {pr.status_code}: {proxy_url}", flush=True)
            except Exception as e2:
                print(f"[warn] proxy GET failed: {proxy_url} :: {e2}", flush=True)

            # 2) Fallback to shareholder.com listing (often more reliable than ir.jackhenry.com)
            try:
                fallback = "https://jkhy.client.shareholder.com/press-releases?mobile=1&view=all"
                print(f"[warn] Jack Henry fallback listing: {fallback}", flush=True)
                time.sleep(REQUEST_DELAY_SEC)
                fr = SESSION.get(
                    fallback,
                    headers={"User-Agent": browser_ua, "Accept": "text/html,application/xhtml+xml,*/*"},
                    timeout=(10, max(read_timeout, 45)),
                    allow_redirects=True,
                )
                if fr.status_code < 400:
                    txtf = fr.text or ""
                    if txtf.strip() and not looks_like_error_html(txtf, fallback):
                        return txtf
                else:
                    print(f"[warn] fallback GET {fr.status_code}: {fallback}", flush=True)
            except Exception as e3:
                print(f"[warn] fallback GET failed: {fallback} :: {e3}", flush=True)

            return None

        print(f"[warn] GET failed: {url} :: {e}", flush=True)
        return None


def fetch_bytes(url: str, timeout: int = 25) -> Optional[bytes]:
    if not is_http_url(url):
        return None
    try:
        time.sleep(REQUEST_DELAY_SEC)
        h = host(url)
        headers = None
        if h in {"www.bis.org", "bis.org", "www.reginfo.gov", "reginfo.gov"}:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "application/xml,text/xml,application/rss+xml,text/html;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        r = SESSION.get(url, headers=headers, timeout=(10, timeout), allow_redirects=True)
        if r.status_code >= 400:
            print(f"[warn] GET {r.status_code}: {url}", flush=True)
            return None
        return r.content
    except Exception as e:
        print(f"[warn] GET failed: {url} :: {e}", flush=True)
        return None


def fetch_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 35,
) -> Optional[Dict[str, Any]]:
    try:
        time.sleep(REQUEST_DELAY_SEC)
        r = SESSION.get(
            url,
            params=params or {},
            headers={"Accept": "application/json"},
            timeout=(10, timeout),
            allow_redirects=True,
        )
        if r.status_code >= 400:
            print(f"[warn] JSON GET {r.status_code}: {r.url}", flush=True)
            return None
        try:
            return r.json()
        except Exception:
            preview = (r.text or "")[:300].replace("\n", " ")
            print(f"[warn] JSON parse failed: {r.url} :: preview={preview}", flush=True)
            return None
    except Exception as e:
        print(f"[warn] JSON GET failed: {url} :: {e}", flush=True)
        return None


def fetch_json_status(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 35,
) -> Tuple[Optional[Dict[str, Any]], int, str]:
    try:
        time.sleep(REQUEST_DELAY_SEC)
        r = SESSION.get(
            url,
            params=params or {},
            headers={"Accept": "application/json"},
            timeout=(10, timeout),
            allow_redirects=True,
        )
        final_url = r.url
        status = int(getattr(r, "status_code", 0) or 0)

        if status >= 400:
            return None, status, final_url

        try:
            return r.json(), status, final_url
        except Exception:
            return None, 0, final_url
    except Exception:
        return None, 0, url


# ============================
# MONTH WINDOW (previous calendar month in CT)
# ============================

def monthly_window_utc(now_utc: datetime) -> Tuple[datetime, datetime, datetime]:
    """
    Returns (window_start_utc, window_end_utc, target_month_start_ct)
    for the previous calendar month in Central Time.

    RegMonthly intentionally uses a one-day buffer on both sides of the
    prior-month pull so late-posted items are not missed. Example: a May 1 run
    for April uses March 31 00:00 CT through May 1 00:00 CT.

    The third return value remains the actual target month start so source
    URLs that need a calendar month, such as IRS monthly archives, still point
    to the intended month rather than the buffer day.
    """
    now_ct = now_utc.astimezone(CENTRAL_TZ)

    first_of_this_month_ct = now_ct.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    target_month_start_ct = (first_of_this_month_ct - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    buffered_start_ct = target_month_start_ct - timedelta(days=1)
    buffered_end_ct = first_of_this_month_ct

    return (
        buffered_start_ct.astimezone(timezone.utc),
        buffered_end_ct.astimezone(timezone.utc),
        target_month_start_ct,
    )


def irs_news_releases_for_month_url(window_start_ct: datetime) -> str:
    month = window_start_ct.strftime("%B").lower()
    year = window_start_ct.year
    return f"https://www.irs.gov/newsroom/news-releases-for-{month}-{year}"



# ============================
# DATE PATTERNS
# ============================

MONTH_DATE_RE = re.compile(r"(?P<md>\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+\d{4}\b)", re.I)
DAY_MONTH_DATE_RE = re.compile(r"(?P<dmy>\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{4}\b)", re.I)
SLASH_DATE_RE = re.compile(r"(?P<sd>\b\d{1,2}/\d{1,2}/\d{2,4}\b)")
ISO_DATE_RE = re.compile(r"(?P<id>\b\d{4}-\d{2}-\d{2}\b)")

DAYFIRST_SOURCES: set[str] = set()


def extract_any_date(text: str, source: str = "") -> Optional[datetime]:
    if not text:
        return None

    m = MONTH_DATE_RE.search(text)
    if m:
        dt = parse_date(m.group("md"))
        if dt:
            return dt

    m = DAY_MONTH_DATE_RE.search(text)
    if m:
        dt = parse_date(m.group("dmy"), dayfirst=True)
        if dt:
            return dt

    m = SLASH_DATE_RE.search(text)
    if m:
        sd = m.group("sd")
        if source == "Visa":
            dt = parse_slash_date_best(sd)
        else:
            dt = parse_date(sd, dayfirst=(source in DAYFIRST_SOURCES))
        if dt:
            return dt

    m = ISO_DATE_RE.search(text)
    if m:
        dt = parse_date(m.group("id"))
        if dt:
            return dt

    return None


# ============================
# NAV / PAGINATION / GENERIC LINK FILTERS
# ============================

NAV_TITLE_RE = re.compile(
    r"^\s*(home|current page|page\s*\d+|next|previous|prev|older|newer|"
    r"first|last|back|top|menu|breadcrumb|view all|all|show more|load more)\s*$",
    re.I,
)

GENERIC_TITLES = {
    "home",
    "news",
    "newsroom",
    "press releases",
    "press release",
    "recent postings",
    "date",
    "investor relations",
    "supervision & examination",
    "economics",
    "consumers & communities",
    "general licenses",
    "miscellaneous",
    "read more",
    "learn more",

    # Generic section/listing labels. These sometimes get captured from agency
    # listing pages with the same date/time as the real article directly below
    # them. They should not be published as monthly articles.
    "readouts",
    "speeches",
    "speech",
    "statements",
    "statement",
    "remarks and statements",
    "resources",
    "events",
    "event",
    "fact sheets",
    "fact sheet",
}


def is_probably_nav_link(source: str, title: str, url: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True

    if NAV_TITLE_RE.match(t):
        return True

    if re.fullmatch(r"[\d]+", t):
        return True
    if re.fullmatch(r"[«»‹›→←]+", t):
        return True

    u = urlparse(url)
    q = parse_qs(u.query or "")

    if any(k in q for k in ["page", "p", "start", "offset"]):
        if NAV_TITLE_RE.search(t) or re.fullmatch(r"\d+", t):
            return True

    if source == "OFAC":
        if "page" in q:
            return True
        if u.path.rstrip("/").endswith("/recent-actions"):
            return True
        if u.path.rstrip("/").endswith("/recent-actions/enforcement-actions"):
            return True

    if source == "White House":
        if t.lower() in {"all", "featured", "news", "gallery", "livestream", "contact"}:
            return True

    # OCC pages sometimes include non-article CTA links titled 'More' / 'More More'
    if source == "OCC":
        tl = t.strip().lower()
        if tl in {"more", "more more", "moremore"}:
            return True
    # NACHA: exclude non-article /news hub and pager/filter/topic links that sometimes get captured as "articles"
    if source == "NACHA":
        try:
            up = urlparse(url)
            p = (up.path or "").rstrip("/")
            qraw = up.query or ""
            q = parse_qs(qraw)

            # Keep only real article pages like /news/<slug>
            if not p.startswith("/news/"):
                return True

            # Drop anything with query parameters (except harmless utm_ tracking)
            if qraw:
                non_utm = [k for k in q.keys() if not k.lower().startswith("utm_")]
                if non_utm:
                    return True

            # Drop known pager/filter keys even if title looks legitimate
            if any(k in q for k in ["page", "p", "start", "offset", "sort", "filter", "category", "topic", "tags", "year", "month"]):
                return True
        except Exception:
            pass
    return False


def is_generic_listing_or_home(source: str, title: str, url: str) -> bool:
    tl = (title or "").strip().lower()
    if tl in GENERIC_TITLES:
        return True

    # NACHA: treat /news hub as non-article
    if source == "NACHA":
        try:
            u0 = urlparse(url)
            if (u0.path or "").rstrip("/") == "/news":
                return True
        except Exception:
            pass

    u = urlparse(url)
    p = (u.path or "/").rstrip("/")

    if p == "":
        return True

    for hub in ["/newsroom", "/news", "/press-releases", "/pressreleases", "/media-room", "/media", "/about-us"]:
        if p.endswith(hub):
            return True

    if source == "USDA Rural Development":
        pl = p.lower()
        if pl.startswith("/bulletins/"):
            return False
        if pl.startswith("/accounts/usdard/bulletins"):
            return False
        if any(x in pl for x in ["/subscriptions/", "/subscriber/", "/preferences/"]):
            return True

    if source == "Freddie Mac":
        pl = p.lower()
        if pl.startswith("/search/organization/"):
            return False
        if pl.startswith("/en/search/organization/"):
            return False
        if pl.startswith("/news-release/"):
            return False
        if pl.startswith("/en/news-release/"):
            return False

    if source == "Treasury":
        pl = p.lower()
        treasury_listing_paths = {
            "/news/press-releases/readouts",
            "/news/press-releases/statements",
            "/news/press-releases/speeches",
            "/news/press-releases/testimony",
        }
        if pl in treasury_listing_paths:
            return True
        if tl in {"readouts", "statements", "speeches", "testimony"}:
            return True

    if source == "Mastercard":
        if p.endswith("/news-and-trends/press"):
            return False

    if source == "FHLB MPF":
        if p.endswith("/program-guidelines/mpf-program-updates"):
            return False

    return False


# ============================
# FEED DETECTION + DISCOVERY
# ============================

FEED_SUFFIX_RE = re.compile(r"(\.rss|\.xml|\.atom)$", re.I)


def looks_like_feed_url(url: str) -> bool:
    u = url.strip()
    if not is_http_url(u):
        return False
    p = path(u).lower()
    if FEED_SUFFIX_RE.search(p):
        return True
    if p.endswith("/feed") or p.endswith("/feed/"):
        return True
    q = (urlparse(u).query or "").lower()
    if "output=atom" in q:
        return True
    return False


def discover_feeds(page_url: str, html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    feeds: List[str] = []

    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel", [])).lower()
        typ = (link.get("type") or "").lower()
        href = link.get("href")
        if not href:
            continue
        if "alternate" in rel and ("rss" in typ or "atom" in typ or href.lower().endswith((".xml", ".rss", ".atom"))):
            feeds.append(urljoin(page_url, href))

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.lower().endswith((".xml", ".rss", ".atom")):
            feeds.append(urljoin(page_url, href))

    out: List[str] = []
    seen = set()
    for f in feeds:
        f = canonical_url(f)
        if f not in seen and looks_like_feed_url(f):
            seen.add(f)
            out.append(f)
    return out


def items_from_feed(source: str, feed_url: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    b = fetch_bytes(feed_url, timeout=40)
    if not b:
        return out

    fp = feedparser.parse(b)
    if getattr(fp, "bozo", 0):
        bozo_ex = getattr(fp, "bozo_exception", None)
        if bozo_ex:
            print(f"[warn] feed bozo: {feed_url} :: {bozo_ex}", flush=True)

    for e in fp.entries:
        title = clean_text(e.get("title", ""), 220)
        link = (e.get("link") or "").strip()
        if not title or not link:
            continue

        url = canonical_url(link)
        if not allowed_for_source(source, url):
            continue
        if is_probably_nav_link(source, title, url):
            continue
        if is_generic_listing_or_home(source, title, url):
            continue

        dt = None
        if e.get("published"):
            dt = parse_date(e.get("published"))
        elif e.get("updated"):
            dt = parse_date(e.get("updated"))
        elif e.get("published_parsed"):
            try:
                dt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            except Exception:
                dt = None

        if not dt or not in_window(dt, start, end):
            continue

        summary = ""
        if e.get("summary"):
            summary = clean_text(BeautifulSoup(e["summary"], "html.parser").get_text(" ", strip=True), 380)

        out.append(
            {
                "category": CATEGORY_BY_SOURCE.get(source, source),
                "source": source,
                "title": title,
                "published_at": iso_z(dt),
                "url": url,
                "summary": summary,
            }
        )

    return out



# ============================
# BIS ARCHIVE / SITEMAP BACKFILL
# ============================

BIS_SITEMAP_URL = "https://www.bis.org/sitemap.xml"
BIS_SITEMAP_MAX_FILES = 40
BIS_SITEMAP_DETAIL_CAP = 1800

# The all-site BIS RSS feed is intentionally a rolling "what's new" feed, not
# a historical month archive. RegMonthly runs after the target month has ended,
# so a prior-month build can legitimately find zero target-month entries in RSS.
# The sitemap backfill uses sitemap last-modified dates only to select candidates,
# then confirms the actual publication date from each BIS detail page.
BIS_STATIC_PATH_RE = re.compile(
    r"^/(?:$|index(?:\.htm)?$|about/?$|about/index\.htm$|rss/|search/|doclist/|"
    r"terms_conditions|privacy|contact|careers|sitemap)",
    re.I,
)


def _xml_child_text(elem: Any, wanted_local_name: str) -> str:
    wanted = wanted_local_name.lower()
    for child in list(elem):
        if _xml_local_name(getattr(child, "tag", "")).lower() == wanted:
            return clean_text(" ".join(child.itertext()), 2000)
    return ""


def _bis_title_from_html(url: str, html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for attrs in [
        {"property": "og:title"},
        {"name": "twitter:title"},
    ]:
        m = soup.find("meta", attrs=attrs)
        if m and m.get("content"):
            t = clean_text(str(m.get("content")), 320)
            if t:
                return re.sub(r"\s*[|\-–—]\s*Bank for International Settlements\s*$", "", t, flags=re.I).strip()
    h1 = soup.find("h1")
    if h1:
        t = clean_text(h1.get_text(" ", strip=True), 320)
        if t:
            return t
    if soup.title:
        t = clean_text(soup.title.get_text(" ", strip=True), 320)
        t = re.sub(r"\s*[|\-–—]\s*Bank for International Settlements\s*$", "", t, flags=re.I).strip()
        if t:
            return t
    return title_from_url_slug(url, "BIS update")


def _bis_sitemap_candidates(start: datetime, end: datetime) -> List[Tuple[str, Optional[datetime]]]:
    """Return BIS URLs whose sitemap lastmod is near the target month.

    lastmod is a discovery hint only. Actual inclusion still requires a publication
    date parsed from the page and inside RegMonthly's date window.
    """
    # BIS supports year-scoped sitemap URLs (e.g. ?documents=2026). Using the
    # target year avoids crawling the entire historical site just to rebuild one month.
    years = range(start.year, end.year + 1)
    queue: List[str] = [f"{BIS_SITEMAP_URL}?documents={y}" for y in years]
    seen_maps: set[str] = set()
    candidates: List[Tuple[str, Optional[datetime]]] = []
    seen_urls: set[str] = set()

    # Give sitemap timestamps a little room for timezone/republishing differences.
    hint_start = start - timedelta(days=3)
    hint_end = end + timedelta(days=3)

    while queue and len(seen_maps) < BIS_SITEMAP_MAX_FILES:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        raw = fetch_bytes(sm, timeout=45)
        if not raw:
            print(f"[warn] BIS sitemap unavailable: {sm}", flush=True)
            continue
        try:
            root = ET.fromstring(raw)
        except Exception as e:
            print(f"[warn] BIS sitemap XML parse failed: {sm} :: {e}", flush=True)
            continue

        root_name = _xml_local_name(getattr(root, "tag", "")).lower()
        if root_name == "sitemapindex":
            for node in list(root):
                loc = _xml_child_text(node, "loc")
                if loc and loc not in seen_maps and len(queue) < BIS_SITEMAP_MAX_FILES * 2:
                    queue.append(loc)
            continue

        for node in list(root):
            if _xml_local_name(getattr(node, "tag", "")).lower() != "url":
                continue
            loc = canonical_url(_xml_child_text(node, "loc"))
            if not loc or loc in seen_urls or not allowed_for_source("BIS", loc):
                continue
            seen_urls.add(loc)

            pth = path(loc)
            if BIS_STATIC_PATH_RE.search(pth):
                continue
            # Detail/article pages on BIS are overwhelmingly .htm/.html. Keeping
            # this tight avoids crawling PDFs, media, datasets and static assets.
            if not re.search(r"\.html?$", pth, re.I):
                continue

            lastmod_raw = _xml_child_text(node, "lastmod")
            lastmod = parse_date(lastmod_raw) if lastmod_raw else None
            if lastmod is not None and not (hint_start <= lastmod <= hint_end):
                continue

            # BIS year sitemaps may omit lastmod. Many speech/press URLs encode
            # YYMMDD; use that only as a discovery hint so old speeches don't
            # consume the detail-fetch budget. Actual inclusion is still based on
            # the publication date read from the page.
            if lastmod is None:
                m = re.search(r"/(?:review|press)/[a-z]*?(\d{2})(\d{2})(\d{2})[a-z0-9_-]*\.html?$", pth, re.I)
                if m:
                    try:
                        hinted = datetime(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
                    except Exception:
                        hinted = None
                    if hinted is not None and not (hint_start <= hinted <= hint_end):
                        continue
                    lastmod = hinted
                else:
                    # Some BIS statistical filenames encode YYMM without a day.
                    m2 = re.search(r"(?:^|[^0-9])(\d{2})(\d{2})(?:[^0-9]|$)", pth.rsplit('/', 1)[-1])
                    if m2:
                        try:
                            yy, mm = 2000 + int(m2.group(1)), int(m2.group(2))
                            if yy in range(start.year, end.year + 1) and 1 <= mm <= 12:
                                if mm not in {start.month, end.month}:
                                    continue
                        except Exception:
                            pass

            candidates.append((loc, lastmod))

    candidates.sort(key=lambda x: x[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return candidates[:BIS_SITEMAP_DETAIL_CAP]


def items_from_bis_archive(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Backfill BIS items for the target month from the official sitemap."""
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    candidates = _bis_sitemap_candidates(start, end)
    print(f"[sitemap] BIS candidates: {len(candidates)}", flush=True)

    for url, _lastmod in candidates:
        html = polite_get(url, timeout=35)
        if not html:
            continue
        dt, snippet = extract_published_from_detail(url, html, source="BIS")
        if dt is None or not in_window(dt, start, end):
            continue
        title = _bis_title_from_html(url, html)
        if not title or is_generic_listing_or_home("BIS", title, url) or is_probably_nav_link("BIS", title, url):
            continue
        key = canonical_dedupe_url(url)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "category": CATEGORY_BY_SOURCE.get("BIS", "International Banking"),
            "source": "BIS",
            "title": title,
            "published_at": iso_z(dt),
            "url": url,
            "summary": snippet,
        })

    print(f"[sitemap] BIS dated target-month items: {len(out)}", flush=True)
    return out


# ============================
# FEDERAL REGISTER API ITEMS
# ============================

def _fedreg_params_for_filter(
    kind: str,
    value: str,
    start_d: str,
    end_d: str,
    page: int,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "per_page": 200,
        "page": page,
        "order": "newest",
        "conditions[publication_date][gte]": start_d,
        "conditions[publication_date][lte]": end_d,
        "fields[]": [
            "title",
            "publication_date",
            "html_url",
            "document_number",
            "type",
            "abstract",
            "agencies",
        ],
    }

    if kind == "topics":
        params["conditions[topics][]"] = value
    elif kind == "agencies":
        params["conditions[agencies][]"] = value
    elif kind == "sections":
        params["conditions[sections][]"] = value
    else:
        params["conditions[term]"] = value

    return params




def _fedreg_params_all(start_d: str, end_d: str, page: int) -> Dict[str, Any]:
    """Federal Register query for the main dashboard: date window only."""
    return {
        "per_page": 200,
        "page": page,
        "order": "newest",
        "conditions[publication_date][gte]": start_d,
        "conditions[publication_date][lte]": end_d,
        "fields[]": [
            "title",
            "publication_date",
            "html_url",
            "document_number",
            "type",
            "abstract",
            "agencies",
        ],
    }


def items_from_federal_register_all(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Pull every dated Federal Register document in the dashboard window.

    The main dashboard must not pre-qualify Federal Register documents by banking
    topic, agency, section, or keyword. Relevance filtering belongs in the Smart
    Index only. Agency tags are retained solely for optional UI filtering.
    """
    start_d = start.date().isoformat()
    end_d = end.date().isoformat()
    endpoint = f"{FEDREG_API_BASE.rstrip('/')}/documents.json"

    by_doc: Dict[str, Dict[str, Any]] = {}
    page = 1

    while True:
        params = _fedreg_params_all(start_d, end_d, page)
        j, status, final_url = fetch_json_status(endpoint, params=params, timeout=45)
        if not j:
            if status >= 400:
                print(f"[warn] Federal Register JSON GET {status}: {final_url}", flush=True)
            break

        results = j.get("results") or []
        if not isinstance(results, list) or not results:
            break

        for r in results:
            try:
                title = clean_text(str(r.get("title") or ""), 220)
                pub_s = str(r.get("publication_date") or "").strip()
                url = str(r.get("html_url") or "").strip()
                docnum = str(r.get("document_number") or "").strip()
                if not title or not pub_s or not url:
                    continue

                dt = parse_date(pub_s)
                if not dt or not in_window(dt, start, end):
                    continue

                if url.startswith("/"):
                    url = "https://www.federalregister.gov" + url
                url = canonical_url(url)
                if not allowed_for_source("Federal Register", url):
                    continue

                abstract = clean_text(str(r.get("abstract") or ""), 380)
                agencies = r.get("agencies")
                agency_tags = _fedreg_agency_tags(agencies)
                doc_type = normalize_fedreg_slug(str(r.get("type") or ""))
                tags = list(agency_tags)
                if doc_type:
                    tags.append(f"type:{doc_type}")

                key = docnum or url
                existing = by_doc.get(key)
                if not existing:
                    by_doc[key] = {
                        "category": "Federal Register",
                        "source": "Federal Register",
                        "title": title,
                        "published_at": iso_z(dt),
                        "url": url,
                        "summary": abstract,
                        "fr_tags": sorted(set(tags)),
                        "fedreg_document_number": docnum,
                    }
                else:
                    existing["fr_tags"] = sorted(set(existing.get("fr_tags") or []) | set(tags))
                    if not existing.get("summary") and abstract:
                        existing["summary"] = abstract
            except Exception:
                continue

        total_pages = 0
        try:
            total_pages = int(j.get("total_pages") or 0)
        except Exception:
            total_pages = 0

        if total_pages and page >= total_pages:
            break
        if len(results) < 200 and not j.get("next_page_url"):
            break

        page += 1
        if page > 200:
            print("[warn] Federal Register all-documents pagination exceeded 200 pages; stopping defensively.", flush=True)
            break

    out = list(by_doc.values())
    out.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    print(f"[api] Federal Register all documents: {len(out)} unique dated docs", flush=True)
    return out


def _fedreg_pretty_slug(s: str) -> str:
    # "truth-lending" -> "Truth Lending"
    s = (s or "").strip()
    s = s.replace("_", "-")
    s = re.sub(r"-{2,}", "-", s)
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.title() if s else ""


def _fedreg_group_from_agencies(agencies: Any) -> Optional[str]:
    # agencies is typically a list of dicts like {"id":..., "name":..., "slug":...}
    try:
        if not agencies or not isinstance(agencies, list):
            return None
        names: List[str] = []
        for a in agencies:
            if not isinstance(a, dict):
                continue
            nm = str(a.get("name") or "").strip()
            if nm:
                names.append(nm)
        if not names:
            return None
        # Keep it readable (avoid 6+ agencies blowing up the UI)
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} + {names[1]}"
        return f"{names[0]} + {names[1]} +{len(names)-2}"
    except Exception:
        return None


def _fedreg_group_label(kind: str, value: str, agencies: Any) -> Tuple[str, str]:
    """
    Returns (group_type, group_label)
    group_type: "agency" | "topic" | "section" | "filter"
    """
    agency_label = _fedreg_group_from_agencies(agencies)
    if agency_label:
        return "agency", agency_label

    k = (kind or "").strip().lower()
    v = (value or "").strip()
    pretty = _fedreg_pretty_slug(v)

    if k == "topics":
        return "topic", (pretty or v)
    if k == "agencies":
        return "agency", (pretty or v)
    if k == "sections":
        return "section", (pretty or v)

    return "filter", (pretty or v or "Federal Register")


def _fedreg_source_for_group(group_label: str) -> str:
    gl = (group_label or "").strip()
    if not gl:
        return "Federal Register"
    return f"Federal Register • {gl}"


def _fedreg_kind_singular(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k.endswith("s"):
        k = k[:-1]
    if k in {"topic", "agency", "section"}:
        return k
    return "filter"


def _fedreg_tag(kind: str, value: str) -> str:
    k = _fedreg_kind_singular(kind)
    v = normalize_fedreg_slug(value or "")
    return f"{k}:{v}" if v else k


def _fedreg_agency_tags(agencies: Any) -> List[str]:
    tags: List[str] = []
    try:
        if not agencies or not isinstance(agencies, list):
            return tags
        for a in agencies:
            if not isinstance(a, dict):
                continue
            slug = normalize_fedreg_slug(str(a.get("slug") or ""))
            if slug:
                tags.append(f"agency:{slug}")
    except Exception:
        return tags
    # de-dupe while preserving order
    out: List[str] = []
    seen: set[str] = set()
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def items_from_federal_register_topics(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """
    Pull Federal Register documents via API across a set of topic/agency/section filters.

    IMPORTANT for the frontend filter chips (index.html):
    - source must be exactly "Federal Register"
    - each item must include `fr_tags: [ "topic:...", "agency:...", "section:..." ]`
    """
    start_d = start.date().isoformat()
    end_d = end.date().isoformat()
    endpoint = f"{FEDREG_API_BASE.rstrip('/')}/documents.json"

    # Deduplicate by document_number but *merge* tags from multiple filters.
    by_doc: Dict[str, Dict[str, Any]] = {}

    for f in FEDREG_FILTERS:
        kind = f["kind"]
        value = f["value"]
        filter_tag = _fedreg_tag(kind, value)

        page = 1
        total_unique_touched = 0
        tried_fallback = False

        while True:
            params = _fedreg_params_for_filter(kind, value, start_d, end_d, page)

            j, status, final_url = fetch_json_status(endpoint, params=params, timeout=45)

            # Some combinations can 400. Try alternate condition keys.
            if j is None and status == 400 and not tried_fallback:
                tried_fallback = True

                fallbacks: List[str] = []
                if kind == "topics":
                    fallbacks = ["sections", "agencies", "term"]
                elif kind == "sections":
                    fallbacks = ["topics", "agencies", "term"]
                elif kind == "agencies":
                    fallbacks = ["topics", "sections", "term"]
                else:
                    fallbacks = ["topics", "sections", "agencies"]

                fixed = False
                for nk in fallbacks:
                    params2 = _fedreg_params_for_filter(nk, value, start_d, end_d, page)
                    j2, status2, _u2 = fetch_json_status(endpoint, params=params2, timeout=45)
                    if j2 is not None and status2 < 400:
                        kind = nk
                        filter_tag = _fedreg_tag(kind, value)
                        j = j2
                        status = status2
                        fixed = True
                        break

                if not fixed:
                    print(
                        f"[warn] Federal Register filter '{value}' failed (400) for kinds tried; last={final_url}",
                        flush=True,
                    )
                    break

            if not j:
                if status >= 400 and status != 400:
                    print(f"[warn] Federal Register JSON GET {status}: {final_url}", flush=True)
                break

            results = j.get("results") or []
            if not isinstance(results, list) or len(results) == 0:
                break

            for r in results:
                try:
                    title = clean_text(str(r.get("title") or ""), 220)
                    pub_s = str(r.get("publication_date") or "").strip()
                    url = str(r.get("html_url") or "").strip()
                    docnum = str(r.get("document_number") or "").strip()

                    if not title or not pub_s or not url:
                        continue

                    dt = parse_date(pub_s)
                    if not dt or not in_window(dt, start, end):
                        continue

                    if url.startswith("/"):
                        url = "https://www.federalregister.gov" + url
                    url = canonical_url(url)

                    if not allowed_for_source("Federal Register", url):
                        continue

                    abstract = clean_text(str(r.get("abstract") or ""), 380)

                    agencies = r.get("agencies")
                    agency_tags = _fedreg_agency_tags(agencies)

                    # Key: prefer doc number, else fall back to URL.
                    key = docnum or url

                    existing = by_doc.get(key)
                    if not existing:
                        by_doc[key] = {
                            "category": "Federal Register",
                            "source": "Federal Register",
                            "title": title,
                            "published_at": iso_z(dt),
                            "url": url,
                            "summary": abstract,
                            # frontend filter chips read this
                            "fr_tags": sorted(set([filter_tag] + agency_tags)),
                            # useful debug metadata
                            "fedreg_document_number": docnum,
                        }
                        total_unique_touched += 1
                    else:
                        # Merge tags if the same doc is hit by multiple filters.
                        tags = set(existing.get("fr_tags") or [])
                        tags.add(filter_tag)
                        tags.update(agency_tags)
                        existing["fr_tags"] = sorted(tags)

                        # Prefer a non-empty summary.
                        if not existing.get("summary") and abstract:
                            existing["summary"] = abstract
                except Exception:
                    continue

            page += 1
            if page > 20:
                break

        print(
            f"[api] Federal Register {_fedreg_kind_singular(kind)} '{value}': touched {total_unique_touched} unique docs",
            flush=True,
        )

    # Return newest-first like other tiles
    out = list(by_doc.values())
    out.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return out

# ============================
# DETAIL PAGE EXTRACTION
# ============================


def extract_fdic_published_from_detail(detail_url: str, html: str) -> Optional[datetime]:
    """FDIC-only detail date parser.

    FDIC press-release pages can include unrelated listing/sidebar dates before
    the real article date. Do not use the first arbitrary page date for FDIC.
    Prefer the date in the article body near the H1/title, then the
    "Last Updated" footer date, then conservative metadata fallbacks.
    """
    soup = BeautifulSoup(html, "html.parser")

    def _date_from_text(text: str) -> Optional[datetime]:
        if not text:
            return None
        # Prefer full written dates like April 7, 2026. This avoids grabbing
        # unrelated numeric/template dates from navigation or listing widgets.
        for m in re.finditer(
            r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b",
            text,
            re.I,
        ):
            dt = parse_date(m.group(0))
            if dt:
                return dt
        return None

    # 1) Look in the main/article area near the H1/title.
    main = soup.find("main") or soup.find("article") or soup
    h1 = main.find("h1") if getattr(main, "find", None) else None
    if h1:
        chunks: List[str] = []
        for sib in h1.find_all_next(limit=18):
            if getattr(sib, "name", "") in {"script", "style", "nav", "footer"}:
                continue
            txt = clean_text(sib.get_text(" ", strip=True) if getattr(sib, "get_text", None) else "", 240)
            if txt:
                chunks.append(txt)
            if len(" ".join(chunks)) > 1200:
                break
        dt = _date_from_text(" ".join(chunks))
        if dt:
            return dt

    # 2) Look for explicit FDIC update label, which is usually the article date
    # on FDIC detail pages when the body date is not available.
    page_text = clean_text((main or soup).get_text(" ", strip=True), 8000)
    m = re.search(
        r"Last\s+Updated[:\s]+((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4})",
        page_text,
        re.I,
    )
    if m:
        dt = parse_date(m.group(1))
        if dt:
            return dt

    # 3) Conservative metadata fallback for FDIC only. Avoid generic first-date
    # page scanning because FDIC templates can expose unrelated dates first.
    for k, v in [
        ("property", "article:published_time"),
        ("name", "article:published_time"),
        ("name", "pubdate"),
        ("name", "publish-date"),
        ("name", "date"),
        ("property", "og:updated_time"),
    ]:
        mtag = soup.find("meta", attrs={k: v})
        if mtag and mtag.get("content"):
            dt = parse_date(mtag.get("content"))
            if dt:
                return dt

    return None

def extract_fatf_published_from_detail(html: str) -> Optional[datetime]:
    """Find the FATF article's own publication date, avoiding dated site chrome.

    FATF detail pages can show other dated links in the global header before the
    article body. Prefer the article region immediately after H1 so an older
    "high-risk jurisdictions" date cannot disqualify a valid monthly item.
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find("article") or soup
    h1 = main.find("h1") if getattr(main, "find", None) else None

    chunks: List[str] = []
    if h1:
        for node in h1.find_all_next(limit=60):
            name = getattr(node, "name", "")
            if name in {"script", "style", "nav", "footer", "header"}:
                continue
            if name in {"p", "div", "span", "time", "li"} and getattr(node, "get_text", None):
                text = clean_text(node.get_text(" ", strip=True), 700)
                if text:
                    chunks.append(text)
            if len(" ".join(chunks)) > 5000:
                break

    article_text = " ".join(chunks)
    if article_text:
        # The normal FATF lead is e.g. "Paris, 21 July 2026 - ...".
        m = re.search(r"\b(?:Paris,?\s*)?(\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{4})\b", article_text, re.I)
        if m:
            dt = parse_date(m.group(1), dayfirst=True)
            if dt:
                return dt

    return None


def extract_published_from_detail(detail_url: str, html: str, source: str = "") -> Tuple[Optional[datetime], str]:
    soup = BeautifulSoup(html, "html.parser")

    snippet = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        snippet = clean_text(meta_desc.get("content"), 380)

    if source == "FDIC":
        dt = extract_fdic_published_from_detail(detail_url, html)
        if dt:
            return dt, snippet

    if source == "FATF":
        dt = extract_fatf_published_from_detail(html)
        if dt:
            return dt, snippet

    t = soup.find("time")
    if t:
        dt = parse_date(t.get("datetime") or t.get_text(" ", strip=True))
        if dt:
            return dt, snippet

    meta_keys = [
        ("property", "article:published_time"),
        ("name", "article:published_time"),
        ("name", "pubdate"),
        ("name", "publish-date"),
        ("name", "date"),
        ("property", "og:updated_time"),
    ]
    for k, v in meta_keys:
        m = soup.find("meta", attrs={k: v})
        if m and m.get("content"):
            dt = parse_date(m.get("content"))
            if dt:
                return dt, snippet

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.get_text(strip=True) or "{}")
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            for k in ["datePublished", "dateModified"]:
                if k in obj:
                    dt = parse_date(obj.get(k))
                    if dt:
                        return dt, snippet

    dt = extract_any_date(soup.get_text(" ", strip=True), source=source)
    if dt:
        return dt, snippet

    return None, snippet


# ============================
# LISTING EXTRACTION (STRICTER)
# ============================

def pick_container(soup: BeautifulSoup) -> Optional[Any]:
    return (
        soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find(id=re.compile(r"(main|content)", re.I))
        or soup.find("article")
        or soup.find("body")
    )


def looks_js_rendered(html: str) -> bool:
    s = (html or "").lower()
    if "you have javascript disabled" in s:
        return True
    if "loading" in s and "press release" in s:
        return True
    if "select year" in s and "loading" in s:
        return True
    # Some vendor sites render tiles after hydration
    if "data-reactroot" in s and "press" in s and "insights" in s:
        return True
    return False


def strip_nav_like(container: Any) -> None:
    for tag in container.find_all(["header", "footer", "nav", "aside"]):
        try:
            tag.decompose()
        except Exception:
            pass


def find_time_near_anchor(a: Any, source: str) -> Optional[datetime]:
    parent = a.find_parent(["li", "article", "div", "p", "section", "tr", "td"]) or a.parent
    if not parent:
        return None

    t = parent.find("time")
    if t:
        raw = (t.get("datetime") or t.get_text(" ", strip=True) or "").strip()
        if source == "Visa" and SLASH_DATE_RE.search(raw):
            dt = parse_slash_date_best(raw)
        else:
            dt = parse_date(raw, dayfirst=(source in DAYFIRST_SOURCES))
        if dt:
            return dt

    near = clean_text(parent.get_text(" ", strip=True) if parent else "", 900)
    return extract_any_date(near, source=source)


def is_likely_article_anchor(a: Any) -> bool:
    for tag in ["h1", "h2", "h3"]:
        if a.find_parent(tag) is not None:
            return True
    cls = " ".join(a.get("class", [])).lower()
    if any(k in cls for k in ["title", "headline", "card", "teaser", "post"]):
        return True
    p = a.find_parent(["article", "li"])
    if p is not None:
        return True
    return False


# ============================
# FHLB MPF
# ============================

FHLBMPF_LISTING_PATH = "/program-guidelines/mpf-program-updates"
FHLBMPF_DETAIL_PREFIX = "/program-guidelines/mpf-program-updates/"


def fhlbmpf_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    strip_nav_like(container)

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()

    for a in container.select(f'a[href^="{FHLBMPF_DETAIL_PREFIX}"]'):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue

        if href.rstrip("/") == FHLBMPF_LISTING_PATH.rstrip("/"):
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("FHLB MPF", url):
            continue

        raw_title = (a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            raw_title = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()

        title = clean_text(raw_title, 220)
        if not title or len(title) < 8:
            continue

        if title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        if is_probably_nav_link("FHLB MPF", title, url):
            continue
        if is_generic_listing_or_home("FHLB MPF", title, url):
            continue

        if url in seen:
            continue
        seen.add(url)

        dt = find_time_near_anchor(a, "FHLB MPF")

        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    return links


# ============================
# OFAC
# ============================

OFAC_ITEM_RE = re.compile(r"^/recent-actions/\d{8}(/)?$")
OFAC_URL_DATE_RE = re.compile(r"/recent-actions/(?P<ymd>\d{8})(?:/)?$")


def ofac_date_from_url(url: str) -> Optional[datetime]:
    try:
        m = OFAC_URL_DATE_RE.search(urlparse(url).path)
        if not m:
            return None
        ymd = m.group("ymd")
        dt = datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None



PAGINATION_MAX_PAGES = 40  # monthly: deeper pagination for OFAC/Treasury/White House/Senate


def _find_next_page_url(page_url: str, html: str) -> Optional[str]:
    """Best-effort 'next/older' page discovery for paginated listings.

    Important: Many gov/Drupal pagers include "Page 1 / Page 2 / ..." links *before*
    the "Next page" control. We must prefer true "next" controls (rel=next / text/aria-label
    contains 'next'/'older') and, when using page=N links, choose the smallest page number
    greater than the current page.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    # Determine current page number from querystring (default 0 if missing)
    cur_page = 0
    try:
        p = urlparse(page_url)
        qs = parse_qs(p.query or "")
        if "page" in qs and qs["page"]:
            cur_page = int(qs["page"][0])
    except Exception:
        cur_page = 0

    # 1) <link rel="next" href="...">
    for ln in soup.find_all("link", href=True):
        rel = ln.get("rel") or []
        if isinstance(rel, str):
            rel = [rel]
        rel = [r.lower() for r in rel]
        if "next" in rel:
            href = (ln.get("href") or "").strip()
            if href:
                try:
                    u = canonical_url(urljoin(page_url, href))
                    if u != canonical_url(page_url):
                        return u
                except Exception:
                    pass

    # 2) Gather <a> candidates with light scoring
    scored: list[tuple[int, int, str]] = []  # (priority, page_num_or_big, url)
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue

        # pager sites often hide text inside <span class="visually-hidden">Next page</span>
        txt_a = clean_text(a.get_text(" ", strip=True), 120).lower()
        aria = ((a.get("aria-label") or "") + " " + (a.get("title") or "")).lower()

        rel = a.get("rel") or []
        if isinstance(rel, str):
            rel = [rel]
        rel = [r.lower() for r in rel]

        # resolve URL
        try:
            u = canonical_url(urljoin(page_url, href))
        except Exception:
            continue
        if u == canonical_url(page_url):
            continue

        # extract page number if present
        page_num = None
        try:
            up = urlparse(u)
            q = parse_qs(up.query or "")
            if "page" in q and q["page"]:
                page_num = int(q["page"][0])
        except Exception:
            page_num = None

        is_nextish = ("next" in rel) or ("next" in txt_a) or ("next" in aria) or ("older" in txt_a) or ("older" in aria) or ("›" in txt_a) or ("»" in txt_a)

        # Priority:
        #   0 = explicit next/older control
        #   1 = page=N links where N > cur_page (choose smallest N > cur_page)
        #   2 = anything else that looks pager-y
        if is_nextish:
            scored.append((0, page_num if page_num is not None else 10**9, u))
            continue

        if page_num is not None and page_num > cur_page:
            scored.append((1, page_num, u))
            continue

        # fallback pager-like links
        if (page_num is not None) or ("/page/" in u):
            scored.append((2, page_num if page_num is not None else 10**9, u))

    if not scored:
        return None

    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[0][2]


def _bump_query_page(url: str, param: str = "page") -> Optional[str]:
    """Increment a querystring page param (page=1 -> page=2). If absent, add page=2."""
    try:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        p = urlparse(url)
        qs = parse_qs(p.query)
        cur = 1
        if param in qs and qs[param]:
            try:
                cur = int(qs[param][0])
            except Exception:
                cur = 1
        qs[param] = [str(cur + 1)]
        new_query = urlencode(qs, doseq=True)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, new_query, p.fragment))
    except Exception:
        return None


def _bump_query_page_from_zero(url: str, param: str = "page") -> Optional[str]:
    """Increment a querystring page param where the *implicit* first page is page=0.

    If the param is absent, return page=1 (not page=2). If present, increment normally.
    This matches a bunch of Drupal/Gov pagers where ?page=0 is the first page and the bare URL omits it.
    """
    try:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        p = urlparse(url)
        qs = parse_qs(p.query)
        if param in qs and qs[param]:
            try:
                cur = int(qs[param][0])
            except Exception:
                cur = 0
            nxt = cur + 1
        else:
            nxt = 1
        qs[param] = [str(nxt)]
        new_query = urlencode(qs, doseq=True)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, new_query, p.fragment))
    except Exception:
        return None


def _append_path_page(url: str, n: int) -> Optional[str]:
    """Turn .../news/ into .../news/page/N/ if not already."""
    try:
        u = canonical_url(url)
        # normalize trailing slash
        if not u.endswith("/"):
            u += "/"
        if "/page/" in u:
            return None
        return canonical_url(urljoin(u, f"page/{n}/"))
    except Exception:
        return None


def _next_page_url_source_fallback(source: str, cur_url: str, cur_html: str, page_i: int) -> Optional[str]:
    """
    Source-specific pagination fallback when HTML doesn't expose a clear 'Next' link.
    We only attempt a few known patterns for sources that routinely hide pager controls.
    """
    u = canonical_url(cur_url)

    # OFAC recent actions commonly uses ?page=N (pager sometimes icon-only)
    if source == "OFAC" and "ofac.treasury.gov/recent-actions" in u:
        return _bump_query_page_from_zero(u, "page")

    # Treasury press releases supports ?page=N
    if source in ("Treasury", "Treasury Press Releases") and "home.treasury.gov/news/press-releases" in u:
        return _bump_query_page_from_zero(u, "page")

    # White House uses /news/page/N/ and /presidential-actions/page/N/
    if source == "White House" and ("whitehouse.gov/news" in u or "whitehouse.gov/presidential-actions" in u):
        # page_i is zero-based loop counter; next page number starts at 2
        return _append_path_page(u, page_i + 2)

    # NACHA news listing supports Drupal-style ?page=N pagination; the bare /news page is page 0.
    if source == "NACHA" and "nacha.org/news" in u:
        return _bump_query_page_from_zero(u, "page")

    # Senate Banking: pagination is often query-based and 1-based.
    if source == "Senate Banking" and "banking.senate.gov/newsroom" in u:
        try:
            from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
            p = urlparse(u)
            qs = parse_qs(p.query or "")
            # Prefer PageNum_rs when present; otherwise fall back.
            keys = ["PageNum_rs", "PageNum", "page"]
            cur_key = next((k for k in keys if k in qs and qs[k]), None)

            if cur_key:
                try:
                    cur = int(qs[cur_key][0])
                except Exception:
                    cur = 1
                nxt = cur + 1
                qs[cur_key] = [str(nxt)]
            else:
                # No explicit page param on the first page -> assume 1-based and start at 2.
                qs["PageNum_rs"] = [str(page_i + 2)]
            new_query = urlencode(qs, doseq=True)
            return urlunparse((p.scheme, p.netloc, p.path, p.params, new_query, p.fragment))
        except Exception:
            # fallback: best-effort increment if a param exists
            return _bump_query_page_from_zero(u, "PageNum_rs") or _bump_query_page_from_zero(u, "PageNum") or _bump_query_page_from_zero(u, "page")
    return None


def _paginate_listing(
    source: str,
    first_url: str,
    first_html: str,
    window_start: Optional[datetime],
    single_page_fn,
) -> List[Tuple[str, str, Optional[datetime]]]:
    """Fetch multiple listing pages until we likely cover window_start."""
    out: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    cur_url = first_url
    cur_html = first_html

    for _i in range(PAGINATION_MAX_PAGES):
        batch = single_page_fn(cur_url, cur_html) or []
        for t, u, d in batch:
            if not u or u in seen:
                continue
            seen.add(u)
            out.append((t, u, d))
            if len(out) >= MAX_LISTING_LINKS:
                return out[:MAX_LISTING_LINKS]

        # Stop when the current page clearly reaches (or goes older than) the window start.
        if window_start:
            dts = [d for _t, _u, d in batch if d]
            if dts and min(dts) <= window_start:
                # We likely have enough depth to include the full month.
                break

        next_url = _find_next_page_url(cur_url, cur_html)
        if not next_url:
            next_url = _next_page_url_source_fallback(source, cur_url, cur_html, _i)
        if not next_url or canonical_url(next_url) == canonical_url(cur_url):
            break

        nxt_html = polite_get(next_url)
        if not nxt_html:
            break

        cur_url, cur_html = next_url, nxt_html

    return out


def ofac_links(page_url: str, html: str, window_start: Optional[datetime]) -> List[Tuple[str, str, Optional[datetime]]]:
    return _paginate_listing("OFAC", page_url, html, window_start, ofac_links_single)


def treasury_links(page_url: str, html: str, window_start: Optional[datetime]) -> List[Tuple[str, str, Optional[datetime]]]:
    return _paginate_listing("Treasury", page_url, html, window_start, treasury_links_single)


def whitehouse_links(page_url: str, html: str, window_start: Optional[datetime]) -> List[Tuple[str, str, Optional[datetime]]]:
    return _paginate_listing("White House", page_url, html, window_start, whitehouse_links_single)




def irs_links(
    page_url: str,
    html: str,
    window_start: datetime,
    window_end: datetime,
) -> List[Tuple[str, str, Optional[datetime]]]:
    """IRS newsroom + monthly archive extractor.

    IRS rolls news releases into per-month archive pages like:
      /newsroom/news-releases-for-january-2026

    The IRS HTML structure varies across newsroom hubs, and many links live inside
    plain <div> blocks (not <li>/<article>). For RegMonthly we want *the full prior
    month*, so we:
      - capture all /newsroom/ links on the page (including div-based listings)
      - try to infer a nearby date
      - if we find a date and it's outside the target month window, drop it early
        (detail fetch will still confirm dates when missing).
    """
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    strip_nav_like(container)

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    def consider_anchor(a) -> None:
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            return

        url = canonical_url(urljoin(page_url, href))
        if not url or not allowed_for_source("IRS", url):
            return

        pth = (urlparse(url).path or "").lower()

        # Keep real newsroom items; skip the main newsroom landing and obvious non-articles.
        if "/newsroom/" not in pth:
            return
        if pth.rstrip("/").endswith("/newsroom"):
            return
        if "/downloads/rss" in pth:
            return

        raw_title = (a.get_text(" ", strip=True) or "").strip()
        title = clean_text(raw_title, 220)
        if not title or len(title) < 8:
            return

        tl = title.lower()
        if tl in GENERIC_TITLES or tl in {"news releases", "tax tips", "newsroom"}:
            return
        if is_probably_nav_link("IRS", title, url) or is_generic_listing_or_home("IRS", title, url):
            return

        if url in seen:
            return
        seen.add(url)

        dt = find_time_near_anchor(a, "IRS")
        if dt is None:
            wrap = a.find_parent(["li", "article", "div", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 1600), source="IRS")

        # If we have a date and it's outside the month window, discard now.
        if dt is not None and not in_window(dt, window_start, window_end):
            return

        links.append((title, url, dt))

    # Prefer structured listings but also include div-based listings (IRS often uses those).
    selectors = [
        "article a[href]",
        "li a[href]",
        "h2 a[href]",
        "h3 a[href]",
        "p a[href]",
        "div a[href]",
    ]
    for a in container.select(",".join(selectors)):
        consider_anchor(a)
        if len(links) >= MAX_LISTING_LINKS:
            break

    # Fallback: any /newsroom/ anchors anywhere
    if not links:
        for a in soup.find_all("a", href=True):
            if "/newsroom/" in (a.get("href") or "").lower():
                consider_anchor(a)
                if len(links) >= MAX_LISTING_LINKS:
                    break

    return links


def ofac_links_single(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()

    for a in container.select('a[href^="/recent-actions/"]'):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue

        if not OFAC_ITEM_RE.match(href):
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("OFAC", url):
            continue

        raw_title = (a.get_text(" ", strip=True) or "").strip()
        title = clean_text(raw_title, 220)
        if not title or len(title) < 8:
            continue

        if url in seen:
            continue
        seen.add(url)

        dt = find_time_near_anchor(a, "OFAC")
        if dt is None:
            dt = ofac_date_from_url(url)

        if dt is None:
            wrap = a.find_parent(["div", "article", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 1000), source="OFAC")

        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    return links


def whitehouse_links_single(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    def consider_anchor(a) -> None:
        nonlocal links, seen
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            return

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("White House", url):
            return

        title = clean_text(a.get_text(" ", strip=True) or "", 220)
        if not title or len(title) < 8:
            return
        if is_probably_nav_link("White House", title, url):
            return
        if is_generic_listing_or_home("White House", title, url):
            return

        if url in seen:
            return
        seen.add(url)

        dt = find_time_near_anchor(a, "White House")
        if dt is None:
            wrap = a.find_parent(["div", "article", "li", "section"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 1000), source="White House")

        links.append((title, url, dt))

    # Primary: headline links (current markup)
    for a in container.select("h2 a[href], h3 a[href]"):
        consider_anchor(a)
        if len(links) >= MAX_LISTING_LINKS:
            break

    # Fallback: some White House listings don't use h2/h3 anchors in the main container
    if not links:
        for a in container.find_all("a", href=True):
            href = (a.get("href") or "").strip().lower()
            if ("/news/" in href) or ("/briefings-statements/" in href) or ("/presidential-actions/" in href):
                consider_anchor(a)
                if len(links) >= MAX_LISTING_LINKS:
                    break

    # newest-first when dates exist
    links.sort(key=lambda t: (t[2] is None, t[2] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return links


# ============================
# Mastercard
# ============================

MASTERCARD_PR_PATH_RE = re.compile(
    r"^/(us|global|gb|mea)/en/"
    r"(?:(?:news-and-trends/press)|(?:newsroom/press-releases))"
    r"/(?P<year>\d{4})"
    r"(?:/[a-z0-9\-%]+)*"
    r"/[a-z0-9\-%]+\.html$",
    re.I,
)

MC_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]{8,220})\]\((https?://www\.mastercard\.com/[^\s)]+)\)",
    re.I,
)


MC_DATE_IN_PATH_RE = re.compile(
    r"/(?:(?:news-and-trends/press)|(?:newsroom/press-releases)|press)"
    r"(?:/(?:releases))?"
    r"/(?P<y>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})/",
    re.I,
)

MC_DATE_MONTHNAME_RE = re.compile(
    r"/(?P<y>\d{4})/"
    r"(?P<mon>january|february|march|april|may|june|july|august|september|october|november|december)"
    r"/(?P<d>\d{1,2})/",
    re.I,
)


def mastercard_date_from_url(url: str) -> Optional[datetime]:
    """Best-effort date extraction from Mastercard press-release URL paths.

    Mastercard detail pages are frequently blocked (403) even via proxy. Many URLs embed a date in the path.
    We support both numeric months (YYYY/MM/DD) and month-name paths (YYYY/february/12/...).
    """
    try:
        p = urlparse(url).path or ""

        m = MC_DATE_IN_PATH_RE.search(p)
        if m:
            y = int(m.group("y"))
            mo = int(m.group("m"))
            d = int(m.group("d"))
        else:
            m2 = MC_DATE_MONTHNAME_RE.search(p)
            if not m2:
                return None
            y = int(m2.group("y"))
            mon = (m2.group("mon") or "").strip().lower()
            month_map = {
                "january": 1,
                "february": 2,
                "march": 3,
                "april": 4,
                "may": 5,
                "june": 6,
                "july": 7,
                "august": 8,
                "september": 9,
                "october": 10,
                "november": 11,
                "december": 12,
            }
            mo = month_map.get(mon)
            if not mo:
                return None
            d = int(m2.group("d"))

        dt = datetime(y, mo, d, 12, 0, 0, tzinfo=CENTRAL_TZ)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _mastercard_links_from_text(page_url: str, text: str) -> List[Tuple[str, str, Optional[datetime]]]:
    """Mastercard fallback: parse proxy/reader text when HTML cards/anchors are thin.

    This supports both markdown-style links returned by reader/proxy paths and raw
    mastercard.com URLs embedded in text blobs. Dates are taken from nearby text when
    available, then from the URL path as a last resort.
    """
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()
    blob = text or ""

    def _add(title: str, url: str, dt: Optional[datetime]) -> None:
        nonlocal links, seen
        if not url or url in seen:
            return
        if not allowed_for_source("Mastercard", url):
            return
        if not MASTERCARD_PR_PATH_RE.match(urlparse(url).path):
            return
        if is_probably_nav_link("Mastercard", title, url):
            return
        if is_generic_listing_or_home("Mastercard", title, url):
            return
        seen.add(url)
        links.append((title, url, dt))

    for m in MC_MARKDOWN_LINK_RE.finditer(blob):
        title = clean_text(m.group(1), 220)
        url = canonical_url(m.group(2))
        if not url:
            continue

        i0, i1 = m.span()
        ctx = blob[max(0, i0 - 260) : min(len(blob), i1 + 80)]
        dt = extract_any_date(ctx, source="Mastercard")
        if dt is None:
            line_start = blob.rfind("\n", 0, i0) + 1
            line_end = blob.find("\n", i1)
            if line_end == -1:
                line_end = len(blob)
            line = blob[line_start:line_end]
            dt = extract_any_date(line, source="Mastercard")
        if dt is None:
            dt = mastercard_date_from_url(url)

        _add(title, url, dt)
        if len(links) >= MAX_LISTING_LINKS:
            return links

    raw_url_re = re.compile(r'(https?://www\.mastercard\.com/[^\s"\')<>]+)', re.I)
    for m in raw_url_re.finditer(blob):
        url = canonical_url(m.group(1))
        if not url:
            continue

        i0, i1 = m.span()
        ctx = blob[max(0, i0 - 260) : min(len(blob), i1 + 120)]
        dt = extract_any_date(ctx, source="Mastercard")
        if dt is None:
            dt = mastercard_date_from_url(url)

        title = title_from_url_slug(url, "Mastercard press release")
        _add(title, url, dt)
        if len(links) >= MAX_LISTING_LINKS:
            break

    return links


def _parse_date_any(text: str) -> Optional[datetime]:
    t = (text or "").strip()
    if not t:
        return None
    # Common formats like "February 23, 2026" or "Feb 23, 2026"
    try:
        dt = dtparser.parse(t, fuzzy=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CENTRAL_TZ)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def aba_news_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    """
    ABA's /news-research page is server-rendered and includes a short list of fresh items with dates.
    We pull only the real story links (usually bankingjournal.aba.com) and attach the nearby date text.
    """
    soup = BeautifulSoup(html, "html.parser")
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()

    # Grab anchors that look like actual stories (most are on bankingjournal.aba.com)
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("ABA", url):
            continue

        title = clean_text(a.get_text(" ", strip=True) or "", 220)
        if not title or len(title) < 10:
            continue

        # Find a nearby date string within the same block
        dt: Optional[datetime] = None
        block = a.parent
        # Walk up a bit to find the small card/list item
        for _ in range(4):
            if not block:
                break
            txtb = block.get_text(" ", strip=True)
            # quick month-name heuristic
            if re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b\s+\d{1,2},\s+\d{4}", txtb):
                dt = _parse_date_any(txtb)
                break
            block = block.parent

        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        links.append((title, url, dt))

    # Prefer most recent-looking first if dates exist
    links.sort(key=lambda t: (t[2] is None, t[2] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return links[:MAX_LISTING_LINKS]


def wolterskluwer_news_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    """
    Wolters Kluwer /en/news is server-rendered; links point to /en/news/<slug>.
    Capture those and try to extract the nearby date in the same card.
    """
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()

    # Proxy responses are often Markdown rather than HTML.
    if html and "<html" not in html.lower() and "](" in html:
        md_re = re.compile(
            r"\[([^\]]{8,260})\]\((https?://www\.wolterskluwer\.com/(?:en|en-gb)/news/[^\)\s]+)\)",
            re.I,
        )
        for m in md_re.finditer(html):
            title = clean_text(m.group(1), 220)
            url = canonical_url(m.group(2))
            if not title or title.lower() in {"read more", "learn more"}:
                continue
            if url in seen or not allowed_for_source("Wolters Kluwer", url):
                continue
            ctx = html[max(0, m.start() - 240): min(len(html), m.end() + 180)]
            dt = extract_any_date(ctx, source="Wolters Kluwer")
            seen.add(url)
            links.append((title, url, dt))
            if len(links) >= MAX_LISTING_LINKS:
                return links
        if links:
            return links

    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        # keep only newsroom article slugs
        if "/en/news/" not in href:
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("Wolters Kluwer", url):
            continue

        title = clean_text(a.get_text(" ", strip=True) or "", 220)
        if not title or len(title) < 10:
            continue
        if title.lower() in {"read more", "learn more"}:
            continue

        dt: Optional[datetime] = None
        block = a.parent
        for _ in range(5):
            if not block:
                break
            txtb = block.get_text(" ", strip=True)
            if re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b\s+\d{1,2},\s+\d{4}", txtb):
                dt = _parse_date_any(txtb)
                break
            block = block.parent

        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        links.append((title, url, dt))

    links.sort(key=lambda t: (t[2] is None, t[2] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return links[:MAX_LISTING_LINKS]


def _discover_senate_banking_paged_links(
    page_url: str,
    first_html: str,
    max_pages: int = 120,
) -> List[Tuple[str, str, Optional[datetime]]]:
    """Exhaustively walk Senate Banking majority/minority press-release pagination.

    The site commonly uses PageNum_rs=N and may not expose a reliable "next" link in the HTML.
    We stop when a page yields no new qualifying article links.
    """
    out: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    cur_url = canonical_url(page_url)
    cur_html = first_html

    for page_i in range(max_pages):
        batch = senate_banking_links_single(cur_url, cur_html) or []
        new_on_page = 0
        for t, u, d in batch:
            if not u or u in seen:
                continue
            seen.add(u)
            out.append((t, u, d))
            new_on_page += 1
            if len(out) >= MAX_LISTING_LINKS:
                return out[:MAX_LISTING_LINKS]

        if new_on_page == 0:
            break

        next_url = _next_page_url_source_fallback("Senate Banking", cur_url, cur_html, page_i)
        if not next_url or canonical_url(next_url) == canonical_url(cur_url):
            break

        nxt_html = polite_get(next_url)
        if not nxt_html:
            break

        cur_url, cur_html = next_url, nxt_html

    return out


def senate_banking_links(page_url: str, html: str, window_start: Optional[datetime]) -> List[Tuple[str, str, Optional[datetime]]]:
    links = _paginate_listing("Senate Banking", page_url, html, window_start, senate_banking_links_single)

    # Majority/minority press-release archives often hide pager controls.
    # Walk PageNum_rs pages directly so we do not miss articles because of pagination.
    page_url_l = (page_url or "").lower()
    if ("/newsroom/majority-press-releases" in page_url_l) or ("/newsroom/minority-press-releases" in page_url_l):
        seen = {u for _t, u, _d in links}
        for t, u, d in _discover_senate_banking_paged_links(page_url, html):
            if u in seen:
                continue
            seen.add(u)
            links.append((t, u, d))
            if len(links) >= MAX_LISTING_LINKS:
                break

    # The Senate Banking newsroom landing page now exposes only a small handful of recent items,
    # which can leave a full prior-month run empty. Fall back to sitemap discovery for this source only.
    if len(links) < 12:
        seen = {u for _t, u, _d in links}
        for t, u, d in sitemap_links_for_source("Senate Banking"):
            if u in seen:
                continue
            seen.add(u)
            links.append((t, u, d))
            if len(links) >= MAX_LISTING_LINKS:
                break
    return links


def senate_banking_links_single(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    strip_nav_like(container)

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    for a in container.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue

        href_l = href.lower()
        if not (
            "/newsroom/majority-press-releases/" in href_l
            or "/newsroom/minority-press-releases/" in href_l
            or "/newsroom/majority/" in href_l
            or "/newsroom/minority/" in href_l
            or re.search(r"/newsroom/\d{2}/\d{2}/\d{4}/", href_l)
            or href_l.startswith("/news/")
        ):
            continue
        if any(x in href_l for x in ["/videos", "/in-the-news", "/photos"]):
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("Senate Banking", url):
            continue
        if is_generic_listing_or_home("Senate Banking", "", url):
            continue

        raw_title = (a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            raw_title = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
        title = clean_text(raw_title, 220)
        if not title or len(title) < 8:
            continue
        if is_probably_nav_link("Senate Banking", title, url):
            continue
        if title.lower() in GENERIC_TITLES:
            continue

        if url in seen:
            continue

        dt = find_time_near_anchor(a, "Senate Banking")
        if dt is None:
            wrap = a.find_parent(["article", "div", "li", "section", "p", "tr", "td"]) or a.parent
            if wrap:
                blob = clean_text(wrap.get_text(" ", strip=True), 1200)
                dt = extract_any_date(blob, source="Senate Banking")

                if dt is None:
                    prev_bits: List[str] = []
                    for sib in list(wrap.previous_siblings)[-6:]:
                        try:
                            txt = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib).strip()
                        except Exception:
                            txt = ""
                        if txt:
                            prev_bits.append(txt)
                    if prev_bits:
                        dt = extract_any_date(" ".join(prev_bits), source="Senate Banking")

        seen.add(url)
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    links.sort(key=lambda t: (t[2] is None, t[2] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return links

def fincen_links(page_url: str, html: str, window_start: Optional[datetime]) -> List[Tuple[str, str, Optional[datetime]]]:
    return _paginate_listing("FinCEN", page_url, html, window_start, fincen_links_single)

# ============================
# OCC
# ============================

# OCC detail pages usually look like:
#   https://www.occ.gov/news-issuances/news-releases/2026/nr-occ-YYYY-XX.html
OCC_DETAIL_RE = re.compile(r"/news-issuances/news-releases/\d{4}/[^\s#?]+", re.I)

def occ_links_single(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    if not soup:
        return []

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    # OCC "index-news-releases" has a list/table of releases; grab anchors that look like detail pages.
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("OCC", url):
            continue

        # Prefer detail pages, not the listing hub itself.
        if not OCC_DETAIL_RE.search(url):
            continue

        raw_title = (a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            raw_title = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()

        title = clean_text(raw_title, 220)
        if not title or len(title) < 8:
            continue

        if is_probably_nav_link("OCC", title, url) or is_generic_listing_or_home("OCC", title, url):
            continue

        if url in seen:
            continue
        seen.add(url)

        # Try to infer a nearby date on the listing page (helps pagination stop early).
        dt = find_time_near_anchor(a, "OCC")
        if dt is None:
            wrap = a.find_parent(["tr", "li", "article", "div", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 1200), source="OCC")

        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    # Jina and other text proxies can return Markdown instead of HTML.
    if not links and html:
        md_re = re.compile(
            r"\[([^\]]{8,260})\]\((https?://(?:www\.)?occ\.gov/news-issuances/news-releases/\d{4}/[^\)\s]+)\)",
            re.I,
        )
        for m in md_re.finditer(html):
            title = clean_text(m.group(1), 260)
            url = canonical_url(m.group(2))
            if not title or url in seen or not allowed_for_source("OCC", url):
                continue
            line_start = max(0, html.rfind("\n", 0, m.start()) + 1)
            line_end = html.find("\n", m.end())
            if line_end < 0:
                line_end = min(len(html), m.end() + 300)
            dt = extract_any_date(html[line_start:line_end], source="OCC")
            seen.add(url)
            links.append((title, url, dt))
            if len(links) >= MAX_LISTING_LINKS:
                break

    return links

def occ_links(page_url: str, html: str, window_start: Optional[datetime]) -> List[Tuple[str, str, Optional[datetime]]]:
    # OCC pagination varies; use generic paginator + source-specific fallback if needed.
    return _paginate_listing("OCC", page_url, html, window_start, occ_links_single)




def fincen_links_single(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()

    for a in container.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue

        # FinCEN has multiple URL families:
        # - /news-room/... (current)
        # - /newsroom/... (alternate)
        # - /files/news/news-releases/... (often used for news releases)
        # Keep only HTML pages (skip PDFs).
        href_l = href.lower()
        if href_l.endswith(".pdf"):
            continue
        if not (
            "/news-room/" in href_l
            or href_l.rstrip("/").endswith("/news-room")
            or "/newsroom/" in href_l
            or href_l.rstrip("/").endswith("/newsroom")
            or "/news/" in href_l
            or href_l.rstrip("/").endswith("/news")
            or "/files/news/news-releases" in href_l
        ):
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("FinCEN", url):
            continue

        raw_title = (a.get_text(" ", strip=True) or "").strip()
        title = clean_text(raw_title, 220)
        if not title or len(title) < 8:
            continue

        if url in seen:
            continue
        seen.add(url)

        dt = find_time_near_anchor(a, "FinCEN")
        if dt is None:
            wrap = a.find_parent(["article", "div", "li", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 1200), source="FinCEN")

        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    return links



def mastercard_date_from_listing_context(a: Any) -> Optional[datetime]:
    """Try harder to find a date string near a Mastercard press link on the listing page.

    On the /press.html listing, the date is often rendered as plain text near the headline,
    not inside a <time> tag, and may be adjacent to the anchor rather than inside it.
    """
    if not a:
        return None
    # Look in the nearest card/list row and its immediate siblings
    node = a.find_parent(["li", "article", "div", "section", "p"]) or a.parent
    candidates: list[str] = []
    try:
        if node:
            candidates.append(clean_text(node.get_text(" ", strip=True), 600))
            # include some sibling text (date can be in adjacent span/div)
            for sib in list(getattr(node, "next_siblings", []))[:6]:
                try:
                    if hasattr(sib, "get_text"):
                        candidates.append(clean_text(sib.get_text(" ", strip=True), 200))
                    else:
                        s = str(sib).strip()
                        if s:
                            candidates.append(clean_text(s, 200))
                except Exception:
                    continue
    except Exception:
        pass

    # Fallback: climb a couple levels and re-scan
    try:
        up = node
        for _ in range(2):
            if not up:
                break
            up = up.parent
            if up and hasattr(up, "get_text"):
                candidates.append(clean_text(up.get_text(" ", strip=True), 900))
    except Exception:
        pass

    mc_dt_re = re.compile(
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?\s+\d{1,2},\s+\d{4}",
        re.I,
    )

    for cand in candidates:
        dt = extract_any_date(cand, source="Mastercard")
        if dt is None:
            m = mc_dt_re.search(cand or "")
            if m:
                dt = parse_date(m.group(0))

        if dt:
            return dt
    return None



def mastercard_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()

    for a in container.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue

        u = urlparse(urljoin(page_url, href))
        if u.netloc.lower() != "www.mastercard.com":
            continue
        if not MASTERCARD_PR_PATH_RE.match(u.path):
            continue
        # Skip Mastercard listing/index pages (not actual press-release detail pages)
        pth = (u.path or "").rstrip("/")
        if pth.endswith("/press") or pth.endswith("/press.html"):
            continue
        if re.search(r"/news-and-trends/press/\d{4}$", pth, re.I) or re.search(r"/news-and-trends/press/\d{4}\.html$", pth, re.I):
            continue
        if re.search(r"/newsroom/press-releases/\d{4}$", pth, re.I) or re.search(r"/newsroom/press-releases/\d{4}\.html$", pth, re.I):
            continue
        
        url = canonical_url(u.geturl())
        if not allowed_for_source("Mastercard", url):
            continue
        
        raw_title = (a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            raw_title = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
        
        title = clean_text(raw_title, 220)
        if not title or len(title) < 10:
            continue
        
        if title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        if is_probably_nav_link("Mastercard", title, url):
            continue
        if is_generic_listing_or_home("Mastercard", title, url):
            continue
        
        if url in seen:
            continue
        seen.add(url)
        
        dt = extract_any_date(title, source="Mastercard")
        if dt is None:
            dt = find_time_near_anchor(a, "Mastercard")
        if dt is None:
            wrap = a.find_parent(["li", "article", "div", "section"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 1000), source="Mastercard")
        if dt is None:
            dt = mastercard_date_from_url(url)
        if dt is None:
            dt = mastercard_date_from_listing_context(a)
        if dt is None:
            dt = mastercard_date_from_url(url)
        
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            return links
        
    extra = _mastercard_links_from_text(page_url, html)
    for t, u, d in extra:
        if u in seen:
            continue
        seen.add(u)
        links.append((t, u, d))
        if len(links) >= MAX_LISTING_LINKS:
            break

    links.sort(key=lambda t: (t[2] is None, t[2] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return links
        
        
# ============================
# Visa
# ============================
        
def visa_date_from_listing_context(a: Any) -> Optional[datetime]:
    if not a:
        return None
        
    head = a.find_parent(["h1", "h2", "h3"]) or a
        
    try:
        checked = 0
        for sib in head.previous_siblings:
            if checked >= 25:
                break
            checked += 1
        
            txt = ""
            if isinstance(sib, str):
                txt = sib.strip()
            else:
                try:
                    txt = (sib.get_text(" ", strip=True) or "").strip()
                except Exception:
                    txt = ""
        
            if not txt:
                continue
        
            m = SLASH_DATE_RE.search(txt)
            if m:
                return parse_slash_date_best(m.group("sd"))
        
            dt = extract_any_date(txt, source="Visa")
            if dt:
                return dt
    except Exception:
        pass
        
    return None
        
        
def visa_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()

    selectors = [
        'a[href*="/about-visa/newsroom/press-releases.releaseId."]',
        'a[href*="/about-visa/newsroom/press-releases/"]',
        'a[href*="press-releases.releaseId."]',
        'a[href*="/press-releases.releaseId."]',
    ]

    for sel in selectors:
        for a in container.select(sel):
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#"):
                continue

            url = canonical_url(urljoin(page_url, href))
            if not allowed_for_source("Visa", url):
                continue

            raw_title = (a.get_text(" ", strip=True) or "").strip()
            if not raw_title:
                raw_title = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
            title = clean_text(raw_title, 220)
            if not title or len(title) < 8:
                continue

            if title.lower() in {"read more", "learn more", "more", "details"}:
                continue
            if is_probably_nav_link("Visa", title, url):
                continue
            if is_generic_listing_or_home("Visa", title, url):
                continue

            if url in seen:
                continue
            seen.add(url)

            dt = find_time_near_anchor(a, "Visa")
            if dt is None:
                dt = visa_date_from_listing_context(a)
            if dt is None:
                wrap = a.find_parent(["li", "article", "div", "section"]) or a.parent
                if wrap:
                    dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 1200), source="Visa")

            links.append((title, url, dt))
            if len(links) >= MAX_LISTING_LINKS:
                links.sort(key=lambda t: (t[2] is None, t[2] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
                return links

    blob = clean_text(container.get_text("\n", strip=True), 50000)
    visa_line_re = re.compile(
        r'(?P<date>\b\d{1,2}/\d{1,2}/\d{2,4}\b)\s+(?P<title>.{8,260}?)\s+(?P<url>https?://usa\.visa\.com/about-visa/newsroom/press-releases(?:\.releaseId)?\.[^\s]+)',
        re.I,
    )
    for m in visa_line_re.finditer(blob):
        url = canonical_url(m.group('url'))
        if url in seen:
            continue
        title = clean_text(m.group('title'), 220)
        dt = parse_slash_date_best(m.group('date'))
        if not title or not allowed_for_source('Visa', url):
            continue
        if is_probably_nav_link('Visa', title, url) or is_generic_listing_or_home('Visa', title, url):
            continue
        seen.add(url)
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    links.sort(key=lambda t: (t[2] is None, t[2] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return links


# ============================
# Treasury press releases
# ============================
        
TREASURY_PR_PATH_RE = re.compile(r"^/news/press-releases/[a-z0-9\-]+$", re.I)
        
        
def treasury_date_from_listing_context(a: Any) -> Optional[datetime]:
    if not a:
        return None
        
    wrap = a.find_parent(["li", "article", "div", "section"]) or a.parent
    if wrap:
        blob = clean_text(wrap.get_text(" ", strip=True), 900)
        dt = extract_any_date(blob, source="Treasury")
        if dt:
            return dt
        
    head = a.find_parent(["h1", "h2", "h3", "h4"]) or a
    try:
        checked = 0
        for sib in head.previous_siblings:
            if checked >= 30:
                break
            checked += 1
            txt = ""
            if isinstance(sib, str):
                txt = sib.strip()
            else:
                try:
                    txt = (sib.get_text(" ", strip=True) or "").strip()
                except Exception:
                    txt = ""
            if not txt:
                continue
            dt = extract_any_date(txt, source="Treasury")
            if dt:
                return dt
    except Exception:
        pass
        
    return None
        
        
def treasury_links_single(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []
        
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()
        
    for a in container.select('a[href^="/news/press-releases/"]'):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        if not TREASURY_PR_PATH_RE.match(href):
            continue
        
        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("Treasury", url):
            continue
        
        raw_title = (a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            raw_title = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
        
        title = clean_text(raw_title, 220)
        if not title or len(title) < 8:
            continue
        
        if title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        if is_probably_nav_link("Treasury", title, url):
            continue
        if is_generic_listing_or_home("Treasury", title, url):
            continue
        
        if url in seen:
            continue
        seen.add(url)
        
        dt = find_time_near_anchor(a, "Treasury")
        if dt is None:
            dt = treasury_date_from_listing_context(a)
        
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break
        
    if not links:
        for a in container.select("h2 a[href], h3 a[href]"):
            href = (a.get("href") or "").strip()
            if not href or not href.startswith("/news/press-releases/"):
                continue
            if not TREASURY_PR_PATH_RE.match(href):
                continue
        
            url = canonical_url(urljoin(page_url, href))
            if not allowed_for_source("Treasury", url):
                continue
        
            title = clean_text(a.get_text(" ", strip=True) or "", 220)
            if not title:
                continue
        
            if url in seen:
                continue
            seen.add(url)
        
            dt = find_time_near_anchor(a, "Treasury")
            if dt is None:
                dt = treasury_date_from_listing_context(a)
        
            links.append((title, url, dt))
            if len(links) >= MAX_LISTING_LINKS:
                break
        
    return links
        
        
# ============================
# Freddie Mac (GlobeNewswire)
# ============================
        
def _globenewswire_find_date_near(a: Any, source: str) -> Optional[datetime]:
    if not a:
        return None
        
    dt = find_time_near_anchor(a, source)
    if dt:
        return dt
        
    cur = a
    for _ in range(0, 5):
        cur = cur.parent if getattr(cur, "parent", None) is not None else None
        if not cur or not getattr(cur, "get_text", None):
            break
        
        try:
            for sel in [
                ".date",
                ".release-date",
                ".releaseDate",
                ".timestamp",
                ".time",
                "[class*='date']",
                "[class*='time']",
                "[class*='timestamp']",
            ]:
                el = cur.select_one(sel)
                if el and getattr(el, "get_text", None):
                    dt2 = extract_any_date(clean_text(el.get_text(" ", strip=True), 240), source=source)
                    if dt2:
                        return dt2
        except Exception:
            pass
        
        try:
            blob = clean_text(cur.get_text(" ", strip=True), 1200)
            dt2 = extract_any_date(blob, source=source)
            if dt2:
                return dt2
        except Exception:
            pass
        
    return None
        
        
def freddiemac_globenewswire_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []
        
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()
        
    for a in container.select('a[href*="/news-release/"], a[href*="/en/news-release/"]'):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        
        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("Freddie Mac", url):
            continue
        
        raw_title = (a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            raw_title = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
        if not raw_title:
            continue
        
        title = clean_text(raw_title, 220)
        if not title or len(title) < 8:
            continue
        
        if title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        if is_probably_nav_link("Freddie Mac", title, url):
            continue
        if is_generic_listing_or_home("Freddie Mac", title, url):
            continue
        
        if url in seen:
            continue
        seen.add(url)
        
        dt = _globenewswire_find_date_near(a, "Freddie Mac")
        links.append((title, url, dt))
        
        if len(links) >= MAX_LISTING_LINKS:
            break
        
    return links
        
        
# ============================
# CDIA
# ============================
        
def cdia_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []
        
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()
        
    for a in container.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        
        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("CDIA", url):
            continue
        
        t = (a.get_text(" ", strip=True) or "").strip()
        tl = t.lower()
        
        if tl in {"read more", "learn more", "more", ""}:
            wrap = a.find_parent(["article", "div", "section", "li"]) or a.parent
            if not wrap:
                continue
        
            h = wrap.find(["h1", "h2", "h3", "h4"])
            if h:
                title = clean_text(h.get_text(" ", strip=True), 220)
            else:
                blob = clean_text(wrap.get_text(" ", strip=True), 500)
                title = clean_text(blob.split("…")[0], 220)
        
            if not title or title.lower() in GENERIC_TITLES:
                continue
        else:
            title = clean_text(t, 220)
        
        if is_probably_nav_link("CDIA", title, url):
            continue
        if is_generic_listing_or_home("CDIA", title, url):
            continue
        if url in seen:
            continue
        seen.add(url)
        
        dt = find_time_near_anchor(a, "CDIA")
        links.append((title, url, dt))
        
        if len(links) >= MAX_LISTING_LINKS:
            break
        
    return links
        
        
# ============================
# ✅ NEW: Jack Henry listing extractor (table-based)
# ============================
        
JH_DETAIL_RE = re.compile(r"^/news-releases/news-release-details/", re.I)
        
def jackhenry_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    # Jack Henry IR pages can intermittently time out or return JS-heavy content.
    # When we fetch via the r.jina.ai proxy, the response is often Markdown, not HTML.
    # In that case, parse Markdown-style links like: [Title](https://ir.jackhenry.com/...)
    if html and ("<html" not in html.lower()) and ("](" in html) and ("ir.jackhenry.com" in html or "jkhy.client.shareholder.com" in html):
        links: List[Tuple[str, str, Optional[datetime]]] = []
        seen: set[str] = set()

        md_re = re.compile(
            r"\[([^\]]{3,220})\]\((https?://(?:ir\.jackhenry\.com|jkhy\.client\.shareholder\.com)/[^\)\s]+)\)",
            re.I,
        )
        for m in md_re.finditer(html):
            title = clean_text(m.group(1), 220)
            url = canonical_url(m.group(2))
            if not allowed_for_source("Jack Henry", url):
                continue
            up = urlparse(url).path.lower()
            if not JH_DETAIL_RE.match(up) and "/news-release-details/" not in up and "/press-release-details/" not in up:
                continue
            if url in seen:
                continue
            seen.add(url)
            links.append((title, url, None))
            if len(links) >= MAX_LISTING_LINKS:
                break

        if links:
            return links

    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    strip_nav_like(container)

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    for a in container.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("Jack Henry", url):
            continue

        up = urlparse(url).path.lower()
        if not JH_DETAIL_RE.match(up) and "/news-release-details/" not in up and "/press-release-details/" not in up:
            continue

        raw_title = (a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            wrap = a.find_parent(["tr", "li", "article", "div", "section"]) or a.parent
            if wrap:
                h = wrap.find(["h1", "h2", "h3", "h4", "strong"])
                if h:
                    raw_title = (h.get_text(" ", strip=True) or "").strip()

        if not raw_title:
            continue

        title = clean_text(raw_title, 220)
        if not title or title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        if is_probably_nav_link("Jack Henry", title, url):
            continue

        if url in seen:
            continue
        seen.add(url)

        dt = None
        row = a.find_parent("tr")
        if row:
            dt = extract_any_date(clean_text(row.get_text(" ", strip=True), 500), source="Jack Henry")
        if dt is None:
            wrap = a.find_parent(["li", "article", "div", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 900), source="Jack Henry")
        if dt is None:
            dt = find_time_near_anchor(a, "Jack Henry")

        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    # Plain-URL fallback for shareholder/proxy text that isn't valid HTML.
    if not links and html:
        raw_url_re = re.compile(
            r"https?://(?:ir\.jackhenry\.com|jkhy\.client\.shareholder\.com)/[^\s"'<>]+',
            re.I,
        )
        for m in raw_url_re.finditer(html):
            url = canonical_url(m.group(0).rstrip(').,'))
            if not allowed_for_source("Jack Henry", url):
                continue
            up = urlparse(url).path.lower()
            if not JH_DETAIL_RE.match(up) and "/news-release-details/" not in up and "/press-release-details/" not in up:
                continue
            if url in seen:
                continue
            seen.add(url)
            title = clean_text(up.rsplit('/', 1)[-1].replace('-', ' ').strip().title(), 220) or "Jack Henry press release"
            links.append((title, url, None))
            if len(links) >= MAX_LISTING_LINKS:
                break

    return links


# ============================
# ✅ NEW: TCS listing extractor (non-article DOM)
# ============================
        
TCS_PR_PATH_RE = re.compile(r"^/who-we-are/newsroom/press-release/", re.I)
        
def tcs_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    # Proxy responses are often Markdown rather than HTML.
    if html and "<html" not in html.lower() and "](" in html:
        md_re = re.compile(
            r"\[([^\]]{8,260})\]\((https?://www\.tcs\.com/who-we-are/newsroom/press-release/[^\)\s]+)\)",
            re.I,
        )
        for m in md_re.finditer(html):
            title = clean_text(m.group(1), 220)
            url = canonical_url(m.group(2))
            if not title or url in seen or not allowed_for_source("TCS", url):
                continue
            ctx = html[max(0, m.start() - 240): min(len(html), m.end() + 180)]
            dt = extract_any_date(ctx, source="TCS")
            seen.add(url)
            links.append((title, url, dt))
            if len(links) >= MAX_LISTING_LINKS:
                return links
        if links:
            return links

    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []
    strip_nav_like(container)
        
    for a in container.select('a[href^="/who-we-are/newsroom/press-release/"]'):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        if not TCS_PR_PATH_RE.match(href):
            continue
        
        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("TCS", url):
            continue
        
        raw_title = (a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            raw_title = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
        title = clean_text(raw_title, 220)
        if not title:
            continue
        if title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        if is_probably_nav_link("TCS", title, url):
            continue
        
        if url in seen:
            continue
        seen.add(url)
        
        dt = find_time_near_anchor(a, "TCS")
        if dt is None:
            wrap = a.find_parent(["li", "article", "div", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 800), source="TCS")
        
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break
        
    return links
        
        
# ============================
# ✅ NEW: Mambu listing extractor (JS page -> regex + proxy fallback)
# ============================
        
MAMBU_PR_RE = re.compile(r"/en/insights/press/[a-z0-9\-]+", re.I)
        
def mambu_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    # Try normal DOM first
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        container = soup
        
    strip_nav_like(container)
        
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()
        
    for a in container.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        if "/en/insights/press/" not in href:
            continue
        
        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("Mambu", url):
            continue
        
        title = clean_text((a.get_text(" ", strip=True) or "").strip(), 220)
        if not title or title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        
        if url in seen:
            continue
        seen.add(url)
        
        dt = find_time_near_anchor(a, "Mambu")
        if dt is None:
            wrap = a.find_parent(["li", "article", "div", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 900), source="Mambu")
        
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            return links
        
    # If page is JS-rendered and DOM found nothing, use regex on raw HTML (sometimes hrefs exist but not in main container)
    if not links:
        for m in MAMBU_PR_RE.finditer(html or ""):
            href = m.group(0)
            url = canonical_url(urljoin(page_url, href))
            if not allowed_for_source("Mambu", url):
                continue
            if url in seen:
                continue
            seen.add(url)
            links.append(("Mambu press release", url, None))
            if len(links) >= MAX_LISTING_LINKS:
                return links
        
    # Optional last resort: proxy the listing page itself and regex again
    if not links:
        proxy_html = polite_get(_jina_proxy_url(page_url))
        if proxy_html:
            for m in MAMBU_PR_RE.finditer(proxy_html or ""):
                href = m.group(0)
                url = canonical_url(urljoin(page_url, href))
                if not allowed_for_source("Mambu", url):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                links.append(("Mambu press release", url, None))
                if len(links) >= MAX_LISTING_LINKS:
                    break
        
    return links
        
        
# ============================
# ✅ NEW: Finastra listing extractor (fixes "Read the article" titles)
# ============================
        
FINASTRA_DETAIL_RE = re.compile(r"^/press-media/[a-z0-9\-]+", re.I)
        
def finastra_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []
        
    strip_nav_like(container)
        
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()
        
    # Finastra "media room" cards frequently have a CTA link text like "Read the article"
    for a in container.select('a[href^="/press-media/"]'):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        if not FINASTRA_DETAIL_RE.match(href):
            continue
        
        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("Finastra", url):
            continue
        
        raw = (a.get_text(" ", strip=True) or "").strip()
        if not raw:
            raw = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
        
        # If the link is just the CTA, pull the headline from the surrounding card.
        tl = (raw or "").strip().lower()
        if tl in {"read the article", "read article", "read more", "learn more", "more", "details"}:
            wrap = a.find_parent(["article", "li", "div", "section"]) or a.parent
            title = ""
            if wrap:
                h = wrap.find(["h1", "h2", "h3", "h4"])
                if h:
                    title = clean_text(h.get_text(" ", strip=True), 220)
        
                # fallback: sometimes headline is in a strong/span instead of heading tag
                if not title:
                    for sel in ["strong", ".title", ".headline", "[class*='title']", "[class*='headline']"]:
                        try:
                            el = wrap.select_one(sel)
                        except Exception:
                            el = None
                        if el and getattr(el, "get_text", None):
                            cand = clean_text(el.get_text(" ", strip=True), 220)
                            if cand and cand.lower() not in {"read the article", "read more", "learn more"}:
                                title = cand
                                break
        
            if not title:
                # last resort: use a non-generic label
                title = "Finastra press article"
        else:
            title = clean_text(raw, 220)
        
        if not title or len(title) < 8:
            continue
        if is_probably_nav_link("Finastra", title, url):
            continue
        if is_generic_listing_or_home("Finastra", title, url):
            continue
        
        if url in seen:
            continue
        seen.add(url)
        
        dt = find_time_near_anchor(a, "Finastra")
        if dt is None:
            wrap = a.find_parent(["article", "li", "div", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 900), source="Finastra")
        
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break
        
    return links
        
        


# ============================
# ✅ NEW: FASB "In the News" listing extractor
# ============================

FASB_IN_NEWS_PATH_RE = re.compile(r"^/news-and-meetings/in-the-news/(?!$)[^\s?#]+", re.I)

def fasb_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    """Extract real FASB 'In the News' items from https://www.fasb.org/news-and-meetings/in-the-news

    The listing page often uses CTA-style anchor text (e.g., 'Read more') which the generic
    extractor discards. This function pulls the headline from the surrounding card and attaches
    a nearby date (e.g., 'February 9, 2026') so the item passes the monthly window filter.

    This change is *only* for the FASB source.
    """
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    # If we fetched via proxy and got markdown-ish text, parse markdown links first.
    if html and ("<html" not in (html.lower())) and ("](" in html) and ("fasb.org" in html):
        md_re = re.compile(
            r"\[([^\]]{1,260})\]\((https?://(?:www\.)?fasb\.org/news-and-meetings/in-the-news/[^\)\s]+)\)",
            re.I,
        )
        for m in md_re.finditer(html):
            raw_title = clean_text(m.group(1), 220)
            url = canonical_url(m.group(2))
            if not allowed_for_source("FASB", url):
                continue

            # If title is generic, try to infer from nearby line text
            tl = (raw_title or "").strip().lower()
            title = raw_title
            if tl in {"read more", "learn more", "more", "details", "read the article", "read article"}:
                i0, _i1 = m.span()
                line_start = html.rfind("\n", 0, i0) + 1
                line = html[line_start:i0]
                line = re.sub(r"\s+", " ", (line or "").strip())
                # strip leading bullet-like characters
                line = re.sub(r"^[\-\*\u2022\s]+", "", line)
                # remove an obvious date prefix if present
                line2 = re.sub(r"^(?:[A-Z][a-z]{2,9})\.?\s+\d{1,2},\s+\d{4}\s*[:\-–—]\s*", "", line).strip()
                cand = clean_text(line2 or line, 220)
                if cand and cand.lower() not in GENERIC_TITLES and len(cand) >= 8:
                    title = cand
                else:
                    title = "FASB In the News"

            if url in seen:
                continue
            seen.add(url)

            ctx = html[max(0, m.start() - 240) : min(len(html), m.end() + 120)]
            dt = extract_any_date(ctx, source="FASB")
            links.append((title, url, dt))
            if len(links) >= MAX_LISTING_LINKS:
                return links

        if links:
            return links

    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    strip_nav_like(container)

    for a in container.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue

        full = canonical_url(urljoin(page_url, href))
        u = urlparse(full)
        pth = (u.path or "").rstrip("/")

        # Only accept real in-the-news detail pages (exclude the listing root itself).
        if not FASB_IN_NEWS_PATH_RE.match(pth + "/"):
            continue
        if pth.rstrip("/").lower() in {"/news-and-meetings/in-the-news"}:
            continue

        if not allowed_for_source("FASB", full):
            continue

        raw = (a.get_text(" ", strip=True) or "").strip()
        if not raw:
            raw = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()

        tl = (raw or "").strip().lower()
        title = ""

        if tl in {"read more", "learn more", "more", "details", "read the article", "read article"} or not raw:
            wrap = a.find_parent(["article", "li", "div", "section", "p"]) or a.parent
            if wrap:
                h = wrap.find(["h1", "h2", "h3", "h4", "strong"])
                if h:
                    title = clean_text(h.get_text(" ", strip=True), 220)

                if not title:
                    for sel in [".title", ".headline", "[class*='title']", "[class*='headline']"]:
                        try:
                            el = wrap.select_one(sel)
                        except Exception:
                            el = None
                        if el and getattr(el, "get_text", None):
                            cand = clean_text(el.get_text(" ", strip=True), 220)
                            if cand and cand.lower() not in GENERIC_TITLES:
                                title = cand
                                break

                if not title:
                    blob = clean_text(wrap.get_text(" ", strip=True), 700)
                    # Sometimes the first clause is the headline; keep it short.
                    title = clean_text(blob.split("…")[0], 220)
        else:
            title = clean_text(raw, 220)

        if not title or title.lower() in GENERIC_TITLES or len(title) < 8:
            continue
        if is_probably_nav_link("FASB", title, full):
            continue
        if is_generic_listing_or_home("FASB", title, full):
            continue

        if full in seen:
            continue
        seen.add(full)

        dt = find_time_near_anchor(a, "FASB")
        if dt is None:
            wrap = a.find_parent(["article", "li", "div", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 900), source="FASB")

        links.append((title, full, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    if not links:
        links = sitemap_links_for_source("FASB")

    return links


# ============================
# NACHA NEWS LISTING EXTRACTOR
# ============================

def nacha_links_single(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    """Extract real Nacha news cards from /news.

    Nacha's listing often uses a blank image/card anchor that contains the real
    href, with the headline and date as nearby sibling text. The generic extractor
    can miss those cards because the href anchor has no visible title text.
    """
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    strip_nav_like(container)

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    for a in container.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("NACHA", url):
            continue

        up = urlparse(url)
        pth = (up.path or "").rstrip("/")
        if pth == "/news" or not pth.startswith("/news/"):
            continue

        if up.query:
            non_utm = [k for k in parse_qs(up.query).keys() if not k.lower().startswith("utm_")]
            if non_utm:
                continue

        if url in seen:
            continue

        raw_title = (a.get_text(" ", strip=True) or "").strip()
        card = a.find_parent(["article", "li", "div", "section"]) or a.parent

        if (not raw_title) or raw_title.strip().lower() in {"read more", "learn more", "more", "details"}:
            if card:
                h = card.find(["h1", "h2", "h3", "h4"])
                if h:
                    raw_title = (h.get_text(" ", strip=True) or "").strip()

        title = clean_text(raw_title, 220)
        if not title or len(title) < 8:
            continue
        if title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        if is_probably_nav_link("NACHA", title, url):
            continue

        dt = None
        if card:
            dt = extract_any_date(clean_text(card.get_text(" ", strip=True), 900), source="NACHA")
        if dt is None:
            dt = find_time_near_anchor(a, "NACHA")

        seen.add(url)
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    links.sort(key=lambda t: (t[2] is None, t[2] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return links[:MAX_LISTING_LINKS]


def nacha_links(page_url: str, html: str, window_start: Optional[datetime]) -> List[Tuple[str, str, Optional[datetime]]]:
    return _paginate_listing("NACHA", page_url, html, window_start, nacha_links_single)

# ============================
# BankersOnline static/listing extractor
# ============================

def bankers_online_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    """Capture BankersOnline top-story links from both the live site and its
    static Daily/Weekly/Tech briefing pages. All existing pages remain enabled.
    """
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    for a in container.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or scheme(href) in GLOBAL_DENY_SCHEMES:
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("Bankers Online", url):
            continue

        pth = path(url).lower()
        # Keep actual top-story/detail links and dated static briefing pages;
        # reject the static index page itself and obvious navigation links.
        is_topstory = pth.startswith("/topstory/") and pth.rstrip("/") != "/topstory"
        is_static_detail = host(url) == "files.bankersonline.com" and pth.endswith((".html", ".htm")) and url != canonical_url(page_url)
        if not (is_topstory or is_static_detail):
            continue

        raw_title = (a.get_text(" ", strip=True) or "").strip()
        if not raw_title:
            raw_title = (a.get("aria-label") or "").strip() or (a.get("title") or "").strip()
        title = clean_text(raw_title, 240)
        if not title or len(title) < 8 or title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        if is_probably_nav_link("Bankers Online", title, url):
            continue
        if url in seen:
            continue

        dt = find_time_near_anchor(a, "Bankers Online")
        if dt is None:
            wrap = a.find_parent(["tr", "li", "article", "div", "section", "p"]) or a.parent
            if wrap:
                dt = extract_any_date(clean_text(wrap.get_text(" ", strip=True), 1000), source="Bankers Online")

        seen.add(url)
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    # The static briefing page itself is useful as a final fallback even when
    # its individual Top Story links are hidden or rewritten. Keep it as one
    # dated briefing item rather than reporting the source as empty.
    if not links and host(page_url) == "files.bankersonline.com":
        title_node = soup.find("h1") or soup.find("h2") or soup.find("title")
        title = clean_text(title_node.get_text(" ", strip=True) if title_node else "BankersOnline briefing", 240)
        dt = extract_any_date(clean_text(container.get_text(" ", strip=True), 2500), source="Bankers Online")
        if title and dt is not None:
            links.append((title, canonical_url(page_url), dt))

    return links



# ============================
# FATF
# ============================

def fatf_links(page_url: str, html: str) -> List[Tuple[str, str, Optional[datetime]]]:
    """Capture FATF news/publication detail pages without applying relevance filters.

    FATF frequently publishes dates as "16 July 2026". extract_any_date supports
    that format, and missing listing dates can be confirmed from the detail page
    by the normal bounded detail-fetch path.
    """
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup) or soup
    if not container:
        return []

    strip_nav_like(container)
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen: set[str] = set()

    for a in container.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or scheme(href) in GLOBAL_DENY_SCHEMES:
            continue

        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source("FATF", url):
            continue

        pth = path(url).rstrip("/").lower()
        # Listing/category pages are not articles. Keep individual news and
        # publication details, including the /content/fatf-gafi legacy path.
        if pth in {"/en/news", "/en/publications", "/en/the-fatf/news"}:
            continue
        if not (
            pth.startswith("/en/news/")
            or pth.startswith("/en/publications/")
            or pth.startswith("/content/fatf-gafi/en/publications/")
        ):
            continue
        if not pth.endswith((".html", ".htm")):
            continue

        if url in seen:
            continue

        raw_title = ""
        heading = a.find(["h1", "h2", "h3", "h4"])
        if heading:
            raw_title = heading.get_text(" ", strip=True) or ""
        if not raw_title:
            card = a.find_parent(["article", "li", "div", "section"]) or a.parent
            if card:
                h = card.find(["h1", "h2", "h3", "h4"])
                if h:
                    raw_title = h.get_text(" ", strip=True) or ""
        if not raw_title:
            raw_title = a.get_text(" ", strip=True) or (a.get("aria-label") or "") or (a.get("title") or "")

        title = clean_text(raw_title, 240)
        if not title or len(title) < 8:
            continue
        if title.lower() in {"read more", "read the report", "learn more", "more", "details"}:
            continue

        dt = find_time_near_anchor(a, "FATF")
        seen.add(url)
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break

    return links


# ============================
# MAIN CONTENT LINK ROUTER
# ============================
        
def main_content_links(source: str, page_url: str, html: str, window_start: datetime, window_end: datetime) -> List[Tuple[str, str, Optional[datetime]]]:
    if source == "OCC":
        return occ_links(page_url, html, window_start)
    if source == "OFAC":
        return ofac_links(page_url, html, window_start)
    if source == "Treasury":
        return treasury_links(page_url, html, window_start)
    if source == "White House":
        return whitehouse_links(page_url, html, window_start)
    if source == "Senate Banking":
        return senate_banking_links(page_url, html, window_start)
    if source == "FinCEN":
        return fincen_links(page_url, html, window_start)
        
        
    if source == "IRS":
        return irs_links(page_url, html, window_start, window_end)
        
    if source == "Mastercard":
        return mastercard_links(page_url, html)
    if source == "Visa":
        return visa_links(page_url, html)
    if source == "Freddie Mac":
        return freddiemac_globenewswire_links(page_url, html)
    if source == "CDIA":
        return cdia_links(page_url, html)
    if source == "FASB":
        return fasb_links(page_url, html)
    if source == "NACHA":
        return nacha_links(page_url, html, window_start)
    if source == "FATF":
        return fatf_links(page_url, html)

    if source == "FHLB MPF":
        return fhlbmpf_links(page_url, html)
        
    if source == "ABA":
        return aba_news_links(page_url, html)
    if source == "Wolters Kluwer":
        return wolterskluwer_news_links(page_url, html)
    if source == "Bankers Online":
        return bankers_online_links(page_url, html)
        
        
    # ✅ NEW vendor-specific extractors (fixes your missing pulls)
    if source == "Jack Henry":
        return jackhenry_links(page_url, html)
    if source == "TCS":
        return tcs_links(page_url, html)
    if source == "Mambu":
        return mambu_links(page_url, html)
    if source == "Finastra":
        return finastra_links(page_url, html)
        
    soup = BeautifulSoup(html, "html.parser")
    container = pick_container(soup)
    if not container:
        return []
        
    strip_nav_like(container)
        
    links: List[Tuple[str, str, Optional[datetime]]] = []
    seen = set()
        
    for a in container.find_all("a", href=True):
        if not is_likely_article_anchor(a):
            continue
        
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if scheme(href) in GLOBAL_DENY_SCHEMES or href.startswith("#"):
            continue
        
        url = canonical_url(urljoin(page_url, href))
        if not allowed_for_source(source, url):
            continue
        
        raw_title = a.get_text(" ", strip=True) or ""
        if not raw_title:
            raw_title = (a.get("aria-label") or "").strip()
        if not raw_title:
            raw_title = (a.get("title") or "").strip()
        
        title = clean_text(raw_title, 220)
        if not title:
            continue
        
        if title.lower() in {"read more", "learn more", "more", "details"}:
            continue
        if is_probably_nav_link(source, title, url):
            continue
        if is_generic_listing_or_home(source, title, url):
            continue
        
        if url in seen:
            continue
        seen.add(url)
        
        dt = find_time_near_anchor(a, source)
        links.append((title, url, dt))
        if len(links) >= MAX_LISTING_LINKS:
            break
        
    return links
        
        
# ============================
# SOURCES
# ============================
        
@dataclass
class SourcePage:
    source: str
    url: str
        
        
KNOWN_FEEDS: Dict[str, List[str]] = {
    "FRB": [
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "https://www.federalreserve.gov/feeds/press_bcreg.xml",
    ],
    "BleepingComputer": ["https://www.bleepingcomputer.com/feed/"],
    "Microsoft MSRC": ["https://api.msrc.microsoft.com/update-guide/rss"],
    "Fiserv": ["https://investors.fiserv.com/newsroom/rss"],  # ✅ unchanged

    # Official additive backups; existing listing pages remain active.
    "USDA Rural Development": ["https://www.rd.usda.gov/rss.xml"],
    "ABA": ["https://www.aba.com/rss/press"],
        
    # ✅ NEW: TCS press releases RSS (commonly referenced as Feedburner)
    "TCS": ["http://feeds2.feedburner.com/tcspress"],

    # BIS publishes an official RSS feed for the entire BIS website. Main-site
    # inclusion remains date-based; the Smart feed decides relevance later.
    "BIS": [
        "https://www.bis.org/doclist/rss_all_categories.rss",
        "https://www.bis.org/doclist/all_pressrels.rss",
        "https://www.bis.org/doclist/cbspeeches.rss",
        "https://www.bis.org/doclist/mgmtspeeches.rss",
        "https://www.bis.org/doclist/all_statistics.rss",
        "https://www.bis.org/doclist/bis_fsi_publs.rss",
    ],
}
        
        
def get_start_pages() -> List[SourcePage]:
    now_ct = utc_now().astimezone(CENTRAL_TZ)
    y = now_ct.year
    mc_year_pages = [
        f"https://www.mastercard.com/us/en/news-and-trends/press/{y}.html",
        f"https://www.mastercard.com/us/en/news-and-trends/press/{y-1}.html",
    ]
        
    # Previous calendar month window (CT) drives the RegMonthly timeframe
    _ws_utc, _we_utc, window_start_ct = monthly_window_utc(utc_now())
    irs_month_url = irs_news_releases_for_month_url(window_start_ct)
        
    pages = [
        # OFAC
        SourcePage("OFAC", "https://ofac.treasury.gov/recent-actions"),
        SourcePage("OFAC", "https://ofac.treasury.gov/recent-actions/enforcement-actions"),
        
        # Treasury Press Releases (OFAC tile)
        SourcePage("Treasury", "https://home.treasury.gov/news/press-releases"),
        
        # FinCEN (OFAC/AML tile)
        # FinCEN migrated to /news/... (older /news-room redirects)
        SourcePage("FinCEN", "https://www.fincen.gov/news/press-releases"),
        SourcePage("FinCEN", "https://www.fincen.gov/news/enforcement-actions"),
        SourcePage("FinCEN", "https://www.fincen.gov/news"),
        
        # IRS
        SourcePage("IRS", "https://www.irs.gov/newsroom"),
        SourcePage("IRS", irs_month_url),
        SourcePage("IRS", "https://www.irs.gov/downloads/rss"),
        
        # USDA RD
        SourcePage("USDA Rural Development", "https://www.rd.usda.gov/newsroom/news-releases"),
        SourcePage("USDA Rural Development", "https://www.rd.usda.gov/newsroom"),
        SourcePage("USDA Rural Development", "https://www.rd.usda.gov/newsroom/news-releases/usa"),
        
        # Banking regulators
        SourcePage("OCC", "https://www.occ.gov/news-issuances/news-releases/index-news-releases.html"),
        SourcePage("OCC", "https://www.occ.gov/news-events/newsroom/news-issuances-by-year/news-releases/index-news-releases.html"),
        SourcePage("OCC", f"https://www.occ.gov/news-events/newsroom/news-issuances-by-year/news-releases/{y}-news-releases.html"),
        SourcePage("OCC", "https://www.occ.gov/news-events/newsroom/index.html"),
        SourcePage("OCC", "https://www.occ.gov/news-events/index-news-events.html"),
        SourcePage("FDIC", "https://www.fdic.gov/news/press-releases/"),
        SourcePage("FRB", "https://www.federalreserve.gov/newsevents/pressreleases.htm"),
        SourcePage("FRB Payments", "https://www.federalreserve.gov/newsevents/pressreleases.htm"),
        
        # Mortgage / housing GSEs
        SourcePage("FHLB MPF", "https://www.fhlbmpf.com/program-guidelines/mpf-program-updates"),
        SourcePage("Fannie Mae", "https://www.fanniemae.com/rss/rss.xml"),
        SourcePage("Fannie Mae", "https://www.fanniemae.com/newsroom/fannie-mae-news"),
        SourcePage("Freddie Mac", "https://www.globenewswire.com/search/organization/Freddie%20Mac"),
        
        # Legislative / exec
        SourcePage("Senate Banking", "https://www.banking.senate.gov/newsroom/"),
        SourcePage("Senate Banking", "https://www.banking.senate.gov/newsroom/majority-press-releases"),
        SourcePage("Senate Banking", "https://www.banking.senate.gov/newsroom/minority-press-releases"),
	SourcePage("Senate Banking", "https://www.banking.senate.gov/newsroom"),
        SourcePage("House Financial Services", "https://financialservices.house.gov/news/"),
        SourcePage("White House", "https://www.whitehouse.gov/news/"),
        SourcePage("White House", "https://www.whitehouse.gov/presidential-actions/"),
        SourcePage("White House", "https://www.whitehouse.gov/briefings-statements/"),
        
        # Payments
        SourcePage("NACHA", "https://www.nacha.org/news"),

        # International AML / standard-setting
        SourcePage("FATF", "https://www.fatf-gafi.org/en/the-fatf/news.html"),
        SourcePage("FATF", "https://www.fatf-gafi.org/en/publications.html"),
        
        # Fintech vendors
        SourcePage("FIS", "https://www.investor.fisglobal.com/press-releases/"),
        SourcePage("Fiserv", "https://investors.fiserv.com/newsroom/news-releases"),
        SourcePage("Jack Henry", "https://ir.jackhenry.com/press-releases"),
        SourcePage("Jack Henry", "https://jkhy.client.shareholder.com/press-releases?mobile=1&view=all"),
        SourcePage("Finastra", "https://www.finastra.com/news-events/media-room"),
        SourcePage("TCS", "https://www.tcs.com/who-we-are/newsroom"),
        SourcePage("TCS", "https://www.tcs.com/who-we-are/newsroom/press-release"),
              
        # Payment Networks
        SourcePage("Visa", "https://usa.visa.com/about-visa/newsroom/press-releases-listing.html"),
        
        # Mastercard
        SourcePage("Mastercard", "https://www.mastercard.com/us/en/news-and-trends/press.html"),
        SourcePage("Mastercard", "https://www.mastercard.com/global/en/news-and-trends/press.html"),
    ]
        
    for u in mc_year_pages:
        pages.append(SourcePage("Mastercard", u))
        
    pages.extend(
        [
            # InfoSec (feed-only)
            SourcePage("BleepingComputer", "https://www.bleepingcomputer.com/"),
            SourcePage("Microsoft MSRC", "https://api.msrc.microsoft.com/"),
        
            # CDIA
            SourcePage("CDIA", "https://www.cdiaonline.org/news-events-blogs"),
        
            # FASB
            SourcePage("FASB", "https://www.fasb.org/news-and-meetings/in-the-news"),
            SourcePage("FASB", "https://www.fasb.org/news-and-meetings"),
        
            # Compliance Watch sources
            SourcePage("ABA", "https://www.aba.com/news-research"),
            SourcePage("ABA", "https://www.aba.com/news-research/all-news"),
            SourcePage("ABA", "https://www.aba.com/about-us/press-room"),
            SourcePage("TBA", "https://www.texasbankers.com/news/"),
            SourcePage("Wolters Kluwer", "https://www.wolterskluwer.com/en/news"),
            SourcePage("Wolters Kluwer", "https://www.wolterskluwer.com/en/news?f:contenttype=News%20Page%7CPress%20Release%20Page"),
            SourcePage("Bankers Online", "https://www.bankersonline.com/topstory"),
            SourcePage("Bankers Online", f"https://files.bankersonline.com/cb/{y}/cb.html"),
            SourcePage("Bankers Online", f"https://files.bankersonline.com/bb/{y}/bb.html"),
            SourcePage("Bankers Online", f"https://files.bankersonline.com/tt/{y}/tt.html"),
        ]
    )
        
    return pages
        
        

# ============================
# REGINFO.GOV (OIRA EO 12866 REVIEWS)
# ============================

REGINFO_COMPLETED_YTD_XML = "https://www.reginfo.gov/public/do/XMLViewFileAction?f=EO_RULE_COMPLETED_YTD.xml"
REGINFO_COMPLETED_30D_XML = "https://www.reginfo.gov/public/do/XMLViewFileAction?f=EO_RULE_COMPLETED_30_DAYS.xml"
REGINFO_UNDER_REVIEW_XML = "https://www.reginfo.gov/public/do/XMLViewFileAction?f=EO_RULES_UNDER_REVIEW.xml"
REGINFO_REVIEW_HOME = "https://www.reginfo.gov/public/do/eoPackageMain"


def _xml_local_name(tag: str) -> str:
    return str(tag or "").split("}")[-1].split(":")[-1]


def _xml_key(tag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _xml_local_name(tag).lower())


def _xml_record_fields(elem: Any) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for node in elem.iter():
        key = _xml_key(getattr(node, "tag", ""))
        if key:
            text = clean_text(" ".join(node.itertext()), 1200)
            if text and key not in fields:
                fields[key] = text

        # Some machine-readable exports put identifiers/dates in attributes.
        # Preserve those too so schema changes do not silently zero the tile.
        for attr_name, attr_value in getattr(node, "attrib", {}).items():
            akey = _xml_key(attr_name)
            aval = clean_text(str(attr_value), 1200)
            if akey and aval and akey not in fields:
                fields[akey] = aval
    return fields


def _xml_first(fields: Dict[str, str], *keys: str) -> str:
    for key in keys:
        value = fields.get(_xml_key(key), "")
        if value:
            return value
    return ""


def _xml_first_fuzzy(
    fields: Dict[str, str],
    exact: Tuple[str, ...] = (),
    contains_all: Tuple[Tuple[str, ...], ...] = (),
    contains_any: Tuple[str, ...] = (),
) -> str:
    """Read XML fields without depending on one exact RegInfo schema spelling."""
    value = _xml_first(fields, *exact)
    if value:
        return value

    for required in contains_all:
        for key, val in fields.items():
            if val and all(part in key for part in required):
                return val

    if contains_any:
        for key, val in fields.items():
            if val and any(part in key for part in contains_any):
                return val
    return ""


def _reginfo_fields_look_like_record(fields: Dict[str, str]) -> bool:
    title = _xml_first_fuzzy(
        fields,
        exact=("title", "ruletitle", "regulationtitle", "eoruletitle"),
        contains_any=("title",),
    )
    rin = _xml_first_fuzzy(
        fields,
        exact=("rin", "rinno", "rinnumber"),
        contains_any=("rin",),
    )
    date_val = _xml_first_fuzzy(
        fields,
        exact=("concludeddate", "conclusiondate", "receiveddate", "reviewdate", "date"),
        contains_all=(("date", "conclud"), ("date", "receiv"), ("date", "review")),
        contains_any=("date",),
    )
    return bool(title and (rin or date_val))


def _reginfo_record_nodes(root: Any) -> List[Any]:
    """Find the leaf-most elements that represent one RegInfo review record.

    RegInfo's XML field/tag names have varied. The previous implementation
    required specific *direct-child* names and could therefore return zero records
    even when the official XML was populated. This version scores flattened fields
    and rejects parent containers when a child already looks like a record.
    """
    candidates: List[Any] = []
    for elem in root.iter():
        fields = _xml_record_fields(elem)
        if not _reginfo_fields_look_like_record(fields):
            continue

        child_is_record = False
        for child in list(elem):
            try:
                if _reginfo_fields_look_like_record(_xml_record_fields(child)):
                    child_is_record = True
                    break
            except Exception:
                pass
        if child_is_record:
            continue
        candidates.append(elem)

    return candidates


def _reginfo_detail_url(fields: Dict[str, str], *, mode: str = "", date_token: str = "") -> str:
    rrid = _xml_first_fuzzy(
        fields,
        exact=("rrid", "reviewid", "review_id", "eo_review_id", "id"),
        contains_all=(("review", "id"),),
        contains_any=("rrid",),
    )
    if rrid:
        m = re.search(r"\b(\d{2,})\b", rrid)
        if m:
            return f"https://www.reginfo.gov/public/do/eoDetails?rrid={m.group(1)}"

    # RegInfo does not always expose rrid in every XML report. Falling back to the
    # same eoPackageMain URL for every review caused URL-based dedupe to collapse
    # many legitimate reviews. A RIN search is useful to the user and stays unique.
    rin = _xml_first_fuzzy(
        fields,
        exact=("rin", "rinno", "rinnumber"),
        contains_any=("rin",),
    ).strip()
    if rin:
        safe_rin = re.sub(r"[^A-Za-z0-9\-]", "", rin)
        suffix = ""
        if date_token:
            suffix += f"&regmonthly_date={re.sub(r'[^0-9A-Za-z\-]', '', date_token)[:20]}"
        if mode:
            suffix += f"&regmonthly_mode={re.sub(r'[^a-z]', '', mode.lower())[:20]}"
        return f"https://www.reginfo.gov/public/Forward?SearchTarget=RegReview&textfield={safe_rin}{suffix}"
    return REGINFO_REVIEW_HOME


def _items_from_reginfo_xml(xml_bytes: bytes, start: datetime, end: datetime, mode: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        print(f"[warn] RegInfo XML parse failed ({mode}): {e}", flush=True)
        return []

    nodes = _reginfo_record_nodes(root)
    print(f"[xml] RegInfo {mode}: detected {len(nodes)} candidate review records", flush=True)

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for elem in nodes:
        fields = _xml_record_fields(elem)
        title = _xml_first_fuzzy(
            fields,
            exact=("title", "ruletitle", "regulationtitle", "eoruletitle", "rulename"),
            contains_all=(("rule", "title"),),
            contains_any=("title",),
        )
        rin = _xml_first_fuzzy(
            fields,
            exact=("rin", "rinno", "rinnumber"),
            contains_any=("rin",),
        )
        agency = _xml_first_fuzzy(
            fields,
            exact=("agencyname", "agency", "agencycode"),
            contains_all=(("agency", "name"),),
            contains_any=("agency",),
        )
        subagency = _xml_first_fuzzy(
            fields,
            exact=("subagencyname", "subagency"),
            contains_all=(("subagency", "name"),),
            contains_any=("subagency",),
        )
        stage = _xml_first_fuzzy(
            fields,
            exact=("stage", "rulemakingstage"),
            contains_any=("stage",),
        )
        action = _xml_first_fuzzy(
            fields,
            exact=("concludedaction", "conclusionaction", "action"),
            contains_all=(("conclu", "action"),),
        )

        if mode == "completed":
            date_raw = _xml_first_fuzzy(
                fields,
                exact=("concludeddate", "conclusiondate", "dateconcluded", "reviewdate", "date"),
                contains_all=(("date", "conclud"), ("conclud", "date"), ("date", "complet")),
                contains_any=("concludeddate", "completiondate"),
            )
            status = "OIRA review completed"
        else:
            date_raw = _xml_first_fuzzy(
                fields,
                exact=("receiveddate", "reviewreceiveddate", "datereceived", "reviewdate", "date"),
                contains_all=(("date", "receiv"), ("receiv", "date"), ("review", "date")),
                contains_any=("receiveddate",),
            )
            status = "Pending OIRA review"

        dt = parse_date(date_raw)
        if dt is None:
            dt = extract_any_date(date_raw, source="RegInfo.gov")
        if dt is None or not in_window(dt, start, end):
            continue
        if not title:
            continue

        date_token = dt.strftime("%Y-%m-%d")
        url = _reginfo_detail_url(fields, mode=mode, date_token=date_token)
        unique = f"{mode}|{rin}|{title}|{iso_z(dt)}|{url}"
        if unique in seen:
            continue
        seen.add(unique)

        bits = [status]
        if rin:
            bits.append(f"RIN {rin}")
        org = " / ".join(x for x in [agency, subagency] if x)
        if org:
            bits.append(org)
        if stage:
            bits.append(stage)
        if action and mode == "completed":
            bits.append(action)

        out.append({
            "category": CATEGORY_BY_SOURCE.get("RegInfo.gov", "Regulatory Review"),
            "source": "RegInfo.gov",
            "title": clean_text(title, 320),
            "published_at": iso_z(dt),
            "url": url,
            "summary": clean_text(" | ".join(bits), 500),
        })

    return out


def items_from_reginfo_reviews(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Load dated pending and completed OIRA reviews from RegInfo's official XML exports."""
    out: List[Dict[str, Any]] = []
    for mode, url in [
        # Daily 30-day file is additive; YTD preserves earlier target-month reviews.
        ("completed", REGINFO_COMPLETED_30D_XML),
        ("completed", REGINFO_COMPLETED_YTD_XML),
        ("pending", REGINFO_UNDER_REVIEW_XML),
    ]:
        raw = fetch_bytes(url, timeout=45)
        if not raw:
            print(f"[warn] RegInfo {mode} XML unavailable: {url}", flush=True)
            continue
        got = _items_from_reginfo_xml(raw, start, end, mode)
        out.extend(got)
        print(f"[xml] RegInfo {mode}: {len(got)} items from {url}", flush=True)

    by_key: Dict[str, Dict[str, Any]] = {}
    for it in out:
        key = "|".join([
            str(it.get("published_at") or ""),
            normalized_dedupe_title(str(it.get("title") or "")),
            str(it.get("summary") or "").split(" | ")[0],
        ])
        by_key.setdefault(key, it)
    return list(by_key.values())


# ============================
# STATIC EXPORTS (NO JS)
# ============================
        
def render_raw_html(payload: Dict[str, Any]) -> str:
    ws = str(payload.get("window_start", ""))
    we = str(payload.get("window_end", ""))
    gen_ct = escape(str(payload.get("generated_at_ct", "")))
    gen_utc = escape(str(payload.get("generated_at_utc", "")))
    items = payload.get("items", []) or []
    base_href = f"{PUBLIC_BASE.rstrip('/')}/raw/"
        
    parts: List[str] = []
    for it in items:
        cat = escape(str(it.get("category", "")))
        src = escape(str(it.get("source", "")))
        title = escape(str(it.get("title", "")))
        url = escape(str(it.get("url", "")))
        pub = escape(str(it.get("published_at", "")))
        summary = escape(str(it.get("summary", "") or ""))
        
        parts.append(
            "\n".join(
                [
                    '<article class="card">',
                    '  <div class="meta">',
                    f'    <span class="src">[{src}]</span>',
                    (f'    <span class="cat">{cat}</span>' if cat else ""),
                    f'    <span class="pub">{pub}</span>',
                    "  </div>",
                    f'  <h2 class="title"><a href="{url}">{title}</a></h2>',
                    (f'  <p class="sum">{summary}</p>' if summary else ""),
                    f'  <p class="url">{url}</p>',
                    "</article>",
                ]
            )
        )
        
    body = "\n".join(parts) if parts else "<p>No items in window.</p>"
        
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>RegDashboard – Static Export</title>
  <meta name="description" content="Static export of RegDashboard items (no JavaScript required)." />
  <base href="{escape(base_href)}">
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; line-height: 1.35; }}
    header {{ margin-bottom: 18px; }}
    .small {{ color: #444; font-size: 13px; }}
    .links a {{ margin-right: 12px; }}
    .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 14px; margin: 12px 0; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; color: #555; margin-bottom: 6px; }}
    .title {{ margin: 0 0 6px 0; font-size: 16px; }}
    .sum {{ margin: 0 0 6px 0; color: #222; }}
    .url {{ margin: 0; font-size: 12px; color: #666; word-break: break-word; }}
    a {{ text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 6px; }}
  </style>
</head>
<body>
  <header>
    <h1>RegDashboard — Static Export</h1>
    <div class="small">Window: <code>{escape(ws)}</code> → <code>{escape(we)}</code> (UTC)</div>
    <div class="small">Last updated: <code>{gen_ct}</code> (CT) — <code>{gen_utc}</code> (UTC)</div>
    <div class="small links">
      <a href="./items.md">items.md</a>
      <a href="./items.txt">items.txt</a>
      <a href="./items.ndjson">items.ndjson</a>
      <a href="./items-array.json">items-array.json</a>
      <a href="./items-smart-100.json">items-smart-100.json</a>
      <a href="../">Back to app</a>
    </div>
  </header>
        
  {body}
</body>
</html>
"""
        
        
def render_raw_md(payload: Dict[str, Any]) -> str:
    ws = payload.get("window_start", "")
    we = payload.get("window_end", "")
    gen_ct = str(payload.get("generated_at_ct", "")).strip()
    gen_utc = str(payload.get("generated_at_utc", "")).strip()
    items = payload.get("items", []) or []
        
    lines: List[str] = []
    lines.append("# RegDashboard — Export")
    lines.append("")
    lines.append(f"Window: `{ws}` → `{we}` (UTC)")
    lines.append(f"Last updated: `{gen_ct}` (CT) — `{gen_utc}` (UTC)")
    lines.append("")
        
    for it in items:
        title = (it.get("title") or "").strip()
        source = (it.get("source") or "").strip()
        category = (it.get("category") or "").strip()
        pub = (it.get("published_at") or "").strip()
        url = (it.get("url") or "").strip()
        summary = (it.get("summary") or "").strip()
        
        lines.append(f"## {title}")
        lines.append(f"- Source: {source}")
        lines.append(f"- Category: {category}")
        lines.append(f"- Published: {pub}")
        lines.append(f"- URL: {url}")
        if summary:
            lines.append("")
            lines.append(summary)
        lines.append("")
        
    return "\n".join(lines).strip() + "\n"
        
        
def render_raw_txt(payload: Dict[str, Any]) -> str:
    items = payload.get("items", []) or []
    out: List[str] = []
    for it in items:
        out.append(str(it.get("category", "")).strip())
        out.append(str(it.get("source", "")).strip())
        out.append(str(it.get("published_at", "")).strip())
        out.append(str(it.get("title", "")).strip())
        out.append(str(it.get("url", "")).strip())
        summary = str(it.get("summary", "") or "").strip()
        if summary:
            out.append(summary)
        out.append("-" * 60)
    return "\n".join(out).strip() + "\n"
        
        
def render_print_html(payload: Dict[str, Any]) -> str:
    ws = str(payload.get("window_start", ""))
    we = str(payload.get("window_end", ""))
    gen_ct = escape(str(payload.get("generated_at_ct", "")))
    gen_utc = escape(str(payload.get("generated_at_utc", "")))
    items = payload.get("items", []) or []
        
    header = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>RegDashboard – Print (All Items)</title>
  <meta name="description" content="Single-file print view of all RegDashboard items. No JavaScript." />
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 28px; line-height: 1.35; }}
    h1 {{ margin: 0 0 6px 0; }}
    .meta {{ color: #444; font-size: 13px; margin-bottom: 10px; }}
    article {{ border-top: 1px solid #e5e5e5; padding-top: 12px; margin-top: 12px; }}
    .k {{ display: inline-block; min-width: 90px; color: #555; }}
    .v {{ color: #111; }}
    a {{ word-break: break-word; }}
  </style>
</head>
<body>
  <h1>RegDashboard — Print (All Items)</h1>
  <div class="meta">Window: <strong>{escape(ws)}</strong> → <strong>{escape(we)}</strong> (UTC)</div>
  <div class="meta">Last updated: <strong>{gen_ct}</strong> (CT) — <strong>{gen_utc}</strong> (UTC)</div>
"""
    parts: List[str] = [header]
    for it in items:
        cat = escape(str(it.get("category", "")).strip())
        src = escape(str(it.get("source", "")).strip())
        pub = escape(str(it.get("published_at", "")).strip())
        title = escape(str(it.get("title", "")).strip())
        url = str(it.get("url", "")).strip()
        url_esc = escape(url)
        summary = escape(str(it.get("summary", "") or "").strip())
        
        parts.append("<article>")
        parts.append(f"<div><span class='k'>Category</span><span class='v'>{cat}</span></div>")
        parts.append(f"<div><span class='k'>Source</span><span class='v'>{src}</span></div>")
        parts.append(f"<div><span class='k'>Published</span><span class='v'>{pub}</span></div>")
        parts.append(f"<div><span class='k'>Title</span><span class='v'><a href='{url_esc}'>{title}</a></span></div>")
        parts.append(f"<div><span class='k'>URL</span><span class='v'>{url_esc}</span></div>")
        if summary:
            parts.append(f"<div style='margin-top:6px'><span class='k'>Summary</span><span class='v'>{summary}</span></div>")
        parts.append("</article>")
        
    parts.append("</body></html>\n")
    return "\n".join(parts)
        
        

# ============================
# POWER AUTOMATE SMART EXPORT FILTERS
# ============================

SMART_100_LIMIT = 150  # keep the existing items-smart-100.json endpoint, but expand it to 150 items

SMART_SOURCE_WEIGHTS: Dict[str, int] = {
    "CFPB": 38,
    "OCC": 38,
    "FDIC": 38,
    "Federal Reserve": 38,
    "FRB": 38,
    "FFIEC": 38,
    "FinCEN": 38,
    "OFAC": 38,
    "Federal Register": 4,
    "NACHA": 32,
    "Treasury": 30,
    "Fannie Mae": 24,
    "Freddie Mac": 24,
    "FHLB MPF": 24,
    "Visa": 16,
    "Mastercard": 16,
    "FIS": 12,
    "Fiserv": 12,
    "Jack Henry": 12,
    "Finastra": 12,
    "Temenos": 12,
    "Mambu": 12,
    "TCS": 10,
    "BIS": 34,
    "FATF": 38,
    "RegInfo.gov": 4,
}

SMART_KEYWORD_WEIGHTS: Dict[str, int] = {
    "bank": 18, "banking": 18, "national bank": 24, "community bank": 22,
    "credit union": 18, "fintech": 18, "payments": 18, "payment": 14,
    "ach": 20, "nacha": 20, "wire transfer": 18, "debit": 12, "credit card": 16,
    "card network": 14, "mortgage": 18, "lending": 18, "loan": 16,
    "deposit": 16, "bsa": 22, "aml": 22, "ofac": 24, "sanctions": 22,
    "fincen": 22, "cra": 20, "udaap": 24, "fair lending": 24,
    "ecoa": 20, "regulation b": 22, "regulation z": 22, "reg z": 22,
    "tila": 20, "regulation x": 22, "reg x": 22, "respa": 20,
    "cfpb": 24, "occ": 24, "fdic": 24, "federal reserve": 24,
    "ffiec": 24, "federal register": 18, "capital": 16, "liquidity": 18,
    "model risk": 22, "third-party risk": 22, "third party risk": 22,
    "cybersecurity": 20, "information security": 18, "privacy": 16,
    "consumer compliance": 22,
    "guidance": 12, "rulemaking": 16, "final rule": 18,
    "proposed rule": 18, "supervisory": 16, "examination": 16, "risk management": 18,
}

SMART_NOISE_WEIGHTS: Dict[str, int] = {
    "quarterly": -999,
    "earnings": -40, "investor relations": -45, "shareholder": -35,
    "stock repurchase": -45, "dividend": -40, "appoints": -22,
    "appointment": -22, "conference": -18, "webinar": -12, "award": -25,
    "sponsorship": -24, "charity": -24, "philanthropy": -24,
    "podcast": -16, "survey": -10,
    "enforcement action": -999, "enforcement actions": -999,
    "civil money penalty": -999, "consent order": -999,
    "cease and desist": -999, "prohibition order": -999,
    "banks examined": -999, "list of banks examined": -999,
    "cra examination schedule": -999, "performance evaluations": -80,
    "supervisory highlights": -999, "supervisory highlight": -999,
}


SMART_ENFORCEMENT_TERMS = (
    "enforcement action",
    "enforcement actions",
    "civil money penalty",
    "consent order",
    "cease and desist",
    "prohibition order",
    "formal agreement",
    "settlement agreement",
)

SMART_BANK_EXAM_LIST_TERMS = (
    "banks examined",
    "list of banks examined",
    "banks scheduled for examination",
    "institutions examined",
    "institutions scheduled for examination",
    "cra examination schedule",
    "community reinvestment act performance evaluations",
    "cra performance evaluations",
    "performance evaluations of financial institutions",
    "examined for cra compliance",
)

SMART_SUPERVISORY_HIGHLIGHTS_TERMS = (
    "supervisory highlights",
    "supervisory highlight",
    "cfpb supervisory highlights",
    "supervision highlights",
)


def _smart_text(item: Dict[str, Any]) -> str:
    return " ".join(
        str(item.get(k, "") or "")
        for k in ("title", "summary", "content", "category", "source", "url")
    ).lower()


def exclude_quarterly(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Smart-feed helper only; the main dashboard intentionally keeps quarterly items."""
    return [it for it in items if "quarterly" not in _smart_text(it)]


def is_smart_enforcement_action(item: Dict[str, Any]) -> bool:
    """Identify enforcement-action articles for exclusion from the Power Automate smart feed only."""
    text = _smart_text(item)
    if any(term in text for term in SMART_ENFORCEMENT_TERMS):
        return True

    category = str(item.get("category", "") or "").lower()
    title = str(item.get("title", "") or "").lower()
    source = str(item.get("source", "") or "").lower()

    # Catch regulator pages that categorize enforcement releases without using the exact phrase.
    if "enforcement" in category and any(reg in source for reg in ["occ", "fdic", "federal reserve", "cfpb", "fincen", "ofac"]):
        return True
    if title.startswith("enforcement actions") or title.startswith("enforcement action"):
        return True

    return False


def is_smart_bank_exam_list(item: Dict[str, Any]) -> bool:
    """Identify list-style bank examination / CRA evaluation articles for smart-feed exclusion only."""
    text = _smart_text(item)
    title = str(item.get("title", "") or "").lower()
    source = str(item.get("source", "") or "").lower()

    if any(term in text for term in SMART_BANK_EXAM_LIST_TERMS):
        return True

    # Target recurring regulator list pages, not substantive examination guidance.
    if ("examined" in title or "performance evaluations" in title) and any(
        reg in source for reg in ["occ", "fdic", "federal reserve", "frb"]
    ):
        return True

    return False


def is_smart_supervisory_highlights(item: Dict[str, Any]) -> bool:
    """Identify Supervisory Highlights publications for smart-feed exclusion only."""
    text = _smart_text(item)
    title = str(item.get("title", "") or "").lower()
    source = str(item.get("source", "") or "").lower()
    url = str(item.get("url", "") or "").lower()

    if any(term in text for term in SMART_SUPERVISORY_HIGHLIGHTS_TERMS):
        return True

    # Catch CFPB URL patterns for Supervisory Highlights even when the title changes.
    if "cfpb" in source and "supervisory-highlights" in url:
        return True

    return False


def smart_relevance_score(item: Dict[str, Any]) -> int:
    """Score an item for bank/fintech relevance without changing the item shape."""
    text = _smart_text(item)
    score = 0

    source = str(item.get("source") or item.get("category") or "")
    category = str(item.get("category") or "")
    source_blob = f"{source} {category}"
    for name, weight in SMART_SOURCE_WEIGHTS.items():
        if name.lower() in source_blob.lower():
            score += weight

    for term, weight in SMART_KEYWORD_WEIGHTS.items():
        if term in text:
            score += weight

    for term, weight in SMART_NOISE_WEIGHTS.items():
        if term in text:
            score += weight

    # Regulatory/legal verbs tend to be more useful for bank compliance teams,
    # but enforcement-specific articles are removed from the smart feed before ranking.
    if any(term in text for term in ["rule", "guidance", "bulletin", "advisory", "supervisory", "examination"]):
        score += 12

    return score


def _smart_published_sort_value(item: Dict[str, Any]) -> datetime:
    """Return a safe UTC datetime for Smart feed final date ordering."""
    dt = parse_date(str(item.get("published_at", "") or ""))
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt


def smart_top_items(items: List[Dict[str, Any]], limit: int = SMART_100_LIMIT) -> List[Dict[str, Any]]:
    """Return the best filtered Smart items, then publish them in newest-first date order.

    Selection still uses the bank/fintech relevance score so the endpoint contains
    the best 150 articles. Final output order and smart_index are based on
    published_at descending so Power Automate can sort/display by smart_index and
    preserve date order.
    """
    smart_pool = [
        it for it in items
        if "quarterly" not in _smart_text(it)
        and not is_smart_enforcement_action(it)
        and not is_smart_bank_exam_list(it)
        and not is_smart_supervisory_highlights(it)
    ]

    # First choose the best 150 by relevance.
    ranked_by_relevance = sorted(
        smart_pool,
        key=lambda it: (smart_relevance_score(it), _smart_published_sort_value(it)),
        reverse=True,
    )[:limit]

    # Then publish those selected articles in strict newest-first date order.
    final_order = sorted(
        ranked_by_relevance,
        key=lambda it: (_smart_published_sort_value(it), smart_relevance_score(it)),
        reverse=True,
    )

    indexed: List[Dict[str, Any]] = []
    for idx, it in enumerate(final_order, start=1):
        item_copy = dict(it)
        item_copy["smart_index"] = idx
        indexed.append(item_copy)
    return indexed


def write_raw_aux_files() -> None:
    base = PUBLIC_BASE.rstrip("/")
    raw_base = f"{base}/raw"
    print_base = f"{base}/print"
        
    with open(RAW_ROBOTS_PATH, "w", encoding="utf-8") as f:
        f.write("User-agent: *\nAllow: /\n")
        
    with open(RAW_SITEMAP_PATH, "w", encoding="utf-8") as f:
        f.write(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{raw_base}/index.html</loc></url>
  <url><loc>{raw_base}/items.md</loc></url>
  <url><loc>{raw_base}/items.txt</loc></url>
  <url><loc>{raw_base}/items.ndjson</loc></url>
  <url><loc>{raw_base}/items-array.json</loc></url>
  <url><loc>{raw_base}/items-smart-100.json</loc></url>
  <url><loc>{print_base}/items.html</loc></url>
</urlset>
"""
        )
        
        
# ============================
# BUILD
# ============================
        
def _fedreg_group_rank(it: Dict[str, Any]) -> int:
    # Higher is better
    if str(it.get("category") or "") != "Federal Register":
        return 0
    gt = str(it.get("fedreg_group_type") or "").strip().lower()
    if gt == "agency":
        return 3
    if gt == "topic":
        return 2
    if gt == "section":
        return 1
    return 0
        
        


# ============================
# PUBLISH DEDUPE / LANDING-PAGE SUPPRESSION
# ============================

def normalized_dedupe_title(title: str) -> str:
    """Normalize titles so same-source duplicates compare consistently."""
    s = (title or "").strip().lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    # Remove common agency-release labels without removing the substantive title.
    s = re.sub(
        r"^\s*(readout|press release|news release|statement|remarks|fact sheet|notice|bulletin)\s*[:\-–—]+\s*",
        "",
        s,
    )
    s = re.sub(r"&", " and ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def canonical_dedupe_url(url: str) -> str:
    """Canonical URL for duplicate detection; keeps meaningful query params only."""
    try:
        from urllib.parse import urlencode, urlunparse

        raw, _frag = urldefrag(url or "")
        u = urlparse(raw.strip())
        if not u.scheme or not u.netloc:
            return raw.strip()

        qs = parse_qs(u.query or "", keep_blank_values=False)
        kept = {
            k: v
            for k, v in qs.items()
            if not k.lower().startswith("utm_")
            and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "source"}
        }
        query = urlencode(sorted(kept.items()), doseq=True) if kept else ""
        path_only = re.sub(r"/{2,}", "/", u.path or "/")
        if path_only != "/":
            path_only = path_only.rstrip("/")
        return urlunparse((u.scheme.lower(), u.netloc.lower(), path_only, "", query, ""))
    except Exception:
        return canonical_url(url or "")


def is_treasury_specific_press_release_url(url: str) -> bool:
    try:
        p = (urlparse(url).path or "").rstrip("/").lower()
        # Current Treasury detail pages commonly look like sb0547/jy####.
        return bool(re.fullmatch(r"/news/press-releases/[a-z]{1,4}\d{2,}", p))
    except Exception:
        return False


def item_specificity_score(it: Dict[str, Any]) -> int:
    """Higher score wins when the same source emits duplicate article candidates."""
    source = str(it.get("source") or "")
    title = str(it.get("title") or "")
    url = str(it.get("url") or "")
    summary = str(it.get("summary") or "")

    score = 0
    if summary.strip():
        score += 20
    score += min(len(title.strip()), 180) // 6

    try:
        p = (urlparse(url).path or "").strip("/")
        score += min(len([x for x in p.split("/") if x]), 8) * 4
        score += min(len(p), 220) // 20
    except Exception:
        pass

    if source == "Treasury":
        if is_treasury_specific_press_release_url(url):
            score += 75
        if is_generic_listing_or_home(source, title, url):
            score -= 200

    if normalized_dedupe_title(title) in GENERIC_TITLES:
        score -= 120

    return score


def dedupe_items_with_preference(items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Remove only non-article landing pages and true same-source URL duplicates.

    Main-dashboard inclusion is deliberately *not* based on title similarity,
    keywords, relevance, quarterly status, enforcement status, or other content
    qualifiers. If two dated articles have different URLs, both remain on the
    main site even when their titles are identical. the Smart feed owns filtering.
    """
    dropped: List[Dict[str, str]] = []

    filtered: List[Dict[str, Any]] = []
    for it in items:
        source = str(it.get("source") or "")
        title = str(it.get("title") or "")
        url = str(it.get("url") or "")
        if is_generic_listing_or_home(source, title, url) or is_probably_nav_link(source, title, url):
            dropped.append({
                "reason": "generic listing/category page",
                "source": source,
                "title": title,
                "url": url,
            })
            continue
        filtered.append(it)

    by_url: Dict[str, Dict[str, Any]] = {}

    def prefer(candidate: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
        if str(candidate.get("category") or "") == "Federal Register" and str(current.get("category") or "") == "Federal Register":
            if _fedreg_group_rank(candidate) > _fedreg_group_rank(current):
                return candidate
        if item_specificity_score(candidate) > item_specificity_score(current):
            return candidate
        if (not current.get("summary")) and candidate.get("summary"):
            return candidate
        return current

    for it in sorted(filtered, key=lambda x: str(x.get("published_at") or ""), reverse=True):
        key = f"{str(it.get('source') or '').strip().lower()}|{canonical_dedupe_url(str(it.get('url') or ''))}"
        cur = by_url.get(key)
        if cur is None:
            by_url[key] = it
            continue
        winner = prefer(it, cur)
        loser = cur if winner is it else it
        by_url[key] = winner
        dropped.append({
            "reason": "same-source same-url duplicate",
            "source": str(loser.get("source") or ""),
            "title": str(loser.get("title") or ""),
            "url": str(loser.get("url") or ""),
        })

    return list(by_url.values()), dropped

def build() -> None:
    now_utc = utc_now()
    now_ct = now_utc.astimezone(CENTRAL_TZ).replace(microsecond=0)
        
    window_start, window_end, _window_start_ct = monthly_window_utc(now_utc)
        
    all_items: List[Dict[str, Any]] = []
    global_detail_fetches = 0
    per_source_detail_fetches: Dict[str, int] = {}
        
    pages_by_source: Dict[str, List[str]] = {}
    for sp in get_start_pages():
        pages_by_source.setdefault(sp.source, []).append(sp.url)
        
    for src in set(KNOWN_FEEDS.keys()) | {"Federal Register", "RegInfo.gov"}:
        pages_by_source.setdefault(src, [])
        
    for source, pages in pages_by_source.items():
        print(f"\n===== SOURCE: {source} =====", flush=True)
        source_items_before = len(all_items)
        
        if source == "Federal Register":
            got = items_from_federal_register_all(window_start, window_end)
            if got:
                all_items.extend(got)
                print(f"[api] Federal Register: {len(got)} dated items (no relevance filters)", flush=True)
            else:
                print("[note] Federal Register: no qualifying items in window (or API issue).", flush=True)
            continue

        if source == "RegInfo.gov":
            got = items_from_reginfo_reviews(window_start, window_end)
            if got:
                all_items.extend(got)
                print(f"[xml] RegInfo.gov: {len(got)} dated review items", flush=True)
            else:
                print("[note] RegInfo.gov: no qualifying items in window (or XML issue).", flush=True)
            continue
        
        for fu in KNOWN_FEEDS.get(source, []):
            got = items_from_feed(source, fu, window_start, window_end)
            if got:
                all_items.extend(got)
                print(f"[feed-known] {len(got)} items from {fu}", flush=True)

        if source == "BIS":
            # RSS is rolling and can age the entire prior month out of the feed.
            # Always backfill from the official sitemap, then rely on normal URL
            # dedupe to merge any overlap with RSS.
            got = items_from_bis_archive(window_start, window_end)
            if got:
                all_items.extend(got)
                print(f"[sitemap] BIS: {len(got)} dated archive items", flush=True)
            else:
                print("[note] BIS sitemap backfill produced no target-month items.", flush=True)
            continue
        
        for page_url in pages:
            print(f"\n[source] {source} :: {page_url}", flush=True)
        
            if looks_like_feed_url(page_url):
                got = items_from_feed(source, page_url, window_start, window_end)
                all_items.extend(got)
                print(f"[feed-direct] {len(got)} items from {page_url}", flush=True)
                continue
        
            html = polite_get(page_url)
            if not html:
                print("[skip] no html", flush=True)
                continue
        
            if looks_js_rendered(html):
                print("[note] page looks JS-rendered; using strict extraction (may be limited)", flush=True)
        
            feed_urls = discover_feeds(page_url, html)
            feed_items_total = 0
            for fu in feed_urls:
                got = items_from_feed(source, fu, window_start, window_end)
                if got:
                    all_items.extend(got)
                    feed_items_total += len(got)
                    print(f"[feed] {len(got)} items from {fu}", flush=True)
            print(f"[feed] total: {feed_items_total} | feeds found: {len(feed_urls)}", flush=True)
        
            listing_links = main_content_links(source, page_url, html, window_start, window_end)
            print(f"[list] links captured: {len(listing_links)}", flush=True)
        
            src_used = per_source_detail_fetches.get(source, 0)
            src_cap = PER_SOURCE_DETAIL_CAP.get(source, DEFAULT_SOURCE_DETAIL_CAP)
        
            for title, url, dt in listing_links:
                if is_probably_nav_link(source, title, url):
                    continue
                if is_generic_listing_or_home(source, title, url):
                    continue
        
                snippet = ""
        
                # FDIC listing pages can expose template/sidebar dates that differ from
                # the actual press-release date. Confirm FDIC dates from the detail page
                # only; other sources keep their existing date behavior unchanged.
                if source == "FDIC" and src_cap > 0:
                    if global_detail_fetches < GLOBAL_DETAIL_FETCH_CAP and src_used < src_cap:
                        detail_html = polite_get(url)
                        if detail_html:
                            global_detail_fetches += 1
                            src_used += 1
                            per_source_detail_fetches[source] = src_used

                            dt2, snippet2 = extract_published_from_detail(url, detail_html, source=source)
                            if dt2:
                                dt = dt2
                            if snippet2:
                                snippet = snippet2

                # Visa listing dates can shift between DD/MM/YYYY and local renderings.
                # Confirm Visa dates from the detail page whenever cap permits so the monthly
                # window is based on the article date, not the listing-page text.
                if source == "Visa" and src_cap > 0:
                    needs_visa_confirm = (dt is None) or (not in_window(dt, window_start, window_end))
                    if needs_visa_confirm and global_detail_fetches < GLOBAL_DETAIL_FETCH_CAP and src_used < src_cap:
                        detail_html = polite_get(url)
                        if detail_html:
                            global_detail_fetches += 1
                            src_used += 1
                            per_source_detail_fetches[source] = src_used

                            dt2, snippet2 = extract_published_from_detail(url, detail_html, source=source)
                            if dt2:
                                dt = dt2
                            if snippet2:
                                snippet = snippet2

                # Mastercard can lose links/dates when the listing comes back thin or partially rendered.
                # If a captured Mastercard listing link is missing a usable date, confirm it on detail when allowed.
                if source == "Mastercard" and dt is None and src_cap > 0:
                    if global_detail_fetches < GLOBAL_DETAIL_FETCH_CAP and src_used < src_cap:
                        detail_html = polite_get(url)
                        if detail_html:
                            global_detail_fetches += 1
                            src_used += 1
                            per_source_detail_fetches[source] = src_used

                            dt2, snippet2 = extract_published_from_detail(url, detail_html, source=source)
                            if dt2:
                                dt = dt2
                            if snippet2:
                                snippet = snippet2

                # FATF pages carry unrelated dates in global navigation. Always confirm
                # the article's own date from its detail page when possible.
                if source == "FATF" and src_cap > 0:
                    if global_detail_fetches < GLOBAL_DETAIL_FETCH_CAP and src_used < src_cap:
                        detail_html = polite_get(url)
                        if detail_html:
                            global_detail_fetches += 1
                            src_used += 1
                            per_source_detail_fetches[source] = src_used
                            dt2, snippet2 = extract_published_from_detail(url, detail_html, source=source)
                            if dt2:
                                dt = dt2
                            if snippet2:
                                snippet = snippet2

                # If we still don't have a date, use detail page (bounded by caps)
                if dt is None and src_cap > 0:
                    if global_detail_fetches >= GLOBAL_DETAIL_FETCH_CAP:
                        continue
                    if src_used >= src_cap:
                        continue
        
                    detail_html = polite_get(url)
                    if not detail_html:
                        continue
        
                    global_detail_fetches += 1
                    src_used += 1
                    per_source_detail_fetches[source] = src_used
        
                    dt2, snippet2 = extract_published_from_detail(url, detail_html, source=source)
                    dt = dt2
                    snippet = snippet2
        
                if not dt:
                    continue
                if not in_window(dt, window_start, window_end):
                    continue
        
                all_items.append(
                    {
                        "category": CATEGORY_BY_SOURCE.get(source, source),
                        "source": source,
                        "title": title,
                        "published_at": iso_z(dt),
                        "url": url,
                        "summary": snippet,
                    }
                )
        
            print(
                f"[detail] {source}: used {src_used}/{src_cap} | global {global_detail_fetches}/{GLOBAL_DETAIL_FETCH_CAP}",
                flush=True,
            )
        
        gained = len(all_items) - source_items_before
        if gained == 0:
            print("[note] no qualifying items in month window (or blocked/changed).", flush=True)
        
    # ---- DEDUPE (with preference rules) ----
    items, dropped_dupes = dedupe_items_with_preference(all_items)
    if dropped_dupes:
        print(f"[dedupe] dropped {len(dropped_dupes)} landing/same-URL duplicate items", flush=True)
        for d in dropped_dupes[:25]:
            print(
                f"[dedupe] {d.get('reason')}: {d.get('source')} | {d.get('title')} | {d.get('url')}",
                flush=True,
            )
        if len(dropped_dupes) > 25:
            print(f"[dedupe] ... {len(dropped_dupes) - 25} more", flush=True)
    items.sort(key=lambda x: x["published_at"], reverse=True)
    smart_items = smart_top_items(items, SMART_100_LIMIT)
        
    payload = {
        "window_start": iso_z(window_start),
        "window_end": iso_z(window_end),
        "generated_at_utc": iso_z(now_utc),
        "generated_at_ct": now_ct.isoformat(),
        "items": items,
    }
        
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    ensure_dir(RAW_DIR)
    ensure_dir(PRINT_DIR)
        
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        
    with open(RAW_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(render_raw_html(payload))
        
    with open(RAW_MD_PATH, "w", encoding="utf-8") as f:
        f.write(render_raw_md(payload))
        
    with open(RAW_TXT_PATH, "w", encoding="utf-8") as f:
        f.write(render_raw_txt(payload))
        
    with open(RAW_NDJSON_PATH, "w", encoding="utf-8") as f:
        for it in payload.get("items", []):
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    with open(RAW_ARRAY_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload.get("items", []), f, ensure_ascii=False, indent=2)

    # Power Automate smart feed: KEEP the existing items-smart-100.json URL.
    # The filename is intentionally unchanged for existing flows, while the feed
    # now contains up to 150 ranked bank/fintech-relevant items. Each object
    # includes smart_index so flows can preserve the intended sequence.
    with open(RAW_SMART_100_ARRAY_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(smart_items, f, ensure_ascii=False, indent=2)
        
    with open(PRINT_HTML_PATH, "w", encoding="utf-8") as f:
        f.write(render_print_html(payload))
        
    write_raw_aux_files()
        
    print(
        f"\n[ok] wrote {OUT_PATH} with {len(items)} items | detail fetches: {global_detail_fetches}\n"
        f"[ok] wrote raw exports: {RAW_HTML_PATH}, {RAW_MD_PATH}, {RAW_TXT_PATH}, {RAW_NDJSON_PATH}, {RAW_ARRAY_JSON_PATH}, {RAW_SMART_100_ARRAY_JSON_PATH} (150 items, existing endpoint)\n"
        f"[ok] wrote print export: {PRINT_HTML_PATH}\n"
        f"[ok] wrote crawler hints: {RAW_ROBOTS_PATH}, {RAW_SITEMAP_PATH}",
        flush=True,
    )
if __name__ == "__main__":
    build()
