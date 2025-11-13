import math
import traceback
from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

from logger import log_debug, log_info, log_error
from config import (
    TRADE_ENABLED,
    NOTIFY_ONLY,
    FAT_FINGER_MAX_LOTS,
    USE_TRAILING,
    TRAILING_R_MULT,
    BREAKEVEN_RR,
    PARTIAL_TP_ENABLED,
    PARTIAL_TP_PERCENT,
    PARTIAL_TP_RR,
    PARTIAL_TP_MIN_LOT
)
import notifications

# runtime one-shot state (lost on restart)
_breakeven_set_tickets = set()
_partial_closed_tickets = set()

@dataclass
class TradePlan:
    symbol: str
    direction: str       # "long" / "short" / "buy" / "sell"
    entry_price: float
    sl: float
    tp: float
    lots: float
    tf_entry: str
    score: float
    comment: str

def _notify(msg: str):
    """Send short text notification via notifications.notify_raw and logger."""
    log_info(msg)
    try:
        notifications.notify_raw(msg)
    except Exception:
        log_debug("notify_raw failed")

def _round_volume_to_step(vol: float, vol_min: float, vol_max: float, vol_step: float) -> float:
    """Round/clamp volume to symbol's allowed step/min/max (rounded down to step)."""
    try:
        if vol_step <= 0:
            return max(min(vol, vol_max), vol_min)
        # clamp
        vol = max(vol_min, min(vol, vol_max))
        # compute number of steps from vol_min and floor to avoid exceeding requested
        steps = math.floor((vol - vol_min) / vol_step)
        vol_adj = vol_min + steps * vol_step
        if vol_adj < vol_min:
            vol_adj = vol_min
        # precision
        prec = 0
        if vol_step < 1:
            prec = max(0, -int(math.floor(math.log10(vol_step))))
        return round(vol_adj, prec + 2)
    except Exception as e:
        log_debug(f"_round_volume_to_step error: {e}")
        return vol

def _extract_ticket_from_result(res) -> int:
    """Return deal or order id from order_send result."""
    try:
        deal = getattr(res, "deal", None) or 0
        order = getattr(res, "order", None) or 0
        return int(deal or order or 0)
    except Exception:
        return 0

def build_order_request(plan: TradePlan, volume_override: Optional[float] = None, type_filling: Optional[int] = None) -> dict:
    """Build MT5 order_send request dict from plan."""
    tick = mt5.symbol_info_tick(plan.symbol)
    if tick is None:
        raise RuntimeError("No tick for symbol")
    price = float(tick.ask) if plan.direction.lower() in ("long", "buy", "bull") else float(tick.bid)
    order_type = mt5.ORDER_TYPE_BUY if plan.direction.lower() in ("long", "buy", "bull") else mt5.ORDER_TYPE_SELL
    vol = float(volume_override) if volume_override is not None else float(plan.lots)
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": plan.symbol,
        "volume": vol,
        "type": order_type,
        "price": price,
        "sl": float(plan.sl) if plan.sl else 0.0,
        "tp": float(plan.tp) if plan.tp else 0.0,
        "deviation": int(getattr(plan, "deviation", 20)),
        "magic": int(getattr(plan, "magic", 424242)),
        "comment": (plan.comment or "")[:255],
        "type_time": getattr(mt5, "ORDER_TIME_GTC", 0)
    }
    if type_filling is not None:
        req["type_filling"] = type_filling
    log_debug(f"Order request built: {req}")
    return req

