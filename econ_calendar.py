# analysis/econ_calendar.py
import re
import json
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup
from logger import log_debug, log_error, log_info
from config import HTTP_USER_AGENT, HTTP_RETRIES, HTTP_TIMEOUT, USE_SELENIUM_FOR_FF

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": HTTP_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
})

def _http_get(url: str, params: dict = None, retries: int = None) -> Optional[str]:
    r = None
    errors = []
    rr = retries if retries is not None else HTTP_RETRIES
    for attempt in range(1, rr+1):
        try:
            log_debug(f"HTTP GET attempt {attempt} -> {url}")
            resp = SESSION.get(url, params=params, timeout=HTTP_TIMEOUT)
            log_debug(f"HTTP {url} -> status {resp.status_code}")
            if resp.status_code == 200:
                return resp.text
            else:
                errors.append((resp.status_code, resp.text[:200]))
                # small backoff
                time.sleep(0.5 * attempt)
        except Exception as e:
            errors.append(str(e))
            time.sleep(0.5 * attempt)
    log_debug(f"HTTP GET errors for {url}: {errors}")
    return None

def _parse_investing_html(html: str, days: int) -> List[Dict]:
    events = []
    try:
        soup = BeautifulSoup(html, "lxml")
        # Investing may render events via JS; try to locate JSON blob or table rows
        # First look for script with "initialState" or similar JSON
        scripts = soup.find_all("script")
        json_candidates = []
        for s in scripts:
            t = (s.string or "")
            if "event" in t and ("calendar" in t or "economic" in t or "initialState" in t):
                json_candidates.append(t)
        # Try to extract JSON structures via regex
        for t in json_candidates:
            m = re.search(r"(\{[\s\S]{50,}\})", t)
            if m:
                try:
                    parsed = json.loads(m.group(1))
                    # tough to generalize; try to find keys that look like events
                    def walk(d):
                        if isinstance(d, dict):
                            for k,v in d.items():
                                if isinstance(v, dict):
                                    yield from walk(v)
                                elif isinstance(v, list):
                                    for item in v:
                                        yield from walk(item)
                        elif isinstance(d, list):
                            for item in d:
                                yield from walk(item)
                    for candidate in walk(parsed):
                        if isinstance(candidate, dict) and 'time' in candidate and ('currency' in candidate or 'country' in candidate):
                            # normalize
                            ev = {
                                "datetime_utc": None,
                                "currency": candidate.get("currency") or candidate.get("country"),
                                "impact": candidate.get("impact") or candidate.get("importance") or candidate.get("priority"),
                                "event": candidate.get("event") or candidate.get("title") or candidate.get("name"),
                                "actual": candidate.get("actual"),
                                "forecast": candidate.get("forecast"),
                                "previous": candidate.get("previous"),
                                "source": "investing"
                            }
                            # parse time if present
                            try:
                                tval = candidate.get("time") or candidate.get("date")
                                if tval:
                                    # investing sometimes uses epoch ms
                                    if isinstance(tval, (int, float)):
                                        ev["datetime_utc"] = datetime.fromtimestamp(tval / 1000.0, tz=timezone.utc)
                                    else:
                                        # try parse common formats
                                        try:
                                            dt = datetime.fromisoformat(tval)
                                            ev["datetime_utc"] = dt.astimezone(timezone.utc)
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                            events.append(ev)
                except Exception:
                    pass
        # fallback: parse visible table rows
        rows = soup.find_all("tr")
        for r in rows:
            try:
                # some rows may have attributes data-event-datetime etc.
                if r.get('data-event-datetime'):
                    dt_ts = int(r['data-event-datetime'])
                    dt = datetime.fromtimestamp(dt_ts, tz=timezone.utc)
                    currency = r.get('data-event-iso') or (r.find("td", {"class": re.compile("currency")}) and r.find("td", {"class": re.compile("currency")}).get_text(strip=True))
                    title_td = r.find("td", {"class": re.compile("event")})
                    ev_title = title_td.get_text(strip=True) if title_td else None
                    impact_td = r.find("td", {"class": re.compile("impact")})
                    impact = impact_td.get_text(strip=True) if impact_td else None
                    forecast = None
                    actual = None
                    # try to find forecast/actual in columns
                    tds = r.find_all("td")
                    if len(tds) >= 6:
                        actual = tds[-2].get_text(strip=True)
                        forecast = tds[-1].get_text(strip=True)
                    events.append({
                        "datetime_utc": dt,
                        "currency": currency,
                        "impact": impact,
                        "event": ev_title,
                        "actual": actual,
                        "forecast": forecast,
                        "previous": None,
                        "source": "investing"
                    })
            except Exception:
                pass
    except Exception as e:
        log_debug(f"_parse_investing_html error: {e}")
    return events

def fetch_investing_events(days: int = 2) -> List[Dict]:
    """
    Attempt to fetch events from Investing.com. Return list of event dicts.
    """
    url = "https://www.investing.com/economic-calendar/"
    html = _http_get(url)
    if not html:
        log_debug("fetch_investing_events: no html returned.")
        return []
    events = _parse_investing_html(html, days)
    log_debug(f"fetch_investing_events: found {len(events)} events")
    return events

