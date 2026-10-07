"""Paths, source settings and constants."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TRENDCATCHER_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "trendcatcher.db"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

USER_AGENT = "trendcatcher/0.1 (personal research project; github.com/anoopibrampur)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140 Safari/537.36"
)

# Minimum hours between runs per source. Wikipedia is date-driven instead (see source).
MIN_INTERVAL_HOURS = {
    "tiktok": 6,
    "google_trends": 1,
    "youtube": 3,
    "reddit": 1,
}

# ---- Locations
# Country level works on every source; US states only on Google Trends; no source exposes
# city/metro level (Google's DMA geos like US-NY-501 return nothing).
COUNTRIES = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "IN": "India",
}
US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def location_name(code: str | None) -> str:
    if not code:
        return "Global"
    if code.startswith("US-"):
        return f"{US_STATES.get(code[3:], code[3:])}, US"
    return COUNTRIES.get(code, code)


def parent_location(code: str) -> str | None:
    """US-NY -> US; a country has no parent."""
    return "US" if code.startswith("US-") else None


# Countries TikTok Creative Center serves hashtag trends for (from its frontend bundle).
# India is absent: TikTok is banned there.
TIKTOK_SUPPORTED = {"US", "FR", "DE", "IT", "ES", "GB", "AR", "AU", "BR", "CA", "CO", "EG", "ID", "IL", "JP",
                    "KR", "MY", "MX", "PH", "SA", "SG", "ZA", "TW", "TH", "TR", "AE", "VN"}
TIKTOK_COUNTRIES = [c for c in COUNTRIES if c in TIKTOK_SUPPORTED]
# Industry filter IDs and labels, taken from TikTok Creative Center's own frontend bundle.
# Anonymous requests return only the top 3 hashtags per query, so sweeping industries
# is how we get breadth (~50 hashtags per country).
TIKTOK_INDUSTRIES = {
    "10000000000": "Education",
    "11000000000": "Vehicle & Transportation",
    "12000000000": "Baby, Kids & Maternity",
    "14000000000": "Beauty & Personal Care",
    "15000000000": "Tech & Electronics",
    "17000000000": "Travel",
    "18000000000": "Household Products",
    "19000000000": "Pets",
    "21000000000": "Home Improvement",
    "22000000000": "Apparel & Accessories",
    "23000000000": "News & Entertainment",
    "25000000000": "Games",
    "27000000000": "Food & Beverage",
    "28000000000": "Sports & Outdoor",
    "29000000000": "Health",
}

GOOGLE_TRENDS_GEOS = list(COUNTRIES) + [f"US-{s}" for s in US_STATES]
YOUTUBE_REGIONS = list(COUNTRIES)
WIKI_COUNTRIES = list(COUNTRIES)
# Per-country Wikipedia lists mix projects; keep English plus the local-language wiki.
WIKI_PROJECTS = {"IN": ["en.wikipedia", "hi.wikipedia"]}
WIKI_DEFAULT_PROJECTS = ["en.wikipedia"]
WIKI_GLOBAL_REGION = "en"  # the worldwide English top list (no country)

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"
LLM_MODEL = os.environ.get("TRENDCATCHER_LLM", "qwen3.5:9b")
