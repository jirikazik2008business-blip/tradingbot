# notifications.py
import time
from logger import log_info, log_error, log_debug
from config import DISCORD_MIN_INTERVAL_S

_last_sent = 0.0
_sender = None

def set_sender(func):
    global _sender
    _sender = func

def notify_raw(content: str):
    _post_discord(content)

def _post_discord(content):
    global _last_sent
    now = time.time()
    if now - _last_sent < DISCORD_MIN_INTERVAL_S:
        log_debug("Discord send blocked by rate limiter.")
        return
    if _sender is None:
        log_debug("Discord sender not initialized yet.")
        _last_sent = now
        return
    try:
        _sender(content)
    except Exception as e:
        log_error(f"Discord send failed: {e}")
    _last_sent = now

def notify_signal(plan):
    msg = f"SIGNAL | {plan.symbol} | {plan.direction} | entry={plan.entry_price:.5f} | SL={plan.sl:.5f} | TP={plan.tp:.5f} | lots={plan.lots}"
    log_info(msg)
    _post_discord(msg)

def notify_order_result(result, plan=None):
    if result is None:
        _post_discord(f"ORDER FAILED | {getattr(plan,'symbol','?')}")
    else:
        _post_discord(f"ORDER OK | {getattr(plan,'symbol','?')} | order={getattr(result,'order',None)} | ret={getattr(result,'retcode',None)}")

def notify_order_rejected(result, plan=None):
    _post_discord(f"ORDER REJECTED | {getattr(plan,'symbol','?')} | result={result}")

def notify_position_closed(ticket, pnl, summary=""):
    """
    Notify closed position. summary can include tag 'TP' or 'SL' + chosen comment.
    """
    try:
        msg = f"POSITION CLOSED | ticket={ticket} | pnl={pnl:.2f}"
        if summary:
            msg = f"{msg} | {summary}"
        _post_discord(msg)
    except Exception as e:
        log_error(f"notify_position_closed failed: {e}")

def notify_risk_gate(reason):
    _post_discord(f"RISK GATE | {reason}")

def notify_watchdog(balance, equity):
    _post_discord(f"WATCHDOG | balance={balance:.2f} | equity={equity:.2f}")