def _parse_forexfactory_html(html: str, day: datetime) -> List[Dict]:
    events = []
    try:
        soup = BeautifulSoup(html, "lxml")
        # ForexFactory uses table rows with class "calendar__row" or "calendar_row"
        rows = soup.find_all("tr")
        for r in rows:
            try:
                # look for time cell
                tcell = r.find("td", {"class": re.compile("time", re.I)}) or r.find("td", {"class": "calendar__cell--time"})
                if not tcell:
                    continue
                timestr = tcell.get_text(strip=True)
                # combine with day
                # find currency cell
                curcell = r.find("td", {"class": re.compile("country", re.I)}) or r.find("td", {"class": re.compile("calendar__cell--country")})
                currency = curcell.get_text(strip=True) if curcell else None
                titlecell = r.find("td", {"class": re.compile("event", re.I)}) or r.find("td", {"class": re.compile("calendar__cell--event")})
                title = titlecell.get_text(strip=True) if titlecell else None
                impactcell = r.find("td", {"class": re.compile("impact", re.I)}) or r.find("td", {"class": re.compile("calendar__cell--impact")})
                impact = None
                if impactcell:
                    # some sites use icons / classes for impact
                    img = impactcell.find("span")
                    if img:
                        impact = img.get("title") or img.get_text(strip=True)
                    else:
                        impact = impactcell.get_text(strip=True)
                # parse time like "08:30" or "all day"
                dt = None
                if timestr and ":" in timestr:
                    try:
                        hh, mm = timestr.split(":")
                        dt = datetime(day.year, day.month, day.day, int(hh), int(mm), tzinfo=timezone.utc)
                    except Exception:
                        dt = None
                events.append({
                    "datetime_utc": dt,
                    "currency": currency,
                    "impact": impact,
                    "event": title,
                    "actual": None,
                    "forecast": None,
                    "previous": None,
                    "source": "forexfactory"
                })
            except Exception:
                continue
    except Exception as e:
        log_debug(f"_parse_forexfactory_html error: {e}")
    return events

def fetch_forexfactory_for_day(day: datetime) -> List[Dict]:
    """
    Try to fetch ForexFactory calendar for a single day.
    """
    url = "https://www.forexfactory.com/calendar.php"
    params = {"day": day.strftime("%Y-%m-%d")}
    html = _http_get(url, params=params)
    if html is None:
        # if 403 or blocked, consider headless fallback (not implemented here automatically)
        log_debug(f"ForexFactory HTTP fetch failed for {params.get('day')}")
        return []
    events = _parse_forexfactory_html(html, day)
    log_debug(f"fetch_forexfactory_for_day: got {len(events)} events for {day.date()}")
    return events

def get_all_upcoming(days: int = 2) -> List[Dict]:
    """
    Return all events (combined from Investing.com and ForexFactory) for next `days` days.
    """
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    out = []
    try:
        inv = fetch_investing_events(days)
        if inv:
            out.extend(inv)
        # try forexfactory per-day; skip if blocked
        for d in range(days):
            day = (now + timedelta(days=d))
            ff = fetch_forexfactory_for_day(day)
            if ff:
                out.extend(ff)
    except Exception as e:
        log_error(f"get_all_upcoming error: {e}")
    # normalize: ensure datetime_utc exists and is real datetime or None
    normalized = []
    for e in out:
        try:
            dt = e.get("datetime_utc")
            if isinstance(dt, str):
                try:
                    dt = datetime.fromisoformat(dt).astimezone(timezone.utc)
                except Exception:
                    dt = None
            e["datetime_utc"] = dt
            # normalize impact to lowercase (none, low, medium, high)
            imp = (e.get("impact") or "").strip().lower() if e.get("impact") else ""
            if imp in ("high", "high impact", "red", "h"):
                e["impact_level"] = "high"
            elif imp in ("medium", "medium impact", "orange", "m"):
                e["impact_level"] = "medium"
            elif imp in ("low", "low impact", "yellow", "l"):
                e["impact_level"] = "low"
            else:
                e["impact_level"] = "unknown"
            normalized.append(e)
        except Exception:
            continue
    log_debug(f"get_all_upcoming -> {len(normalized)} events; returning list")
    return normalized

def get_high_impact_upcoming(days: int = 2) -> List[Dict]:
    """
    Filter results from get_all_upcoming to return only high-impact events.
    """
    all_ev = get_all_upcoming(days)
    high = [e for e in all_ev if e.get("impact_level") == "high"]
    log_debug(f"get_high_impact_upcoming -> {len(high)} events")
    return high

def format_events_for_discord(events: List[Dict]) -> str:
    if not events:
        return "No events."
    lines = []
    for e in sorted(events, key=lambda x: (x.get("datetime_utc") or datetime.max)):
        dt = e.get("datetime_utc")
        dt_s = dt.isoformat() if dt else "TBD"
        cur = e.get("currency") or ""
        impact = e.get("impact_level") or e.get("impact") or ""
        title = e.get("event") or ""
        forecast = e.get("forecast") or ""
        lines.append(f"{dt_s} | {cur} | impact={impact} | {title} | f:{forecast}")
    return "\n".join(lines)

if __name__ == "__main__":
    # quick test
    try:
        ev = get_all_upcoming(3)
        print("Events:", len(ev))
        for e in ev[:10]:
            print(e)
    except Exception as e:
        print("Error:", e)
