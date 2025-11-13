# position_watcher.py
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5
from logger import log_info, log_error, log_debug
from metrics import update_closed_trade
from comments import load_takeprofit_comment, load_stoploss_comment
import notifications

def scan_history_and_update(last_check_utc: datetime):
    """
    Scans MT5 history deals from last_check_utc to now and aggregates closed deals.
    Returns new timestamp to be used next call.
    """
    try:
        frm = last_check_utc
        to = datetime.now(timezone.utc)
        inited_here = False
        try:
            if not mt5.initialize():
                if not mt5.initialize():
                    log_debug("MT5 initialization failed in position_watcher.scan_history_and_update.")
                    return to
                inited_here = True
            deals = mt5.history_deals_get(frm, to) or []
        except Exception as e:
            log_error(f"MT5 history fetch failed: {e}")
            return to

        closed_trades = defaultdict(lambda: {"pnl": 0.0, "symbol": "?", "deals": 0})

        for d in deals:
            try:
                if getattr(d, "entry", None) != mt5.DEAL_ENTRY_OUT:
                    continue
                ticket = getattr(d, "position_id", None) or getattr(d, "order", None)
                if ticket is None:
                    continue
                profit = float(getattr(d, "profit", 0.0) or 0.0)
                commission = float(getattr(d, "commission", 0.0) or 0.0)
                swap = float(getattr(d, "swap", 0.0) or 0.0)
                aggregated = profit + commission + swap
                sym = getattr(d, "symbol", "?")
                info = closed_trades[ticket]
                info["pnl"] += aggregated
                info["symbol"] = sym
                info["deals"] += 1
            except Exception as e:
                log_debug(f"Error processing deal record: {e}")
                continue

        processed = 0
        for ticket, info in closed_trades.items():
            pnl = info["pnl"]
            sym = info["symbol"]
            deals_count = info["deals"]
            log_info(f"CLOSE | {sym} | ticket={ticket} | pnl={pnl:.2f} | deals={deals_count}")
            try:
                # append 'closed' row to journal
                update_closed_trade(ticket, pnl)
            except Exception as e:
                log_error(f"Failed to update journal for ticket {ticket}: {e}")

            # send richer notify with TP/SL comment
            try:
                if pnl > 0:
                    comment = load_takeprofit_comment()
                    notifications.notify_position_closed(ticket, pnl, summary=f"TP | {comment}" if comment else "TP")
                elif pnl < 0:
                    comment = load_stoploss_comment()
                    notifications.notify_position_closed(ticket, pnl, summary=f"SL | {comment}" if comment else "SL")
                else:
                    notifications.notify_position_closed(ticket, pnl, summary="Flat/No PnL")
            except Exception as e:
                log_error(f"Failed to send position closed notification for {ticket}: {e}")

            processed += 1

        log_debug(f"scan_history_and_update processed {processed} tickets (interval {frm.isoformat()} -> {to.isoformat()})")
        try:
            if inited_here:
                mt5.shutdown()
        except Exception:
            pass
        return to
    except Exception as e:
        log_error(f"scan_history_and_update error: {e}")
        return datetime.now(timezone.utc) - timedelta(minutes=1)
