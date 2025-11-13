import pandas as pd
from logger import log_debug

def equilibrium_level(df: pd.DataFrame, lookback: int = 20) -> float:
    wnd = df.iloc[-lookback:]
    hi = float(wnd["high"].max())
    lo = float(wnd["low"].min())
    return (hi + lo) / 2.0

def has_recent_FVG(df: pd.DataFrame, direction: str, lookback: int = 40) -> bool:
    end = len(df) - 3
    start = max(1, end - lookback)
    for i in range(end, start-1, -1):
        if direction == "bull":
            if df["low"].iloc[i] > df["high"].iloc[i+2]:
                return True
        else:
            if df["high"].iloc[i] < df["low"].iloc[i+2]:
                return True
    return False
