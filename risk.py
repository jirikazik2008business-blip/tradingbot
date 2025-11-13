# risk.py
import math
import MetaTrader5 as mt5
import pandas as pd
from config import (
    POSITION_MODE, RISK_PCT, LOTS_FIXED, FAT_FINGER_MAX_LOTS, MAX_SPREAD_PIPS,
    JOURNAL_CSV, MAX_POSITIONS_PER_SYMBOL, MAX_TRADES_PER_DAY, MAX_TRADES_PER_WEEK,
    MAX_DRAWDOWN_PCT, MAX_DAILY_LOSS_PCT, MAX_RISK_PER_TRADE_PCT, CONSECUTIVE_LOSS_LIMIT
)
from logger import log_debug, log_error
from metrics import daily_pnl_from_logs

def pip_size(symbol: str) -> float:
    if symbol.endswith("JPY"):
        return 0.01
    if symbol.startswith("XAU"):
        return 0.1
    return 0.0001

def compute_lots(symbol: str, entry: float, sl: float, equity: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"No symbol info for {symbol}")
    if POSITION_MODE == "FIXED":
        lots = LOTS_FIXED
    else:
        stop_pips = max(1e-9, abs(entry - sl) / pip_size(symbol))
        tick_value = getattr(info, "trade_tick_value", None) or 1.0
        tick_size = getattr(info, "trade_tick_size", None) or pip_size(symbol)
        value_per_pip = tick_value * (pip_size(symbol) / tick_size)
        chosen_risk_pct = min(RISK_PCT, MAX_RISK_PER_TRADE_PCT)
        risk_amount = equity * chosen_risk_pct
        lots = (risk_amount / (stop_pips * value_per_pip)) / (getattr(info, "trade_contract_size", 1.0))

    lot_step = getattr(info, "volume_step", 0.01) or 0.01
    min_lot = getattr(info, "volume_min", 0.01) or 0.01
    max_lot = min(getattr(info, "volume_max", 100.0) or 100.0, FAT_FINGER_MAX_LOTS)
    if lot_step > 0:
        lots = math.floor(max(0.0, lots) / lot_step) * lot_step
    lots = max(min_lot, min(max_lot, lots))
    try:
        step_decimals = max(0, int(round(-math.log10(lot_step))))
    except Exception:
        step_decimals = 2
    return round(lots, step_decimals)

def spread_ok(symbol: str) -> bool:
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return False
    spread = abs(tick.ask - tick.bid) / pip_size(symbol)
    return spread <= MAX_SPREAD_PIPS

def positions_count_for_symbol(symbol: str) -> int:
    pos = mt5.positions_get(symbol=symbol) or []
    return len(pos)

def trade_limits_ok() -> bool:
    try:
        df = pd.read_csv(JOURNAL_CSV, on_bad_lines='skip')
    except FileNotFoundError:
        return True
    today = pd.Timestamp.utcnow().date().isoformat()
    week = pd.Timestamp.utcnow().isocalendar().week
    day_count = int(df[df.get('date') == today].shape[0]) if 'date' in df.columns else 0
    week_count = int(df[df.get('week') == week].shape[0]) if 'week' in df.columns else 0
    return day_count < MAX_TRADES_PER_DAY and week_count < MAX_TRADES_PER_WEEK

def consecutive_losses_today() -> int:
    try:
        df = pd.read_csv(JOURNAL_CSV, on_bad_lines='skip')
    except FileNotFoundError:
        return 0
    today = pd.Timestamp.utcnow().date().isoformat()
    if 'date' not in df.columns or 'status' not in df.columns or 'pnl' not in df.columns:
        return 0
    closed_today = df[(df['date'] == today) & (df['status'] == 'closed')]
    if closed_today.empty:
        return 0
    if 'time' in closed_today.columns:
        closed_today = closed_today.sort_values('time')
    else:
        closed_today = closed_today.reset_index()
    consec = 0
    for _, row in closed_today.iloc[::-1].iterrows():
        if float(row.get('pnl', 0.0)) < 0:
            consec += 1
        else:
            break
    return consec

def risk_gates_ok(equity_start: float, balance: float, equity: float) -> bool:
    try:
        dd = (equity_start - equity) / max(1e-9, equity_start)
    except Exception:
        dd = 0.0
    if dd >= MAX_DRAWDOWN_PCT:
        log_error(f"Max drawdown hit: {dd:.2%}")
        return False

    try:
        cnt, daily_pnl = daily_pnl_from_logs()
        if daily_pnl < 0 and abs(daily_pnl) >= (balance * MAX_DAILY_LOSS_PCT):
            log_error(f"Max daily loss hit: {daily_pnl:.2f}")
            return False
    except Exception as e:
        log_debug(f"Could not determine daily pnl from logs: {e}")

    try:
        cons = consecutive_losses_today()
        if cons >= CONSECUTIVE_LOSS_LIMIT:
            log_error(f"Consecutive loss limit reached: {cons}")
            return False
    except Exception as e:
        log_debug(f"consecutive_losses_today check failed: {e}")

    return True
