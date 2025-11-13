# metrics.py (MT5-first PnL & stats, CSV fallback)
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Tuple, Iterable, Any
import csv
import pandas as pd
from config import JOURNAL_CSV, START_BALANCE, USD_CZK
import MetaTrader5 as mt5
from logger import log_debug

def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"Nelze převést na Decimal: {value}") from e

def get_initial_balance(account_info: Optional[Any] = None) -> Decimal:
    """
    Determine initial account balance.
    - First check environment INITIAL_BALANCE or START_BALANCE
    - Then check account_info (mt5.account_info() object or dict-like)
    """
    env_val = os.getenv("INITIAL_BALANCE") or os.getenv("START_BALANCE")
    if env_val:
        return _to_decimal(env_val)
    if account_info:
        # mt5.account_info() returns object with attributes; try common names
        for key in ("initial_balance", "initialCapital", "starting_balance", "startingBalance", "balance", "login"):
            # handle dict-like
            if isinstance(account_info, dict) and key in account_info and account_info[key] is not None:
                return _to_decimal(account_info[key])
        # object attributes
        for attr in ("initial_balance", "initialCapital", "starting_balance", "startingBalance", "balance"):
            if hasattr(account_info, attr):
                val = getattr(account_info, attr)
                if val is not None:
                    return _to_decimal(val)
    # fallback to config START_BALANCE
    return _to_decimal(str(START_BALANCE))