def place_trade(plan: TradePlan) -> Optional[int]:
    """
    Place market order for given plan.
    Returns ticket (int) on success, or None on failure.
    """
    open_msg = (f"OPEN | {plan.symbol} | {plan.direction} | entry≈{plan.entry_price:.5f} | "
                f"SL={plan.sl:.5f} | TP={plan.tp:.5f} | lots={plan.lots} | note='{plan.comment}'")
    _notify(open_msg)

    if NOTIFY_ONLY or not TRADE_ENABLED:
        log_info("Signal-only mode or trading disabled. Not executing order.")
        return None
    if plan.lots > FAT_FINGER_MAX_LOTS:
        log_error(f"Fat finger prevented order: lots={plan.lots}")
        return None

    try:
        # initialize MT5 (safe)
        try:
            inited = mt5.initialize()
            log_debug(f"mt5.initialize() -> {inited}")
        except Exception as e:
            log_debug(f"mt5.initialize exception (continuing): {e}")

        # try selecting symbol
        try:
            mt5.symbol_select(plan.symbol, True)
        except Exception:
            pass

        si = mt5.symbol_info(plan.symbol)
        tick = mt5.symbol_info_tick(plan.symbol)
        log_debug(f"SymbolInfo {plan.symbol}: {si}")
        log_debug(f"Tick {plan.symbol}: {tick}")

        if si is None:
            err = f"Symbol {plan.symbol} not found (symbol_info returned None)."
            _notify(err)
            log_error(err)
            return None

        # check trading allowed
        try:
            if not bool(getattr(si, "trade_allowed", True)):
                err = f"Trading not allowed for {plan.symbol} according to symbol_info."
                _notify(err)
                log_error(err)
                return None
        except Exception:
            pass

        # volume constraints
        try:
            vol_min = float(getattr(si, "volume_min", 0.01) or 0.01)
            vol_max = float(getattr(si, "volume_max", max(plan.lots, 100.0)) or max(plan.lots, 100.0))
            vol_step = float(getattr(si, "volume_step", 0.01) or 0.01)
        except Exception:
            vol_min, vol_max, vol_step = 0.01, 100.0, 0.01

        vol_ok = _round_volume_to_step(plan.lots, vol_min, vol_max, vol_step)
        if vol_ok < vol_min or vol_ok > vol_max:
            err = f"Adjusted volume {vol_ok} outside allowed range [{vol_min},{vol_max}] for {plan.symbol}."
            _notify(err)
            log_error(err)
            return None

        # build base request (no explicit filling)
        try:
            req = build_order_request(plan, volume_override=vol_ok, type_filling=None)
        except Exception as e:
            err = f"Failed to build order request: {e}"
            _notify(err)
            log_error(err)
            return None

        log_debug(f"Attempting order_send for {plan.symbol} (no explicit filling)...")
        try:
            res = mt5.order_send(req)
            log_debug(f"order_send result (default filling): {res}")
        except Exception as e:
            log_error(f"order_send threw exception: {e}\n{traceback.format_exc()}")
            res = None

        def _is_success(r):
            if r is None:
                return False
            rc = getattr(r, "retcode", None)
            try:
                if rc == 0:
                    return True
                if hasattr(mt5, "TRADE_RETCODE_DONE") and rc == getattr(mt5, "TRADE_RETCODE_DONE"):
                    return True
                if rc == 10009:  # vendor quirk seen in some brokers
                    return True
            except Exception:
                pass
            return False

        if _is_success(res):
            ticket = _extract_ticket_from_result(res)
            ok = f"ORDER OK | {plan.symbol} | order={ticket} | retcode={getattr(res,'retcode',None)}"
            _notify(ok)
            try:
                if hasattr(notifications, "notify_order_result"):
                    notifications.notify_order_result(res, plan=plan)
            except Exception:
                pass
            return ticket or 0

        # If non-success, check unsupported filling mode or other reject
        unsupported = False
        last_res = res
        if res is not None:
            rc = getattr(res, "retcode", None)
            comment = str(getattr(res, "comment", "") or "")
            if rc == 10030 or "unsupported filling" in comment.lower() or "filling" in comment.lower():
                unsupported = True
                log_info(f"ORDER REJECTED (unsupported filling) | {plan.symbol} | result={res}")
            else:
                # other rejection - notify and stop
                log_info(f"ORDER REJECTED | {plan.symbol} | result={res}")
                try:
                    if hasattr(notifications, "notify_order_rejected"):
                        notifications.notify_order_rejected({"order": None, "retcode": rc, "comment": comment}, plan=plan)
                except Exception:
                    pass
                return None
        else:
            log_info(f"ORDER send returned None for {plan.symbol}, will attempt fallback fillings.")
            unsupported = True

        # Fallback fillings
        filling_candidates = []
        for name in ("ORDER_FILLING_IOC", "ORDER_FILLING_FOK", "ORDER_FILLING_RETURN"):
            if hasattr(mt5, name):
                filling_candidates.append(getattr(mt5, name))
        filling_candidates = list(dict.fromkeys(filling_candidates))

        for fm in filling_candidates:
            try:
                req_f = build_order_request(plan, volume_override=vol_ok, type_filling=fm)
                log_info(f"Retrying order_send for {plan.symbol} with type_filling={fm}")
                r2 = None
                try:
                    r2 = mt5.order_send(req_f)
                    log_debug(f"order_send result (filling={fm}): {r2}")
                except Exception as e:
                    log_error(f"order_send exception for filling {fm}: {e}\n{traceback.format_exc()}")
                last_res = r2
                if _is_success(r2):
                    ticket = _extract_ticket_from_result(r2)
                    ok = f"ORDER OK (fallback) | {plan.symbol} | order={ticket} | filling={fm}"
                    _notify(ok)
                    try:
                        if hasattr(notifications, "notify_order_result"):
                            notifications.notify_order_result(r2, plan=plan)
                    except Exception:
                        pass
                    return ticket or 0
                if r2 is not None:
                    rc2 = getattr(r2, "retcode", None)
                    c2 = str(getattr(r2, "comment", "") or "")
                    if rc2 == 10030 or "unsupported filling" in c2.lower() or "filling" in c2.lower():
                        log_info(f"Filling {fm} unsupported for {plan.symbol}, trying next.")
                        continue
                    else:
                        log_info(f"Order rejected with filling {fm} for {plan.symbol}: {r2}")
                        try:
                            if hasattr(notifications, "notify_order_rejected"):
                                notifications.notify_order_rejected({"order": None, "retcode": rc2, "comment": c2}, plan=plan)
                        except Exception:
                            pass
                        return None
            except Exception as e:
                log_debug(f"Exception during fallback for filling {fm}: {e}")

        log_error(f"No order placed for {plan.symbol} after trying filling modes. Last result: {last_res}")
        try:
            if hasattr(notifications, "notify_order_rejected"):
                notifications.notify_order_rejected({"order": None, "retcode": getattr(last_res, 'retcode', None), "comment": str(getattr(last_res,'comment',None))}, plan=plan)
        except Exception:
            pass
        return None

    except Exception as e:
        log_error(f"place_trade fatal error: {e}\n{traceback.format_exc()}")
        try:
            notifications.notify_raw(f"ORDER EXCEPTION | {getattr(plan,'symbol','unknown')} | {e}")
        except Exception:
            pass
        return None


