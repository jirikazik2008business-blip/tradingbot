# data.py
import MetaTrader5 as mt5
import pandas as pd
from logger import log_error, log_debug
from config import LOG_TZ

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1
}

def fetch_rates(symbol: str, tf: str, bars: int = 800) -> pd.DataFrame:
    timeframe = TIMEFRAME_MAP.get(tf)
    if timeframe is None:
        raise ValueError(f"Unknown timeframe {tf}")
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None:
        log_error(f"Rates fetch failed for {symbol} {tf}: {mt5.last_error()}")
        raise RuntimeError(f"Rates fetch failed for {symbol} {tf}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    log_debug(f"Fetched {len(df)} bars for {symbol} {tf}")
    return df
