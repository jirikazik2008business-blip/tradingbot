# analysis/zones.py
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np
from data import fetch_rates
from logger import log_debug, log_error
from datetime import datetime, timezone, timedelta

def _symbol_pip(symbol: str) -> float:
    """Return pip size for common FX pairs (approx)."""
    sym = (symbol or "").upper()
    if "JPY" in sym:
        return 0.01
    return 0.0001

def _filter_last_days(df: pd.DataFrame, days: int = 365) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    try:
        ts = pd.to_datetime(df["time"], errors="coerce", utc=True)
    except Exception:
        try:
            ts = pd.to_datetime(df["time"], errors="coerce")
            ts = ts.dt.tz_localize(timezone.utc)
        except Exception:
            # give up and return empty
            log_debug("_filter_last_days: failed to parse times, returning empty df")
            return pd.DataFrame()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    df = df.copy()
    df["__ts"] = ts
    df = df[df["__ts"] >= cutoff].drop(columns="__ts")
    return df

def _local_extrema(series: pd.Series, order: int = 3) -> Tuple[List[int], List[int]]:
    """
    Find local maxima and minima indices using simple neighborhood check.
    'order' determines the number of neighbors on each side.
    Returns (high_indices, low_indices).
    """
    highs: List[int] = []
    lows: List[int] = []
    try:
        n = len(series)
        if n < (order * 2 + 1):
            return highs, lows
        # Ensure numeric
        arr = pd.to_numeric(series, errors="coerce").reset_index(drop=True)
        for i in range(order, n - order):
            window = arr.iloc[i - order:i + order + 1]
            center = arr.iloc[i]
            if np.isnan(center):
                continue
            # strict equality check to avoid duplicate plateau issues: require center == max and unique or at least >= others
            if center >= window.max() and center == window.max():
                highs.append(i)
            if center <= window.min() and center == window.min():
                lows.append(i)
    except Exception as e:
        log_debug(f"_local_extrema failed: {e}")
    return highs, lows

def _cluster_levels(levels: List[float], pip: float, cluster_tol_multiplier: float = 5.0, max_levels: int = 20) -> List[float]:
    """
    Simple clustering: groups levels closer than (pip * cluster_tol_multiplier).
    Returns sorted unique levels (ascending).
    """
    try:
        if not levels:
            return []
        tol = max(1e-8, pip * cluster_tol_multiplier)
        levels_sorted = sorted([float(l) for l in levels if np.isfinite(l)])
        clusters: List[float] = []
        cur = [levels_sorted[0]]
        for lv in levels_sorted[1:]:
            if abs(lv - np.mean(cur)) <= tol:
                cur.append(lv)
            else:
                clusters.append(float(np.mean(cur)))
                cur = [lv]
        if cur:
            clusters.append(float(np.mean(cur)))
        # round levels to reasonable precision, dedupe and sort
        prec = 6 if pip < 0.0005 else 4
        rounded = sorted({round(x, prec) for x in clusters})
        # trim to max_levels (keep those with most clustered neighbourhood density)
        if len(rounded) <= max_levels:
            return rounded
        # compute a simple significance heuristic: count original levels within +-tol of each rounded
        sig = []
        for r in rounded:
            count = sum(1 for v in levels if abs(v - r) <= tol)
            sig.append((r, count))
        sig_sorted = sorted(sig, key=lambda x: (-x[1], x[0]))  # most occurrences first, then price
        top = [r for r, _ in sig_sorted[:max_levels]]
        return sorted(top)
    except Exception as e:
        log_error(f"_cluster_levels error: {e}")
        return []

