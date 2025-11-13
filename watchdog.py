import time
from datetime import datetime, timezone
from logger import log_info, log_debug
from config import STATUS_LOG, WATCHDOG_INTERVAL_HOURS
import MetaTrader5 as mt5

def write_status():
    try:
        acc = mt5.account_info()
        bal = float(acc.balance) if acc else 0.0
        eq = float(acc.equity) if acc else 0.0
    except Exception:
        bal, eq = 0.0, 0.0
    msg = f"{datetime.now(timezone.utc).isoformat()} | balance={bal:.2f} | equity={eq:.2f}\n"
    try:
        with open(STATUS_LOG, "a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        log_debug("Failed to write status log")

def start_watchdog():
    while True:
        try:
            write_status()
        except Exception as e:
            log_debug(f"watchdog error: {e}")
        time.sleep(WATCHDOG_INTERVAL_HOURS * 3600)