def manage_open_positions(use_trailing: bool = True, trailing_r_mult: float = 0.5):
    """
    Adjust SL/TP for open positions:
     - Break-even (one-time) when RR >= BREAKEVEN_RR (requires USE_TRAILING True and use_trailing True)
     - Partial TP (one-time) when RR >= PARTIAL_TP_RR and PARTIAL_TP_ENABLED True
     - Trailing updates when use_trailing True (and USE_TRAILING True)
    """
    pos_list = mt5.positions_get() or []
    for p in pos_list:
        try:
            tick = mt5.symbol_info_tick(p.symbol)
            if not tick:
                continue

            price = float(tick.bid) if p.type == mt5.POSITION_TYPE_SELL else float(tick.ask)
            entry = float(p.price_open)
            sl = float(p.sl) if p.sl else None
            tp = float(p.tp) if p.tp else None
            vol = float(p.volume)

            # compute risk (entry <-> sl). If no SL, skip risk-based moves.
            if sl is None:
                risk = 0.0
            else:
                risk = abs(entry - sl)

            # compute achieved R:R
            rr = 0.0
            if risk > 0:
                if p.type == mt5.POSITION_TYPE_BUY:
                    rr = (price - entry) / risk
                else:
                    rr = (entry - price) / risk

            ticket = int(getattr(p, "ticket", 0))

            # --- Break-even one-shot ---
            try:
                if USE_TRAILING and use_trailing and risk > 0 and rr >= float(BREAKEVEN_RR):
                    if ticket not in _breakeven_set_tickets:
                        # buy: set sl = entry if sl < entry
                        if p.type == mt5.POSITION_TYPE_BUY:
                            if sl is None or sl < entry - 1e-9:
                                mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "sl": entry, "tp": tp})
                                _breakeven_set_tickets.add(ticket)
                                log_info(f"BREAKEVEN SET | {p.symbol} | ticket={ticket} | new_sl={entry:.5f} | rr={rr:.2f}")
                        else:
                            if sl is None or sl > entry + 1e-9:
                                mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "sl": entry, "tp": tp})
                                _breakeven_set_tickets.add(ticket)
                                log_info(f"BREAKEVEN SET | {p.symbol} | ticket={ticket} | new_sl={entry:.5f} | rr={rr:.2f}")
            except Exception as e:
                log_debug(f"breakeven error on {ticket}: {e}")

            # --- Partial TP one-shot ---
            try:
                if PARTIAL_TP_ENABLED and risk > 0 and rr >= float(PARTIAL_TP_RR):
                    if ticket not in _partial_closed_tickets:
                        # gather symbol volume info
                        si = mt5.symbol_info(p.symbol)
                        vol_min = float(getattr(si, "volume_min", PARTIAL_TP_MIN_LOT) or PARTIAL_TP_MIN_LOT)
                        vol_step = float(getattr(si, "volume_step", 0.01) or 0.01)
                        percent = max(0.0, min(100.0, float(PARTIAL_TP_PERCENT)))
                        desired_close_vol = vol * (percent / 100.0)
                        partial_vol = _round_volume_to_step(desired_close_vol, vol_min, vol, vol_step)
                        # ensure partial_vol is valid and doesn't close whole pos
                        if partial_vol >= vol_min and partial_vol < vol - 1e-9:
                            # create opposite market order to close partial_vol
                            order_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                            price_close = float(tick.bid) if order_type == mt5.ORDER_TYPE_SELL else float(tick.ask)
                            req = {
                                "action": mt5.TRADE_ACTION_DEAL,
                                "symbol": p.symbol,
                                "volume": partial_vol,
                                "type": order_type,
                                "price": price_close,
                                "deviation": 20,
                                "magic": 424242,
                                "comment": f"partial_tp {percent:.0f}%",
                                "type_time": getattr(mt5, "ORDER_TIME_GTC", 0)
                            }
                            try:
                                r = mt5.order_send(req)
                                log_debug(f"Partial close order_send result for ticket {ticket}: {r}")
                                # treat success by retcode as OK
                                rc = getattr(r, "retcode", None) if r is not None else None
                                if r is not None and (rc == 0 or (hasattr(mt5, "TRADE_RETCODE_DONE") and rc == getattr(mt5, "TRADE_RETCODE_DONE")) or rc == 10009):
                                    _partial_closed_tickets.add(ticket)
                                    log_info(f"PARTIAL TP executed | {p.symbol} | ticket={ticket} | closed_vol={partial_vol} | rr={rr:.2f}")
                                    try:
                                        notifications.notify_position_closed(ticket, getattr(r, "profit", 0.0), summary=f"PARTIAL {percent:.0f}%")
                                    except Exception:
                                        pass
                                else:
                                    log_info(f"PARTIAL TP rejected | {p.symbol} | ticket={ticket} | result={r}")
                            except Exception as e:
                                log_error(f"Partial close exception for {ticket}: {e}")
                        else:
                            log_debug(f"Partial TP skipped for ticket {ticket}: partial_vol={partial_vol}, vol_min={vol_min}, total_vol={vol}")
            except Exception as e:
                log_debug(f"partial tp error on {ticket}: {e}")

            # --- Trailing (regular) ---
            try:
                if use_trailing and risk > 0 and USE_TRAILING:
                    if p.type == mt5.POSITION_TYPE_BUY and price - entry >= trailing_r_mult * risk:
                        new_sl = max(sl or -1e9, price - trailing_r_mult * risk)
                        # only update if meaningful change
                        if sl is None or abs(new_sl - sl) > 1e-8:
                            mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "sl": new_sl, "tp": tp})
                            log_info(f"TRAILING SL updated | {p.symbol} | ticket={ticket} | old_sl={sl} new_sl={new_sl:.5f} | rr={rr:.2f}")
                    if p.type == mt5.POSITION_TYPE_SELL and entry - price >= trailing_r_mult * risk:
                        new_sl = min(sl or 1e9, price + trailing_r_mult * risk)
                        if sl is None or abs(new_sl - sl) > 1e-8:
                            mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket, "sl": new_sl, "tp": tp})
                            log_info(f"TRAILING SL updated | {p.symbol} | ticket={ticket} | old_sl={sl} new_sl={new_sl:.5f} | rr={rr:.2f}")
            except Exception as e:
                log_debug(f"trailing error on {ticket}: {e}")

        except Exception as e:
            log_error(f"manage_open_positions error on {getattr(p,'ticket',None)}: {e}\n{traceback.format_exc()}")
