"""
CANSLIM Screener Pro — configuration and API key loading.

Key loading priority (highest to lowest):
  1. keys.env  in project root      <- preferred
  2. .env      in project root      <- standard dotenv
  3. Individual *_key.txt files     <- bare key files dropped in the folder
  4. OS environment variables       <- system-level
  5. Saved settings JSON            <- persisted from the UI settings panel
"""
import os
import json
from pathlib import Path

PROJECT_ROOT  = Path(__file__).parent.resolve()
SETTINGS_FILE = Path.home() / ".canslim_pro" / "settings.json"

APP_NAME    = "CANSLIM Screener Pro"
APP_VERSION = "1.1.0"

WEIGHTS = {"C": 0.15, "A": 0.20, "N": 0.15, "S": 0.15, "L": 0.20, "I": 0.10, "M": 0.05}

RATING_BANDS = [
    (90, "Exceptional+"),
    (80, "Exceptional"),
    (70, "Strong"),
    (60, "Above Average"),
    (50, "Average"),
    (0,  "Below Average"),
]

COMPONENT_LABELS = {
    "C": "Current Earnings",
    "A": "Annual Growth",
    "N": "Newness / New Highs",
    "S": "Supply / Demand",
    "L": "Leadership / RS Rank",
    "I": "Institutional Sponsorship",
    "M": "Market Direction",
}

US_BENCHMARK = "^GSPC"
IN_BENCHMARK = "^NSEI"
FMP_BASE     = "https://financialmodelingprep.com/api/v3"
ALPACA_DATA  = "https://data.alpaca.markets/v2"


def _parse_env_file(path: Path) -> dict:
    result = {}
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k and v:
                    result[k] = v
    except Exception:
        pass
    return result


def _read_txt(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    except Exception:
        pass
    return ""


def load_api_keys() -> dict:
    keys = {}

    for fname in ["keys.env", ".env", "api_keys.env", "api_keys.txt", "secrets.env"]:
        p = PROJECT_ROOT / fname
        if p.exists():
            keys.update(_parse_env_file(p))
            break

    if not keys.get("FMP_API_KEY"):
        for fname in ["fmp_key.txt", "fmp_api_key.txt", "FMP_API_KEY.txt", "fmp.txt"]:
            v = _read_txt(PROJECT_ROOT / fname)
            if v:
                keys["FMP_API_KEY"] = v
                break

    if not keys.get("ALPACA_KEY_ID"):
        for fname in ["alpaca_key.txt", "alpaca_api_key.txt", "alpaca.txt",
                      "ALPACA.txt", "ALPACA_KEY.txt", "alpaca_keys.txt"]:
            v = _read_txt(PROJECT_ROOT / fname)
            if v:
                if ":" in v:
                    kid, _, sec = v.partition(":")
                    keys["ALPACA_KEY_ID"]     = kid.strip()
                    keys["ALPACA_SECRET_KEY"] = sec.strip()
                else:
                    keys["ALPACA_KEY_ID"] = v
                break

    if not keys.get("ALPACA_SECRET_KEY"):
        for fname in ["alpaca_secret.txt", "alpaca_secret_key.txt", "ALPACA_SECRET.txt"]:
            v = _read_txt(PROJECT_ROOT / fname)
            if v:
                keys["ALPACA_SECRET_KEY"] = v
                break

    for env_name in ["FMP_API_KEY", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY"]:
        if not keys.get(env_name) and os.environ.get(env_name):
            keys[env_name] = os.environ[env_name]

    if not keys.get("ALPACA_KEY_ID"):
        for alt in ["ALPACA_API_KEY", "APCA_API_KEY_ID"]:
            if os.environ.get(alt):
                keys["ALPACA_KEY_ID"] = os.environ[alt]
                break

    if not keys.get("ALPACA_SECRET_KEY"):
        for alt in ["ALPACA_API_SECRET", "APCA_API_SECRET_KEY"]:
            if os.environ.get(alt):
                keys["ALPACA_SECRET_KEY"] = os.environ[alt]
                break

    saved = load_settings()
    for ui_k, env_k in [("fmp_api_key","FMP_API_KEY"),
                         ("alpaca_key_id","ALPACA_KEY_ID"),
                         ("alpaca_secret_key","ALPACA_SECRET_KEY")]:
        if not keys.get(env_k) and saved.get(ui_k):
            keys[env_k] = saved[ui_k]

    return keys


_KEYS_CACHE = None

def get_keys() -> dict:
    global _KEYS_CACHE
    if _KEYS_CACHE is None:
        _KEYS_CACHE = load_api_keys()
    return _KEYS_CACHE


def invalidate_key_cache():
    global _KEYS_CACHE
    _KEYS_CACHE = None


def load_settings() -> dict:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    return {
        "fmp_api_key": "", "alpaca_key_id": "", "alpaca_secret_key": "",
        "min_score": 60, "max_stocks": 50, "delay": 0.4,
    }


def save_settings(settings: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    invalidate_key_cache()


def get_rating(score: float) -> str:
    for threshold, label in RATING_BANDS:
        if score >= threshold:
            return label
    return "Below Average"


def key_status() -> dict:
    keys = get_keys()
    return {
        "fmp":        bool(keys.get("FMP_API_KEY")),
        "alpaca":     bool(keys.get("ALPACA_KEY_ID") and keys.get("ALPACA_SECRET_KEY")),
        "fmp_key":    (keys.get("FMP_API_KEY","")[:6]+"..." if keys.get("FMP_API_KEY") else "not set"),
        "alpaca_key": (keys.get("ALPACA_KEY_ID","")[:6]+"..." if keys.get("ALPACA_KEY_ID") else "not set"),
    }
