from typing import Optional
import pandas as pd
from data import fetch_rates
from logger import log_debug

def sma_trend_from_df(df: pd.DataFrame, length: int = 20) -> Optional[str]:
    if df is None or df.empty or len(df) < length:
        return None
    closes = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(closes) < length:
        return None
    sma = closes.rolling(window=length).mean().iloc[-1]
    last = float(closes.iloc[-1])
    return "bull" if last > sma else "bear"

def weekly_trend_from_daily(df_daily: pd.DataFrame) -> Optional[str]:
    if df_daily is None or df_daily.empty:
        return None
    d = df_daily.copy()
    d["time"] = pd.to_datetime(d["time"], utc=True)
    d.set_index("time", inplace=True)
    try:
        weekly = d["close"].resample("W").last().dropna()
        if len(weekly) < 3:
            return None
        sma = weekly.rolling(window=min(5, len(weekly))).mean().iloc[-1]
        last = float(weekly.iloc[-1])
        return "bull" if last > sma else "bear"
    except Exception as e:
        log_debug(f"weekly_trend_from_daily failed: {e}")
        return None
