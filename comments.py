# comments.py
import random
from config import BUY_COMMENTS_FILE, SELL_COMMENTS_FILE, TAKEPROFIT_COMMENTS_FILE, STOPLOSS_COMMENTS_FILE
from logger import log_debug
import os

def _load_random_line(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        choice = random.choice(lines) if lines else ""
        log_debug(f"Selected comment from {path}: {choice}")
        return choice
    except FileNotFoundError:
        log_debug(f"No comments file at {path}")
        return ""
    except Exception as e:
        log_debug(f"Error loading comments from {path}: {e}")
        return ""

def load_comment(direction: str) -> str:
    """
    Backwards-compatible: long/buy -> BUY_COMMENTS_FILE else SELL_COMMENTS_FILE
    """
    path = BUY_COMMENTS_FILE if direction and direction.lower() in ("long","buy","bull") else SELL_COMMENTS_FILE
    return _load_random_line(path)

def load_takeprofit_comment() -> str:
    return _load_random_line(TAKEPROFIT_COMMENTS_FILE)

def load_stoploss_comment() -> str:
    return _load_random_line(STOPLOSS_COMMENTS_FILE)

def load_motivation() -> str:
    # support comments/motivation.txt or comments/motivation.comments.txt
    possible = []
    try:
        base = os.getenv("COMMENTS_DIR", "comments")
        p1 = os.path.join(base, "motivation.txt")
        p2 = os.path.join(base, "motivation.comments.txt")
        possible = [p for p in (p1, p2) if os.path.exists(p)]
        if not possible:
            # try env override
            mfile = os.getenv("MOTIVATION_FILE")
            if mfile and os.path.exists(mfile):
                possible = [mfile]
        if possible:
            return _load_random_line(possible[0])
    except Exception as e:
        log_debug(f"load_motivation error: {e}")
    return ""
