import os
from dotenv import load_dotenv
load_dotenv()

def as_bool(val: str) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes", "y")

# MT5 / symbols / timeframes
MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "EURUSD,GBPUSD,USDJPY").split(",") if s.strip()]
ENTRY_TFS = [t.strip() for t in os.getenv("ENTRY_TFS", "M5").split(",")]
ALIGN_TF_HIGH = os.getenv("ALIGN_TF_HIGH", "H4")
ALIGN_TF_MID = os.getenv("ALIGN_TF_MID", "H1")
POSITION_MODE = os.getenv("POSITION_MODE", "PERCENT").upper()

# Risk / sizing
RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))
MAX_RISK_PER_TRADE_PCT = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.005"))
LOTS_FIXED = float(os.getenv("LOTS_FIXED", "0.01"))
FAT_FINGER_MAX_LOTS = float(os.getenv("FAT_FINGER_MAX_LOTS", "5.0"))

MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "0.08"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02"))
MAX_WEEKLY_DRAWDOWN = float(os.getenv("MAX_WEEKLY_DRAWDOWN", "0.04"))
MAX_MONTHLY_DRAWDOWN = float(os.getenv("MAX_MONTHLY_DRAWDOWN", "0.08"))

# News / calendar tuning (minutes)
NEWS_LOOKAHEAD_MIN = int(os.getenv("NEWS_LOOKAHEAD_MIN", "120"))
NEWS_CLOSE_WITHIN_MIN = int(os.getenv("NEWS_CLOSE_WITHIN_MIN", "10"))
NEWS_REDUCE_WITHIN_MIN = int(os.getenv("NEWS_REDUCE_WITHIN_MIN", "60"))

ECON_CAL_TIMEOUT = int(os.getenv("ECON_CAL_TIMEOUT", "15"))
ECON_CAL_CACHE_TTL_S = int(os.getenv("ECON_CAL_CACHE_TTL_S", "300"))
USE_SELENIUM_FOR_FF = as_bool(os.getenv("USE_SELENIUM_FOR_FF", "false"))

MAX_SPREAD_PIPS = float(os.getenv("MAX_SPREAD_PIPS", "2.0"))
MAX_POSITIONS_PER_SYMBOL = int(os.getenv("MAX_POSITIONS_PER_SYMBOL", "2"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
MAX_TRADES_PER_WEEK = int(os.getenv("MAX_TRADES_PER_WEEK", "20"))

NEWYORK_START = os.getenv("NEWYORK_START", "13:00")
NEWYORK_END = os.getenv("NEWYORK_END", "17:00")

CHECKLIST_MIN_SCORE = float(os.getenv("CHECKLIST_MIN_SCORE", "20"))

# Trailing / BE / Partial TP config (new)
USE_TRAILING = as_bool(os.getenv("USE_TRAILING", "true"))            # global switch for trailing & BE
TRAILING_R_MULT = float(os.getenv("TRAILING_R_MULT", "0.5"))        # when to move SL by trailing (mult of R)
BREAKEVEN_RR = float(os.getenv("BREAKEVEN_RR", "1.0"))              # minimal RR to set BE (one-shot)

PARTIAL_TP_ENABLED = as_bool(os.getenv("PARTIAL_TP_ENABLED", "false"))
PARTIAL_TP_PERCENT = float(os.getenv("PARTIAL_TP_PERCENT", "50"))   # percent to close (e.g. 50)
PARTIAL_TP_RR = float(os.getenv("PARTIAL_TP_RR", "1.0"))             # RR threshold to trigger partial TP
PARTIAL_TP_MIN_LOT = float(os.getenv("PARTIAL_TP_MIN_LOT", "0.01")) # minimum lot to consider for partial close

# State / polling
REQUIRE_CONTINUATION = as_bool(os.getenv("REQUIRE_CONTINUATION", "true"))
WATCHDOG_INTERVAL_HOURS = int(os.getenv("WATCHDOG_INTERVAL_HOURS", "6"))
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
DISCORD_MIN_INTERVAL_S = int(os.getenv("DISCORD_MIN_INTERVAL_S", "10"))

# Logging / paths
LOG_DIR = os.getenv("LOG_DIR", "logs")
JOURNAL_CSV = os.getenv("JOURNAL_CSV", "journal.csv")
CLEAN_LOGS_ENABLED = as_bool(os.getenv("CLEAN_LOGS_ENABLED", "true"))
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "30"))
STATUS_LOG = os.getenv("STATUS_LOG", "status.log")
COMMENTS_DIR = os.getenv("COMMENTS_DIR", "comments")

BUY_COMMENTS_FILE = os.getenv("BUY_COMMENTS_FILE", os.path.join(COMMENTS_DIR, "buy.txt"))
SELL_COMMENTS_FILE = os.getenv("SELL_COMMENTS_FILE", os.path.join(COMMENTS_DIR, "sell.txt"))
TAKEPROFIT_COMMENTS_FILE = os.getenv("TAKEPROFIT_COMMENTS_FILE", os.path.join(COMMENTS_DIR, "takeprofit.txt"))
STOPLOSS_COMMENTS_FILE = os.getenv("STOPLOSS_COMMENTS_FILE", os.path.join(COMMENTS_DIR, "stoploss.txt"))

# Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

# Other financials / visuals
START_BALANCE = float(os.getenv("START_BALANCE", "10000"))
USD_CZK = float(os.getenv("USD_CZK", "22.0"))

BACKGROUND_DIR = os.getenv("BACKGROUND_DIR", "assets/backgrounds")
OUTPUT_IMAGE = os.getenv("OUTPUT_IMAGE", os.path.join(LOG_DIR, "stats_image.png"))
FONT_PATH = os.getenv("FONT_PATH", "assets/Montserrat-Bold.ttf")
LAST_BG_INDEX_FILE = os.getenv("LAST_BG_INDEX_FILE", os.path.join(LOG_DIR, "last_bg_index.txt"))

# Trading toggles
TRADE_ENABLED = as_bool(os.getenv("TRADE_ENABLED", "true"))
NOTIFY_ONLY = as_bool(os.getenv("NOTIFY_ONLY", "false"))
SELF_RESTART = as_bool(os.getenv("SELF_RESTART", "false"))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "30"))
STARTUP_PROTECTION_CYCLES = int(os.getenv("STARTUP_PROTECTION_CYCLES", "3"))
CONSECUTIVE_LOSS_LIMIT = int(os.getenv("CONSECUTIVE_LOSS_LIMIT", "2"))

LOG_TZ = os.getenv("LOG_TZ", "Europe/Prague")