def calculate_pnl(current_balance: Any, initial_balance: Any) -> Tuple[Decimal, Decimal]:
    cur = _to_decimal(current_balance)
    init = _to_decimal(initial_balance)
    if init == 0:
        raise ZeroDivisionError("Inicialni balance je 0, nelze spočítat procentuální PnL.")
    pnl_usd = (cur - init).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    pnl_pct = ((pnl_usd / init) * Decimal("100")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return pnl_usd, pnl_pct

def format_pnl(pnl_usd: Decimal, pnl_pct: Decimal) -> str:
    sign_usd = "-" if pnl_usd < 0 else "+"
    sign_pct = "-" if pnl_pct < 0 else "+"
    us = abs(pnl_usd)
    pc = abs(pnl_pct)
    return f"{sign_usd}{us:.2f} USD ({sign_pct}{pc:.4f}%)"

def update_and_report_from_mt5(acc_info: Optional[Any] = None) -> Dict[str, Any]:
    """
    Use MT5 account info (or fetch it) to compute current PnL relative to initial balance.
    Returns dict with initial_balance, current_balance, pnl_usd, pnl_pct, pnl_display.
    """
    try:
        if acc_info is None:
            acc_info = mt5.account_info()
    except Exception as e:
        log_debug(f"mt5.account_info() failed: {e}")
        acc_info = None

    try:
        current_balance = float(getattr(acc_info, "balance", None) or (acc_info.get("balance") if isinstance(acc_info, dict) else 0.0) or 0.0)
    except Exception:
        current_balance = 0.0

    init = get_initial_balance(account_info=acc_info)
    pnl_usd, pnl_pct = calculate_pnl(current_balance, init)
    return {
        "initial_balance": float(init),
        "current_balance": float(current_balance),
        "pnl_usd": float(pnl_usd),
        "pnl_pct": float(pnl_pct),
        "pnl_display": format_pnl(pnl_usd, pnl_pct),
    }

# --- MT5 history-based aggregation helpers ---

def _sum_deals_pnl_mt5(start_dt: datetime, end_dt: datetime) -> Tuple[int, float]:
    """
    Sum closed trade PnL from MT5 history deals between start_dt and end_dt (inclusive start, exclusive end).
    Returns (closed_trades_count, pnl_sum)
    Counts unique position_id for DEAL_ENTRY_OUT events.
    """
    try:
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        deals = mt5.history_deals_get(start_dt, end_dt) or []
    except Exception as e:
        log_debug(f"mt5.history_deals_get failed: {e}")
        return 0, 0.0

    pnl_sum = 0.0
    closed_positions = set()
    for d in deals:
        try:
            # only consider closed deals (exit)
            entry_type = getattr(d, "entry", None)
            if entry_type != mt5.DEAL_ENTRY_OUT:
                continue
            ticket = getattr(d, "position_id", None) or getattr(d, "order", None) or None
            profit = float(getattr(d, "profit", 0.0) or 0.0)
            commission = float(getattr(d, "commission", 0.0) or 0.0)
            swap = float(getattr(d, "swap", 0.0) or 0.0)
            aggregated = profit + commission + swap
            pnl_sum += aggregated
            if ticket is not None:
                closed_positions.add(ticket)
        except Exception:
            continue
    return len(closed_positions), round(pnl_sum, 2)

# --- Public functions used by other modules ---

def daily_pnl_from_logs() -> Tuple[int, float]:
    """
    Prefer MT5 history for today's closed PnL. Fallback to CSV if MT5 not available.
    Returns (closed_count_today, pnl_sum_today)
    """
    try:
        now = datetime.now(timezone.utc)
        start = datetime(year=now.year, month=now.month, day=now.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        cnt, pnl = _sum_deals_pnl_mt5(start, end)
        # if MT5 returned valid data (could be zero) we return it
        return cnt, pnl
    except Exception as e:
        log_debug(f"MT5 daily_pnl fetch failed: {e}")

    # fallback CSV
    try:
        df = pd.read_csv(JOURNAL_CSV, on_bad_lines='skip')
    except FileNotFoundError:
        return 0, 0.0
    if 'date' not in df.columns or 'status' not in df.columns or 'pnl' not in df.columns:
        return 0, 0.0
    today = pd.Timestamp.utcnow().date().isoformat()
    closed_today = df[(df['date'] == today) & (df['status'] == 'closed')]
    cnt = int(closed_today.shape[0])
    pnl = float(closed_today['pnl'].sum()) if cnt > 0 else 0.0
    return cnt, pnl

def month_stats() -> Tuple[int, float, float, float]:
    """
    Prefer MT5 history for month stats. Returns (trades_count, pnl_usd, pnl_czk, pct)
    Falls back to CSV journal if MT5 not available.
    """
    try:
        now = datetime.now(timezone.utc)
        month_start = datetime(year=now.year, month=now.month, day=1, tzinfo=timezone.utc)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        cnt, pnl_usd = _sum_deals_pnl_mt5(month_start, next_month)
        pnl_czk = round(pnl_usd * float(os.getenv("USD_CZK", USD_CZK)), 0)
        try:
            start = float(os.getenv("START_BALANCE", START_BALANCE))
            pct = (pnl_usd / max(1.0, start)) * 100.0
        except Exception:
            pct = 0.0
        return cnt, pnl_usd, pnl_czk, pct
    except Exception as e:
        log_debug(f"MT5 month_stats failed: {e}")

    # fallback CSV behavior
    try:
        df = pd.read_csv(JOURNAL_CSV, on_bad_lines='skip')
    except FileNotFoundError:
        return 0, 0.0, 0.0, 0.0
    if 'datetime_utc' not in df.columns or 'status' not in df.columns or 'pnl' not in df.columns:
        return 0, 0.0, 0.0, 0.0
    df['datetime_utc'] = pd.to_datetime(df['datetime_utc'], utc=True, errors='coerce')
    now = pd.Timestamp.utcnow()
    month_start = pd.Timestamp(year=now.year, month=now.month, day=1)
    mdf = df[(df['datetime_utc'] >= month_start) & (df['status'] == 'closed')]
    trades = int(mdf.shape[0])
    pnl_usd = float(mdf['pnl'].sum()) if trades > 0 else 0.0
    pnl_czk = round(pnl_usd * float(os.getenv("USD_CZK", USD_CZK)), 0)
    try:
        start = float(os.getenv("START_BALANCE", START_BALANCE))
        pct = (pnl_usd / max(1.0, start)) * 100.0
    except Exception:
        pct = 0.0
    return trades, pnl_usd, pnl_czk, pct

# --- CSV journal helpers remain for backward compatibility ---
JOURNAL_FIELDS = [
    "datetime_utc", "date", "time", "week", "symbol", "direction",
    "ticket", "lots", "entry", "sl", "tp", "status", "pnl"
]

def _ensure_journal_exists():
    if not os.path.exists(JOURNAL_CSV):
        try:
            with open(JOURNAL_CSV, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
                writer.writeheader()
        except Exception:
            pass

def init_trade_row(plan, ticket=0) -> Dict[str, Any]:
    dt = datetime.utcnow().replace(tzinfo=timezone.utc)
    date = dt.date().isoformat()
    time_s = dt.time().isoformat()
    week = dt.isocalendar().week
    return {
        "datetime_utc": dt.isoformat(),
        "date": date,
        "time": time_s,
        "week": week,
        "symbol": getattr(plan, "symbol", ""),
        "direction": getattr(plan, "direction", ""),
        "ticket": int(ticket or 0),
        "lots": float(getattr(plan, "lots", 0.0)),
        "entry": float(getattr(plan, "entry_price", 0.0)),
        "sl": float(getattr(plan, "sl", 0.0)),
        "tp": float(getattr(plan, "tp", 0.0)),
        "status": "opened" if ticket else "skipped",
        "pnl": 0.0
    }

def append_journal(row: Dict[str, Any]):
    _ensure_journal_exists()
    try:
        with open(JOURNAL_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
            writer.writerow({k: row.get(k, "") for k in JOURNAL_FIELDS})
    except Exception:
        pass

def update_closed_trade(ticket: int, pnl: float):
    """
    Append a 'closed' record for ticket with pnl. We append a separate row with status 'closed'.
    """
    _ensure_journal_exists()
    dt = datetime.utcnow().replace(tzinfo=timezone.utc)
    row = {
        "datetime_utc": dt.isoformat(),
        "date": dt.date().isoformat(),
        "time": dt.time().isoformat(),
        "week": dt.isocalendar().week,
        "symbol": "",
        "direction": "",
        "ticket": int(ticket or 0),
        "lots": 0.0,
        "entry": 0.0,
        "sl": 0.0,
        "tp": 0.0,
        "status": "closed",
        "pnl": float(pnl)
    }
    append_journal(row)
    return True
