# utils.py
from datetime import datetime, time, timedelta, timezone
import pytz
import os
from typing import Optional, Tuple

LOG_TZ = os.getenv("LOG_TZ", "Europe/Prague")

def parse_env_time(t: str) -> time:
    """
    Parseuje čas ve formátu "HH:MM" nebo "HH:MM:SS" a vrací datetime.time (no tz).
    Používá se pro zadání session hranic (ve formátu lokálního času dané session,
    typicky New York time).
    """
    if not t or not isinstance(t, str):
        raise ValueError("Invalid time string")
    t = t.strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            dt = datetime.strptime(t, fmt)
            return time(dt.hour, dt.minute, dt.second)
        except Exception:
            continue
    raise ValueError(f"Unsupported time format: {t}")

def _ensure_aware_dt(dt: datetime) -> datetime:
    """Pokud je dt naive, považuje ho za UTC a nastaví tzinfo."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def in_ny_session(now: Optional[datetime], start_time: time, end_time: time,
                  ny_tz_name: str = "America/New_York") -> bool:
    """
    Vrací True pokud aktuální okamžik (now) leží v NY session definované
    start_time..end_time. now může být aware nebo naive (pokud naive, považuje se za UTC).
    start_time/end_time jsou time objekty v NY lokálním čase.
    Podpora wrap-around (když end_time < start_time -> session přechází přes půlnoc).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now = _ensure_aware_dt(now)
    ny_tz = pytz.timezone(ny_tz_name)
    now_ny = now.astimezone(ny_tz)
    tnow = now_ny.time()

    if start_time <= end_time:
        return (start_time <= tnow) and (tnow < end_time)
    else:
        # session wraps midnight e.g. 22:00 -> 06:00
        return (tnow >= start_time) or (tnow < end_time)

def time_until_session(now: Optional[datetime], start_time: time,
                       ny_tz_name: str = "America/New_York") -> int:
    """
    Vrátí počet sekund do dalšího začátku session definované start_time (v NY čase).
    now může být aware nebo naive (pokud naive, považuje se za UTC).
    Používá se pro uspání smyčky do další session.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now = _ensure_aware_dt(now)
    ny_tz = pytz.timezone(ny_tz_name)
    now_ny = now.astimezone(ny_tz)

    # dnešní start v NY tz
    today = now_ny.date()
    start_dt_ny = ny_tz.localize(datetime.combine(today, start_time))
    if start_dt_ny <= now_ny:
        # už po dnešním startu -> vezmeme zítřejší start
        next_start_ny = start_dt_ny + timedelta(days=1)
    else:
        next_start_ny = start_dt_ny

    delta = next_start_ny - now_ny
    seconds = int(delta.total_seconds())
    return max(0, seconds)

def get_local_now(tz_name: Optional[str] = None) -> datetime:
    """
    Vrátí aktuální čas jako aware datetime v zadaném timezone (default LOG_TZ).
    """
    tz_name = tz_name or LOG_TZ
    tz = pytz.timezone(tz_name)
    return datetime.now(tz)

# small convenience for compatibility if code expects string parse/result
def parse_env_time_safe(t: str) -> time:
    try:
        return parse_env_time(t)
    except Exception:
        # fallback to midnight if invalid
        return time(0, 0)
