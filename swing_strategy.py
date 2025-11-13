from typing import Optional
from data import fetch_rates
from logger import log_info, log_debug
from analysis.zones import compute_zones_from_tf
from analysis.mtf import sma_trend_from_df, weekly_trend_from_daily
from analysis.volume import equilibrium_level, has_recent_FVG
from trade import TradePlan
from risk import compute_lots, spread_ok, pip_size
from comments import load_comment
import MetaTrader5 as mt5
from config import REQUIRE_CONTINUATION, START_BALANCE
from datetime import datetime, timezone, timedelta
import pandas as pd

def _filter_last_hours(df: pd.DataFrame, hours: int = 4) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    try:
        ts = pd.to_datetime(df["time"], errors="coerce", utc=True)
    except Exception:
        ts = pd.to_datetime(df["time"], errors="coerce")
        ts = ts.dt.tz_localize(timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    df = df.copy()
    df["__ts"] = ts
    df = df[df["__ts"] >= cutoff].drop(columns="__ts")
    return df

def _sma_trend(df: pd.DataFrame, length: int = 20) -> Optional[str]:
    if df is None or df.empty or len(df) < length:
        return None
    closes = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(closes) < length:
        return None
    sma = closes.rolling(window=length).mean().iloc[-1]
    last = float(closes.iloc[-1])
    return "bull" if last > sma else "bear"

def _count_zone_touches(df: pd.DataFrame, level: float, tol: float) -> int:
    if df is None or df.empty:
        return 0
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    cond = (high >= (level - tol)) & (low <= (level + tol))
    return int(cond.sum())

def _is_bullish_engulfing(df: pd.DataFrame) -> bool:
    if df is None or len(df) < 2:
        return False
    prev = df.iloc[-2]
    cur = df.iloc[-1]
    try:
        return (prev["close"] < prev["open"]) and (cur["close"] > cur["open"]) and (cur["close"] > prev["open"])
    except Exception:
        return False

def _is_bearish_engulfing(df: pd.DataFrame) -> bool:
    if df is None or len(df) < 2:
        return False
    prev = df.iloc[-2]
    cur = df.iloc[-1]
    try:
        return (prev["close"] > prev["open"]) and (cur["close"] < cur["open"]) and (cur["close"] < prev["open"])
    except Exception:
        return False

def _is_rejection_wick(df: pd.DataFrame, direction: str, body_wick_ratio: float = 0.4) -> bool:
    if df is None or len(df) < 1:
        return False
    c = df.iloc[-1]
    try:
        o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
    except Exception:
        return False
    body = abs(cl - o)
    full = h - l
    if full <= 0:
        return False
    wick_top = h - max(o, cl)
    wick_bottom = min(o, cl) - l
    if direction == "bull":
        return (wick_bottom >= body * (1.5 / max(1.0, body_wick_ratio))) and (wick_top <= body * 1.0)
    else:
        return (wick_top >= body * (1.5 / max(1.0, body_wick_ratio))) and (wick_bottom <= body * 1.0)

def _confirm_on_higher_tf(symbol: str, rej_dir: str) -> bool:
    try:
        df_h1 = fetch_rates(symbol, "H1", bars=50)
    except Exception:
        df_h1 = pd.DataFrame()
    try:
        df_m30 = fetch_rates(symbol, "M30", bars=50)
    except Exception:
        df_m30 = pd.DataFrame()

    df_h1 = _filter_last_hours(df_h1, hours=24)
    df_m30 = _filter_last_hours(df_m30, hours=24)

    if rej_dir == "bull":
        if _is_bullish_engulfing(df_h1) or _is_rejection_wick(df_h1, "bull"):
            return True
        if _is_bullish_engulfing(df_m30) or _is_rejection_wick(df_m30, "bull"):
            return True
    else:
        if _is_bearish_engulfing(df_h1) or _is_rejection_wick(df_h1, "bear"):
            return True
        if _is_bearish_engulfing(df_m30) or _is_rejection_wick(df_m30, "bear"):
            return True
    return False

def _choose_sl_tp_from_rej(df_entry, rej_idx, direction, account_equity):
    if rej_idx is None or rej_idx < 1 or rej_idx >= len(df_entry):
        rej_idx = max(1, len(df_entry) - 2)
    candle = df_entry.iloc[rej_idx]
    entry = float(df_entry["close"].iloc[-1])
    atr = float((df_entry["high"] - df_entry["low"]).rolling(14).mean().iloc[-1]) if len(df_entry) >= 14 else (float(df_entry["high"].iloc[-1] - df_entry["low"].iloc[-1]) * 0.5)
    buffer = max(atr * 0.15, entry * 0.0003)
    symbol = df_entry.get("symbol").iloc[-1] if "symbol" in df_entry.columns else None
    psize = pip_size(symbol) if symbol else 0.0001
    pip_buffer = 5 * psize

    if direction == "bear":
        sl_candidate = max(float(getattr(candle, "high", candle["high"])), float(df_entry["high"].iloc[rej_idx-1])) + buffer
        sl = max(sl_candidate, entry + pip_buffer)
        risk = abs(entry - sl)
        tp = entry - max(risk * 2.0, atr * 2)
    else:
        sl_candidate = min(float(getattr(candle, "low", candle["low"])), float(df_entry["low"].iloc[rej_idx-1])) - buffer
        sl = min(sl_candidate, entry - pip_buffer)
        risk = abs(entry - sl)
        tp = entry + max(risk * 2.0, atr * 2)

    return float(entry), float(sl), float(tp)

def build_entry_plan(symbol: str, tf_high="H4", tf_mid="H1", tf_entry="M5", min_score=25.0) -> Optional[TradePlan]:
    try:
        df_daily = fetch_rates(symbol, "D1", bars=400)
        df_h4 = fetch_rates(symbol, "H4", bars=600)
        df_h1 = fetch_rates(symbol, "H1", bars=600)
        df_entry = fetch_rates(symbol, tf_entry, bars=600)
    except Exception as e:
        log_debug(f"fetch_rates failed for {symbol}: {e}")
        return None

    df_entry = _filter_last_hours(df_entry, hours=6)
    df_h1 = _filter_last_hours(df_h1, hours=24)
    df_h4 = _filter_last_hours(df_h4, hours=72)
    if df_daily is None:
        df_daily = pd.DataFrame()
    else:
        df_daily = df_daily.tail(200)

    if df_entry is None or df_entry.empty:
        return None

    price = float(df_entry["close"].iloc[-1])

    # zones from daily (and weekly via resample)
    levels_daily = compute_zones_from_tf(symbol, tf="D1", lookback_bars=400)
    if not levels_daily:
        log_debug(f"{symbol} no daily zones available")
        return None

    # choose nearest level
    # tolerance derived from recent volatility
    atr = float((df_entry["high"] - df_entry["low"]).rolling(14).mean().iloc[-1]) if len(df_entry) >= 14 else max(1e-9, float(df_entry["high"].iloc[-1] - df_entry["low"].iloc[-1]) * 0.5)
    tol = max(atr * 0.25, price * 0.0006)
    hit_levels = [lvl for lvl in levels_daily if abs(price - lvl) <= tol]
    if not hit_levels:
        log_debug(f"{symbol} price {price:.5f} not within tol {tol:.6f} of any daily zone")
        return None
    level = min(hit_levels, key=lambda l: abs(price - l))

    touches_daily = _count_zone_touches(df_daily, level, tol * 4) if not df_daily.empty else 0
    touches_weekly = 0
    try:
        if not df_daily.empty:
            d = df_daily.copy()
            d["time"] = pd.to_datetime(d["time"], utc=True)
            d.set_index("time", inplace=True)
            w_df = d.resample("W").agg({"high":"max","low":"min"}).reset_index()
            touches_weekly = _count_zone_touches(w_df, level, tol * 10)
    except Exception:
        touches_weekly = 0

    if touches_daily < 3 and touches_weekly < 3:
        log_debug(f"{symbol} zone {level} fails touch check (daily={touches_daily}, weekly={touches_weekly})")
        return None

    # detect reversal on entry tf using basic structural checks + FVG/BOS if needed
    # simple rules here: look for engulfing or wick rejection on entry
    rej_dir = None
    if _is_bullish_engulfing(df_entry) or _is_rejection_wick(df_entry, "bull"):
        rej_dir = "bull"
    elif _is_bearish_engulfing(df_entry) or _is_rejection_wick(df_entry, "bear"):
        rej_dir = "bear"
    else:
        # fallback to volume-based FVG detection
        if has_recent_FVG(df_entry, "bull"):
            rej_dir = "bull"
        elif has_recent_FVG(df_entry, "bear"):
            rej_dir = "bear"

    if not rej_dir:
        log_debug(f"{symbol} no clear reversal on entry timeframe")
        return None

    # higher timeframe consensus check
    wt = weekly_trend_from_daily(df_daily) if not df_daily.empty else None
    dt = _sma_trend(df_daily, length=20) if not df_daily.empty else None
    h4t = _sma_trend(df_h4, length=20) if not df_h4.empty else None
    trends = [t for t in (wt, dt, h4t) if t is not None]
    consensus = None
    if len(trends) >= 2:
        bulls = sum(1 for t in trends if t == "bull")
        bears = sum(1 for t in trends if t == "bear")
        consensus = "bull" if bulls > bears else ("bear" if bears > bulls else None)
    elif len(trends) == 1:
        consensus = trends[0]
    # do not trade against consensus
    if consensus is not None:
        if (consensus == "bull" and rej_dir == "bear") or (consensus == "bear" and rej_dir == "bull"):
            log_debug(f"{symbol} rejection {rej_dir} conflicts with TF consensus {consensus}")
            return None

    # require H1/M30 confirmation candle
    if not _confirm_on_higher_tf(symbol, rej_dir):
        log_debug(f"{symbol} no H1/M30 confirmation for {rej_dir}")
        return None

    # continuation check (simple equilibrium or FVG)
    cont_ok = False
    try:
        eq = equilibrium_level(df_entry, lookback=20)
        if abs(price - eq) <= eq * 0.002:
            cont_ok = True
        if has_recent_FVG(df_entry, rej_dir):
            cont_ok = True
    except Exception:
        cont_ok = False

    if REQUIRE_CONTINUATION and not cont_ok:
        log_debug(f"{symbol} continuation check failed")
        return None

    # spread check
    try:
        if not spread_ok(symbol):
            log_debug(f"{symbol} spread too high, skipping")
            return None
    except Exception:
        pass

    # equity
    try:
        acc = mt5.account_info()
        equity = float(acc.equity) if acc else float(START_BALANCE)
    except Exception:
        equity = float(START_BALANCE)

    # choose sl/tp
    # find a rejection index: last significant candle
    rej_idx = None
    for i in range(len(df_entry)-2, 1, -1):
        if df_entry["low"].iloc[i] <= level <= df_entry["high"].iloc[i]:
            rej_idx = i
            break
    entry_price, sl, tp = _choose_sl_tp_from_rej(df_entry, rej_idx, rej_dir, equity)

    # lots
    try:
        lots = compute_lots(symbol, entry_price, sl, equity)
        if lots <= 0:
            log_debug(f"{symbol} computed lots=0, skipping")
            return None
    except Exception as e:
        log_debug(f"compute_lots failed: {e}")
        return None

    direction = "short" if rej_dir == "bear" else "long"
    comment = load_comment(direction) or f"SWING {direction} lvl_hit"

    plan = TradePlan(symbol=symbol,
                     direction=direction,
                     entry_price=entry_price,
                     sl=sl,
                     tp=tp,
                     lots=lots,
                     tf_entry=tf_entry,
                     score=0.0,
                     comment=comment)
    log_info(f"PLAN | {symbol} | {direction} | level≈{level:.5f} | entry≈{entry_price:.5f} | SL={sl:.5f} | TP={tp:.5f} | lots={lots}")
    return plan