def compute_zones_from_tf(symbol: str, tf: str = "D1", lookback_bars: int = 400, days_limit: Optional[int] = None) -> List[float]:
    """
    Compute candidate zones (levels) from a timeframe.
    - symbol: e.g. 'EURUSD'
    - tf: timeframe string supported by data.fetch_rates (e.g. 'D1','H4','H1')
    - lookback_bars: how many bars to fetch
    - days_limit: optional limit in days to trim history
    Returns list of floats (levels) sorted ascending.
    """
    try:
        df = fetch_rates(symbol, tf, bars=lookback_bars)
    except Exception as e:
        log_debug(f"compute_zones_from_tf: fetch_rates failed for {symbol} {tf}: {e}")
        df = pd.DataFrame()

    if df is None or df.empty:
        log_debug(f"compute_zones_from_tf: no data for {symbol} {tf}")
        return []

    if days_limit:
        df = _filter_last_days(df, days=days_limit)
        if df.empty:
            log_debug(f"compute_zones_from_tf: after days_limit filter no data for {symbol} {tf}")
            return []

    # Ensure numeric columns
    for col in ("high", "low", "open", "close"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Defensive: need at least some bars
    if df.shape[0] < 5:
        log_debug(f"compute_zones_from_tf: too few bars ({df.shape[0]}) for {symbol} {tf}")
        return []

    # Use local extrema (swing highs/lows) on highs/lows
    highs_idx, _ = _local_extrema(df["high"], order=3)
    _, lows_idx = _local_extrema(df["low"], order=3)

    levels: List[float] = []
    try:
        for i in highs_idx:
            try:
                v = float(df["high"].iloc[i])
                if np.isfinite(v):
                    levels.append(v)
            except Exception:
                continue
        for i in lows_idx:
            try:
                v = float(df["low"].iloc[i])
                if np.isfinite(v):
                    levels.append(v)
            except Exception:
                continue
    except Exception as e:
        log_debug(f"compute_zones_from_tf: error extracting extrema: {e}")

    # also include visible pivots: take last N highest highs / lowest lows to not miss important levels
    try:
        top_highs = df["high"].nlargest(12).unique().tolist()
        top_lows = df["low"].nsmallest(12).unique().tolist()
        for v in top_highs + top_lows:
            try:
                if np.isfinite(v):
                    levels.append(float(v))
            except Exception:
                continue
    except Exception:
        pass

    # cluster levels to remove near-duplicates
    pip = _symbol_pip(symbol)
    clustered = _cluster_levels(levels, pip, cluster_tol_multiplier=8.0, max_levels=60)

    # Return sorted by price (ascending) for consistency
    out = sorted(clustered)
    log_debug(f"{symbol} zones on {tf}: {out[:20]}")
    return out

# helper: compute zones across multiple Tfs and merge
def compute_zones_for_symbol(symbol: str, tfs: List[str] = None, lookback_bars: int = 400, keep_top: int = 12) -> List[float]:
    """
    Collect zones from multiple timeframes (e.g., ['D1','H4']) and merge/cluster them.
    Returns top 'keep_top' levels sorted descending (so highest first in UI).
    """
    if tfs is None:
        tfs = ["D1", "H4"]
    all_levels: List[float] = []
    for tf in tfs:
        try:
            lv = compute_zones_from_tf(symbol, tf, lookback_bars=lookback_bars)
            all_levels.extend(lv)
        except Exception as e:
            log_debug(f"compute_zones_for_symbol: compute_zones_from_tf failed for {tf}: {e}")
    pip = _symbol_pip(symbol)
    merged = _cluster_levels(all_levels, pip, cluster_tol_multiplier=8.0, max_levels=keep_top * 3)
    # pick top 'keep_top' by density/significance heuristic
    if not merged:
        log_debug(f"compute_zones_for_symbol {symbol}: no merged levels found")
        return []
    # significance: how many raw levels fall within tol
    tol = pip * 8.0
    significance = []
    for m in merged:
        count = sum(1 for v in all_levels if abs(v - m) <= tol)
        significance.append((m, count))
    significance_sorted = sorted(significance, key=lambda x: (-x[1], -x[0]))  # prefer more votes, higher price
    top = [round(m, 6) for m, _ in significance_sorted[:keep_top]]
    merged_sorted = sorted(top, reverse=True)  # descending for UI (highest first)
    log_debug(f"compute_zones_for_symbol {symbol} merged: {merged_sorted}")
    return merged_sorted
