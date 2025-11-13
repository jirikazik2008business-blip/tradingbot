import os
import logging
import time
import re
from datetime import datetime, timezone
from config import LOG_DIR, LOG_RETENTION_DAYS, CLEAN_LOGS_ENABLED

os.makedirs(LOG_DIR, exist_ok=True)

# Allow LOG_LEVEL override from environment (e.g. DEBUG, INFO, WARNING, ERROR)
# Default is INFO if not set or invalid.
def _get_log_level():
    lv = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    return {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARN": logging.WARNING,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
        "NOTSET": logging.NOTSET,
    }.get(lv, logging.INFO)

LOG_LEVEL = _get_log_level()

class DailyFileHandler(logging.FileHandler):
    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        self.current_date = None
        self._update_filename()
        super().__init__(self.filename, mode="a", encoding="utf-8")

    def _update_filename(self):
        # use timezone-aware date for filename
        self.current_date = datetime.now(timezone.utc).astimezone().strftime("%d-%m-%Y")
        self.filename = os.path.join(self.log_dir, f"{self.current_date}.log")

    def emit(self, record):
        today = datetime.now(timezone.utc).astimezone().strftime("%d-%m-%Y")
        if today != self.current_date:
            try:
                self.close()
            except Exception:
                pass
            self._update_filename()
            self.baseFilename = os.path.abspath(self.filename)
            self.stream = self._open()
        super().emit(record)

# main logger setup
logger = logging.getLogger("trading_bot")
logger.setLevel(LOG_LEVEL)
handler = DailyFileHandler(LOG_DIR)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%d-%m-%Y %H:%M")
handler.setFormatter(formatter)
logger.handlers = []  # reset handlers to avoid duplicates on reload
logger.addHandler(handler)

# --- suppression & throttling configuration ---
_DEFAULT_SUPPRESS_SECONDS = int(os.getenv("LOG_SUPPRESS_SECONDS", "3600"))

_PATTERN_TTL = {
    "PLAN": int(os.getenv("LOG_TTL_PLAN", "300")),
    "SIGNAL": int(os.getenv("LOG_TTL_SIGNAL", "300")),
    "OPEN": int(os.getenv("LOG_TTL_OPEN", "300")),
    "CLOSE": int(os.getenv("LOG_TTL_CLOSE", "600")),
    "SLEEP": int(os.getenv("LOG_TTL_SLEEP", "300")),
    "RISK_GATE": int(os.getenv("LOG_TTL_RISK", "3600")),
    "WATCHDOG": int(os.getenv("LOG_TTL_WATCHDOG", "3600")),
    "ORDER_REJECTED": int(os.getenv("LOG_TTL_ORDER_REJ", "1800")),
}

_last_logged = {}
_ORDER_REJ_REGEX = re.compile(r"ORDER REJECTED\s*\|\s*(?P<symbol>[A-Z0-9_\-]+)", re.IGNORECASE)
_GENERIC_HAS_SYMBOL = re.compile(r"^(?P<prefix>[A-Z_ ]+)\s*\|\s*(?P<symbol>[A-Z0-9_\-]+)")

def _current_ts() -> float:
    return time.time()

def _should_log_unique(key: str, ttl: int = None) -> bool:
    now = _current_ts()
    ttl_use = (ttl if ttl is not None else _DEFAULT_SUPPRESS_SECONDS)
    last = _last_logged.get(key)
    if last is None or now - last >= ttl_use:
        _last_logged[key] = now
        return True
    return False

def _derive_pattern_and_key(msg: str):
    if not msg:
        return None, None
    m = _ORDER_REJ_REGEX.search(msg)
    if m:
        symbol = m.group("symbol")
        return "ORDER_REJECTED", f"ORDER_REJECTED:{symbol}"
    m2 = _GENERIC_HAS_SYMBOL.search(msg)
    if m2:
        prefix = m2.group("prefix").strip().upper()
        symbol = m2.group("symbol").strip().upper()
        if prefix.startswith("PLAN"):
            return "PLAN", f"PLAN:{symbol}"
        if prefix.startswith("SIGNAL"):
            return "SIGNAL", f"SIGNAL:{symbol}"
        if prefix.startswith("OPEN") or prefix.startswith("ORDER OK") or prefix.startswith("ORDER EXCEPTION"):
            return "OPEN", f"OPEN:{symbol}"
        if prefix.startswith("CLOSE"):
            return "CLOSE", f"CLOSE:{symbol}"
        return prefix.replace(" ", "_"), f"{prefix}:{symbol}"
    low = msg.upper()
    if "SLEEP |" in low or low.startswith("SLEEP |"):
        return "SLEEP", "SLEEP"
    if low.startswith("RISK GATE") or "RISK GATE" in low:
        return "RISK_GATE", "RISK_GATE"
    if low.startswith("WATCHDOG") or "WATCHDOG" in low:
        return "WATCHDOG", "WATCHDOG"
    return None, None

_last_sleep_log_time = 0
_SLEEP_LOG_INTERVAL = 60

def log_sleep(msg: str, sleep_seconds: int):
    global _last_sleep_log_time
    now = _current_ts()
    if now - _last_sleep_log_time >= _SLEEP_LOG_INTERVAL:
        value = sleep_seconds // 60 if sleep_seconds >= 60 else sleep_seconds
        unit = "min" if sleep_seconds >= 60 else "s"
        logger.info(f"SLEEP | {msg} | sleeping for {value}{unit}")
        _last_sleep_log_time = now

def reset_sleep_flag():
    global _last_sleep_log_time
    _last_sleep_log_time = 0

def _maybe_suppress_and_log(level: str, msg: str):
    pattern, key = _derive_pattern_and_key(msg)
    if pattern:
        ttl = _PATTERN_TTL.get(pattern, _DEFAULT_SUPPRESS_SECONDS)
        scoped_key = f"{pattern}:{key or msg}"
        if not _should_log_unique(scoped_key, ttl=ttl):
            return
    else:
        if not _should_log_unique(msg, ttl=_DEFAULT_SUPPRESS_SECONDS):
            return
    getattr(logger, level)(msg)

def log_debug(msg: str):
    try:
        logger.debug(msg)
    except Exception:
        pass

def log_info(msg: str, unique: bool = False, key: str = None):
    if unique:
        check_key = key or msg
        if _should_log_unique(check_key, ttl=_DEFAULT_SUPPRESS_SECONDS):
            logger.info(msg)
    else:
        _maybe_suppress_and_log("info", msg)

def log_warning(msg: str, unique: bool = False, key: str = None):
    if unique:
        check_key = key or msg
        if _should_log_unique(check_key, ttl=_DEFAULT_SUPPRESS_SECONDS):
            logger.warning(msg)
    else:
        _maybe_suppress_and_log("warning", msg)

def log_error(msg: str, unique: bool = False, key: str = None):
    if unique:
        check_key = key or msg
        if _should_log_unique(check_key, ttl=_DEFAULT_SUPPRESS_SECONDS):
            logger.error(msg)
    else:
        _maybe_suppress_and_log("error", msg)

def cleanup_old_logs():
    if not CLEAN_LOGS_ENABLED:
        return
    cutoff = time.time() - LOG_RETENTION_DAYS * 24 * 3600
    try:
        for fn in os.listdir(LOG_DIR):
            if fn.lower().endswith(".log"):
                p = os.path.join(LOG_DIR, fn)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        logger.info(f"LOG CLEANUP | removed {fn}")
                except Exception as e:
                    logger.error(f"LOG CLEANUP ERROR | {fn} | {e}")
    except Exception as e:
        logger.error(f"LOG CLEANUP FAILED | {e}")
