# executor.py
import time
from logger import log_info, log_error, log_debug
from trade import place_trade, TradePlan
from metrics import init_trade_row, append_journal
from notifications import notify_order_result, notify_order_rejected, notify_signal
from comments import load_comment
import MetaTrader5 as mt5

from config import (
    COOLDOWN_SECONDS,
    STARTUP_PROTECTION_CYCLES,
    MAX_POSITIONS_PER_SYMBOL
)

_bars_seen = {}
_started = {}
_last_signal = {}
_last_open_time = {}
_journal_logged_signature = {}

def tick_symbol(symbol: str):
    c = _bars_seen.get(symbol, 0) + 1
    _bars_seen[symbol] = c
    if c > STARTUP_PROTECTION_CYCLES:
        _started[symbol] = True
    log_debug(f"tick_symbol {symbol} bars_seen={c} started={_started.get(symbol,False)}")

def clear_signal(symbol: str):
    if _last_signal.get(symbol, False):
        log_debug(f"clear_signal: resetting last_signal for {symbol}")
    _last_signal[symbol] = False

def _has_open_trade(symbol: str) -> bool:
    try:
        pos = mt5.positions_get(symbol=symbol) or []
        return len(pos) >= int(MAX_POSITIONS_PER_SYMBOL)
    except Exception:
        log_debug(f"_has_open_trade: mt5.positions_get failed for {symbol}")
        return False

def _can_open_now(symbol: str) -> bool:
    last = _last_open_time.get(symbol, 0)
    return (time.time() - last) >= int(COOLDOWN_SECONDS)

def _plan_signature(plan) -> str:
    try:
        return f"{plan.symbol}|{plan.direction}|{round(plan.entry_price, 5)}"
    except Exception:
        return str(plan)

def execute_plan(plan):
    """
    Execute a TradePlan:
      - check startup protection, cooldown, existing positions
      - call place_trade(plan)
      - write journal row (opened or skipped)
      - notify results through notifications module
    Returns order ticket (int) or None.
    """
    try:
        symbol = getattr(plan, "symbol", None)
        if symbol is None:
            log_error("execute_plan received plan without symbol")
            return None

        try:
            comment = load_comment(plan.direction)
            if comment:
                plan.comment = comment
        except Exception as e:
            log_debug(f"Could not load comment: {e}")

        started = _started.get(symbol, False)
        prev_sig = _last_signal.get(symbol, False)
        sig_present = True

        will_attempt_open = False
        reason_no_open = None

        if not started:
            reason_no_open = "startup_protection"
            log_debug(f"{symbol} startup protection active. bars_seen={_bars_seen.get(symbol,0)}")
        elif not sig_present:
            reason_no_open = "no_signal"
        else:
            if prev_sig:
                reason_no_open = "no_rising_edge"
            elif _has_open_trade(symbol):
                reason_no_open = "existing_position"
            elif not _can_open_now(symbol):
                reason_no_open = "cooldown"
            else:
                will_attempt_open = True

        ticket = None
        if will_attempt_open:
            try:
                ticket = place_trade(plan)
                if ticket:
                    _last_open_time[symbol] = time.time()
                    log_info(f"Opened order ticket={ticket} for {symbol}")
                else:
                    log_info(f"Order not placed for {symbol} (place_trade returned None).")
            except Exception as e:
                log_error(f"place_trade failed for {symbol}: {e}")
                ticket = None
        else:
            log_info(f"PLAN_SKIP | {symbol} | reason={reason_no_open}")

        try:
            sig = _plan_signature(plan)
            should_write = False
            if ticket:
                should_write = True
            else:
                last_sig = _journal_logged_signature.get(symbol)
                if last_sig != sig:
                    should_write = True

            if should_write:
                row = init_trade_row(plan, ticket or 0)
                append_journal(row)
                _journal_logged_signature[symbol] = sig
            else:
                log_debug(f"Skipping journal write for repeated signature {sig} on {symbol}")
        except Exception as e:
            log_debug(f"Failed to append journal row: {e}")

        if ticket:
            try:
                notify_order_result({'order': ticket, 'retcode': 0}, plan=plan)
            except Exception:
                pass
            _last_signal[symbol] = True
        else:
            try:
                notify_order_rejected('no-order' if not will_attempt_open else 'rejected', plan=plan)
            except Exception:
                pass
            _last_signal[symbol] = True

        return ticket
    except Exception as e:
        log_error(f"execute_plan error: {e}")
        return None
