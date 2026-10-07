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

TIKTOK_COUNTRIES = ["US", "GB", "CA", "AU"]
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

GOOGLE_TRENDS_GEOS = ["US", "GB", "CA", "AU"]
YOUTUBE_REGIONS = ["US"]

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"
LLM_MODEL = os.environ.get("TRENDCATCHER_LLM", "qwen3.5:9b")
