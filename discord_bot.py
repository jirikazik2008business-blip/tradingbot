# discord_bot.py
import os
import sys
import asyncio
import subprocess
import time
import json
import zipfile
import shutil
import re
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv

import discord
from discord.ext import commands, tasks

# project imports (these are expected to exist in the project)
from logger import log_debug, log_info, log_error
from notifications import set_sender
from config import DISCORD_TOKEN, DISCORD_CHANNEL_ID
import MetaTrader5 as mt5
import metrics
import notifications
import config
import importlib

# optional helpers (may or may not exist)
try:
    from analysis import econ_calendar
except Exception:
    econ_calendar = None
try:
    from strategy import bias as strategy_bias
except Exception:
    strategy_bias = None
try:
    import comments as comments_mod
except Exception:
    comments_mod = None

# import zones module and image generator
try:
    from analysis import zones as zones_mod
except Exception:
    zones_mod = None

try:
    import image_generator
except Exception:
    image_generator = None

# optional system library
try:
    import psutil
except Exception:
    psutil = None

# load environment
load_dotenv()
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ----------------------
# Paths, config and logging setup
# ----------------------
LOG_DIR = getattr(config, "LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
# Primary bot log file (kept as rotating file by python logging)
LOG_PATH = os.path.join(LOG_DIR, "discord_bot.log")

# Setup python logger for this module (alongside project logger functions)
py_logger = logging.getLogger("discord_bot")
if not py_logger.handlers:
    py_logger.setLevel(logging.DEBUG)
    # Timed rotating handler: rotate at midnight, keep 14 backups
    try:
        trh = TimedRotatingFileHandler(LOG_PATH, when="midnight", backupCount=14, encoding="utf-8")
        trh.suffix = "%Y-%m-%d"
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
        trh.setFormatter(formatter)
        py_logger.addHandler(trh)
    except Exception:
        # fallback basic config
        logging.basicConfig(filename=LOG_PATH, level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")

# also log to stdout for terminal visibility
if not any(isinstance(h, logging.StreamHandler) for h in py_logger.handlers):
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
    py_logger.addHandler(sh)

_message_queue: asyncio.Queue = asyncio.Queue()

# ----------------------
# MT5 / health config
# ----------------------
MT5_PATH = os.getenv("MT5_PATH", getattr(config, "MT5_PATH", ""))
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", str(getattr(config, "BOT_OWNER_ID", "0")) or "0"))
try:
    MT5_HEALTH_INTERVAL_S = int(os.getenv("MT5_HEALTH_INTERVAL_S", "30"))
except Exception:
    MT5_HEALTH_INTERVAL_S = 30

# alerts storage
_ALERTS_FILE = os.path.join(LOG_DIR, "alerts.json")
_alerts_lock = asyncio.Lock()
_alerts_cache = {}  # id -> alert dict
try:
    _ALERTS_CHECK_INTERVAL = int(os.getenv("ALERTS_CHECK_INTERVAL_S", "10"))
except Exception:
    _ALERTS_CHECK_INTERVAL = 10

# health state
_mt5_connected = False
_last_mt5_state = None

# ----------------------
# Utility / logging helpers
# ----------------------
def enqueue_message(content: str):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(_message_queue.put_nowait, content)
    except RuntimeError:
        pass

def _log_file(message: str, level: str = "INFO"):
    """
    Writes to the configured python logger (which rotates nightly) and also
    writes a simple UTC timestamped line to LOG_PATH for backward compatibility.
    """
    try:
        # send to python logger
        if level.upper() == "DEBUG":
            py_logger.debug(message)
        elif level.upper() == "ERROR":
            py_logger.error(message)
        elif level.upper() == "WARNING":
            py_logger.warning(message)
        else:
            py_logger.info(message)
    except Exception:
        pass

    # write a simple UTC line for compatibility with _latest_log_file and !log reading
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.utcnow().isoformat()} {level.upper()} {message}\n")
    except Exception:
        pass

# mirror project logger usage to python logger as well
def _log_debug(msg: str):
    try:
        log_debug(msg)
    except Exception:
        pass
    _log_file(msg, level="DEBUG")

def _log_info(msg: str):
    try:
        log_info(msg)
    except Exception:
        pass
    _log_file(msg, level="INFO")

def _log_error(msg: str):
    try:
        log_error(msg)
    except Exception:
        pass
    _log_file(msg, level="ERROR")

# ----------------------
# Startup / background tasks
# ----------------------
@bot.event
async def on_ready():
    _log_info(f"{bot.user} connected to Discord")
    print(f"{bot.user} connected to Discord")
    # wire notifications sender
    set_sender(lambda payload: enqueue_message(payload if isinstance(payload, str) else str(payload)))
    sender_loop.start()
    alerts_check_loop.start()
    # start MT5 health tasks
    asyncio.create_task(_ensure_mt5_connected_once_and_notify())
    asyncio.create_task(_mt5_health_checker_loop())
    await _load_alerts_into_memory()

@tasks.loop(seconds=2)
async def sender_loop():
    try:
        ch = None
        try:
            cid = os.getenv("DISCORD_CHANNEL_ID", DISCORD_CHANNEL_ID)
            cid_int = int(cid) if cid is not None else None
            if cid_int:
                ch = bot.get_channel(cid_int)
        except Exception:
            ch = None
        if ch is None:
            # nothing to send to if channel missing
            return
        for _ in range(5):
            if _message_queue.empty():
                break
            msg = await _message_queue.get()
            try:
                await ch.send(content=msg)
            except Exception as e:
                _log_debug(f"Discord send failed in sender_loop: {e}")
    except Exception as e:
        _log_debug(f"sender_loop error: {e}")

# ----------------------
# Alerts persistence & loop
# ----------------------
async def _load_alerts_into_memory():
    global _alerts_cache
    try:
        if not os.path.exists(_ALERTS_FILE):
            _alerts_cache = {}
            return
        async with _alerts_lock:
            with open(_ALERTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            # ensure keys are ints
            _alerts_cache = {int(k): v for k, v in data.items()}
            _log_debug(f"Loaded {_alerts_cache.__len__()} alerts")
    except Exception as e:
        _log_error(f"Failed to load alerts: {e}")
        _alerts_cache = {}

async def _save_alerts_from_memory():
    try:
        async with _alerts_lock:
            with open(_ALERTS_FILE, "w", encoding="utf-8") as f:
                json.dump(_alerts_cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _log_error(f"Failed to save alerts: {e}")

def _next_alert_id() -> int:
    return max(_alerts_cache.keys(), default=0) + 1

def _normalize_pair(p: str) -> str:
    return p.strip().upper()

def _parse_price(s: str):
    try:
        return float(s)
    except Exception:
        try:
            return float(s.replace(",", "."))
        except Exception:
            return None

@tasks.loop(seconds=_ALERTS_CHECK_INTERVAL)
async def alerts_check_loop():
    try:
        if not _alerts_cache:
            return
        symbols = list({_normalize_pair(a["pair"]) for a in _alerts_cache.values()})
        ticks = {}
        for sym in symbols:
            try:
                t = mt5.symbol_info_tick(sym)
                if t:
                    ticks[sym] = {"bid": float(t.bid), "ask": float(t.ask)}
                    continue
                ticks[sym] = {}
            except Exception as e:
                _log_debug(f"alerts_check_loop mt5 tick error for {sym}: {e}")
                ticks[sym] = {}

        triggered = []
        for aid, a in list(_alerts_cache.items()):
            sym = _normalize_pair(a.get("pair", ""))
            try:
                price = float(a.get("price"))
            except Exception:
                continue
            side = a.get("side", "above")
            tick = ticks.get(sym, {})
            cur_price = tick.get("bid") or tick.get("ask") or None
            if cur_price is None:
                continue
            hit = False
            if side == "above" and cur_price >= price:
                hit = True
            if side == "below" and cur_price <= price:
                hit = True
            if hit:
                msg = f"ALERT TRIGGERED | {sym} | target={price} | current={cur_price:.5f} | id={aid}"
                enqueue_message(msg)
                triggered.append(aid)

        if triggered:
            for aid in triggered:
                _alerts_cache.pop(aid, None)
            await _save_alerts_from_memory()
    except Exception as e:
        _log_debug(f"alerts_check_loop error: {e}")

# ----------------------
# MT5 helpers / health
# ----------------------
def _start_mt5_terminal():
    if not MT5_PATH:
        _log_debug("MT5_PATH not configured, will not auto-start terminal.")
        return False
    try:
        subprocess.Popen([MT5_PATH], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _log_info(f"Attempted to start MT5 terminal from {MT5_PATH}")
        return True
    except Exception as e:
        _log_error(f"Failed to start MT5 terminal: {e}")
        return False

def _attempt_mt5_init_and_login():
    try:
        ok = mt5.initialize()
        _log_debug(f"mt5.initialize() -> {ok}")
        if not ok:
            try:
                le = mt5.last_error()
                _log_debug(f"mt5.last_error() -> {le}")
            except Exception:
                pass
            return False
        # optional login if credentials present
        login = getattr(config, "MT5_LOGIN", None)
        if login:
            try:
                pwd = getattr(config, "MT5_PASSWORD", "")
                server = getattr(config, "MT5_SERVER", "")
                logged = mt5.login(login, password=pwd, server=server)
                _log_debug(f"mt5.login() -> {logged}")
            except Exception as e:
                _log_debug(f"mt5.login() exception: {e}")
        return True
    except Exception as e:
        _log_debug(f"mt5.initialize() raised: {e}")
        return False

async def _ensure_mt5_connected_once_and_notify():
    global _mt5_connected, _last_mt5_state
    connected = False
    if _attempt_mt5_init_and_login():
        try:
            acc = mt5.account_info()
            if acc:
                connected = True
        except Exception:
            connected = False

    if not connected:
        started = _start_mt5_terminal()
        if started:
            await asyncio.sleep(6)
        for i in range(4):
            if _attempt_mt5_init_and_login():
                try:
                    acc = mt5.account_info()
                    if acc:
                        connected = True
                        break
                except Exception:
                    connected = False
            await asyncio.sleep(3)

    _mt5_connected = connected
    _notify_mt5_state_change(connected)
    _last_mt5_state = connected

def _notify_mt5_state_change(state: bool):
    msg = f"MT5 connected: {state}"
    _log_info(msg)
    try:
        notifications.notify_raw(msg)
    except Exception:
        enqueue_message(msg)

async def _mt5_health_checker_loop():
    global _mt5_connected, _last_mt5_state
    interval = MT5_HEALTH_INTERVAL_S
    while True:
        try:
            ok = False
            try:
                acc = mt5.account_info()
                if acc:
                    ok = True
            except Exception:
                ok = False

            if not ok:
                connected = False
                if _attempt_mt5_init_and_login():
                    try:
                        acc = mt5.account_info()
                        if acc:
                            connected = True
                    except Exception:
                        connected = False
                else:
                    _start_mt5_terminal()
                    await asyncio.sleep(6)
                    if _attempt_mt5_init_and_login():
                        try:
                            acc = mt5.account_info()
                            if acc:
                                connected = True
                        except Exception:
                            connected = False

                _mt5_connected = connected
            else:
                _mt5_connected = True

            if _last_mt5_state is None or _mt5_connected != _last_mt5_state:
                _notify_mt5_state_change(_mt5_connected)
                _last_mt5_state = _mt5_connected

        except Exception as e:
            _log_debug(f"mt5 health loop error: {e}")

        await asyncio.sleep(interval)

# ----------------------
# Helpers
# ----------------------
def _safe_account_info():
    try:
        return mt5.account_info()
    except Exception:
        return None

def _is_admin(ctx):
    try:
        if BOT_OWNER_ID and ctx.author.id == BOT_OWNER_ID:
            return True
        return ctx.author.guild_permissions.administrator
    except Exception:
        return False

def _latest_log_file():
    """
    Returns the most recent .log file in LOG_DIR.
    """
    try:
        files = [os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR) if f.lower().endswith(".log")]
        if not files:
            return None
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return files[0]
    except Exception as e:
        _log_debug(f"_latest_log_file error: {e}")
        return None

def _find_log_by_date_str(date_str: str):
    """
    Accepts formats:
      - YYYY-MM-DD
      - YYYYMMDD
      - any substring to match filenames
    Returns first matching path or None.
    """
    date_str = date_str.strip()
    # try YYYY-MM-DD
    try_patterns = [date_str, date_str.replace("-", ""), date_str.replace("-", "_")]
    files = [f for f in os.listdir(LOG_DIR) if f.lower().endswith(".log")]
    for p in try_patterns:
        for f in files:
            if p in f:
                return os.path.join(LOG_DIR, f)
    # fallback: substring match
    for f in files:
        if date_str in f:
            return os.path.join(LOG_DIR, f)
    return None

def _find_log_by_name_or_substring(name: str):
    """
    If name ends with .log and exists -> return that path.
    Else try substring matching in LOG_DIR.
    """
    candidate = name.strip()
    if candidate.endswith(".log"):
        path = os.path.join(LOG_DIR, candidate)
        if os.path.exists(path):
            return path
    # substring match
    files = [f for f in os.listdir(LOG_DIR) if f.lower().endswith(".log")]
    for f in files:
        if candidate in f:
            return os.path.join(LOG_DIR, f)
    return None

def _chunk_text(text: str, size: int = 1900):
    for i in range(0, len(text), size):
        yield text[i:i+size]

# ----------------------
# Discord commands
# ----------------------
@bot.command(name='stats')
async def stats_command(ctx, symbol: str = None):
    try:
        gen = getattr(image_generator, "generate_stats_image", None)
    except Exception:
        gen = None

    zones = None
    if symbol:
        if strategy_bias and hasattr(strategy_bias, "compute_key_levels"):
            try:
                zones = strategy_bias.compute_key_levels(symbol, tf_high=getattr(config, "ALIGN_TF_HIGH", "H4"),
                                                        tf_mid=getattr(config, "ALIGN_TF_MID", "H1"))
            except Exception as e:
                _log_debug(f"strategy_bias.compute_key_levels failed in stats: {e}")
                zones = None
        if not zones and zones_mod and hasattr(zones_mod, "compute_zones_for_symbol"):
            try:
                zones = zones_mod.compute_zones_for_symbol(symbol, tfs=[getattr(config, "ALIGN_TF_HIGH","H4"), getattr(config, "ALIGN_TF_MID","H1")], lookback_bars=400, keep_top=12)
            except Exception as e:
                _log_debug(f"zones_mod.compute_zones_for_symbol failed: {e}")
                zones = None

    try:
        img_path = None
        if gen:
            try:
                img_path = gen(output_path=getattr(config, "OUTPUT_IMAGE", None))
            except TypeError:
                try:
                    img_path = gen()
                except Exception as ee:
                    _log_debug(f"generate_stats_image fallback call failed: {ee}")
                    img_path = None
            except Exception as e:
                _log_debug(f"generate_stats_image exception: {e}")
                img_path = None
    except Exception as e:
        _log_debug(f"generate_stats_image top-level exception: {e}")
        img_path = None

    zones_img = None
    if symbol and image_generator and hasattr(image_generator, "generate_zones_image"):
        try:
            if not zones and zones_mod and hasattr(zones_mod, "compute_zones_for_symbol"):
                try:
                    zones = zones_mod.compute_zones_for_symbol(symbol, tfs=[getattr(config,"ALIGN_TF_HIGH","H4"), getattr(config,"ALIGN_TF_MID","H1")], lookback_bars=400, keep_top=12)
                except Exception as e:
                    _log_debug(f"fallback compute_zones_for_symbol failed: {e}")
            try:
                zones_img = image_generator.generate_zones_image(symbol=symbol, tf=getattr(config, "ALIGN_TF_MID", "H1"), lookback_bars=300, zones=zones)
            except TypeError:
                try:
                    zones_img = image_generator.generate_zones_image(symbol, getattr(config, "ALIGN_TF_MID", "H1"), 300, zones)
                except Exception as e:
                    _log_debug(f"generate_zones_image fallback signature failed: {e}")
                    zones_img = None
        except Exception as e:
            _log_debug(f"generate_zones_image failed: {e}")
            zones_img = None

    try:
        if zones_img and os.path.exists(zones_img):
            await ctx.send(file=discord.File(zones_img))
            _log_file(f"!stats: zones image {zones_img} sent")
            return
    except Exception as e:
        _log_debug(f"sending zones image failed: {e}")

    try:
        if img_path and os.path.exists(img_path):
            await ctx.send(file=discord.File(img_path))
            _log_file(f"!stats: image {img_path} sent")
            return
    except Exception as e:
        _log_debug(f"sending stats image failed: {e}")

    try:
        report = _build_performance_text()
        await ctx.send(f"```\n{report}\n```")
        _log_file("!stats: fallback text sent (image unavailable)")
    except Exception as e:
        _log_debug(f"fallback send failed: {e}")
        await ctx.send("Statistics unavailable. Zkus později nebo zkontroluj logy.")

@bot.command(name='balance')
async def balance_command(ctx):
    try:
        acc = _safe_account_info()
        if acc:
            bal = float(getattr(acc, "balance", 0.0))
            eq = float(getattr(acc, "equity", 0.0))
            margin = float(getattr(acc, "margin", 0.0)) if hasattr(acc, "margin") else 0.0
            free = float(getattr(acc, "margin_free", 0.0)) if hasattr(acc, "margin_free") else 0.0
            msg = f"Balance: ${bal:.2f}\nEquity: ${eq:.2f}\nMargin: ${margin:.2f}\nFree margin: ${free:.2f}"
        else:
            msg = "Account info unavailable (MT5 not connected)."
        await ctx.send(msg)
    except Exception as e:
        _log_error(f"!balance command failed: {e}")
        await ctx.send("Chyba při získávání balance.")

def _build_performance_text():
    try:
        s = metrics.update_and_report_from_mt5(None)
        trades, pnl_usd, pnl_czk, pct = metrics.month_stats()
        text = (f"Performance (month):\n"
                f"Trades closed: {trades}\n"
                f"PnL USD: ${pnl_usd:.2f}\n"
                f"PnL CZK: {pnl_czk:.0f} CZK\n"
                f"Percent: {s.get('pnl_display', '')}\n")
        try:
            import pandas as pd
            from config import JOURNAL_CSV
            if os.path.exists(JOURNAL_CSV):
                df = pd.read_csv(JOURNAL_CSV, on_bad_lines='skip')
                if 'status' in df.columns and 'pnl' in df.columns:
                    closed = df[df['status'] == 'closed']
                    if not closed.empty:
                        wins = (closed['pnl'] > 0).sum()
                        total = closed.shape[0]
                        wr = wins / total * 100.0
                        avg = closed['pnl'].mean()
                        text += f"Winrate: {wr:.1f}% ({wins}/{total})\nAvg PnL per closed: {avg:.2f}\n"
        except Exception:
            pass
        return text
    except Exception as e:
        _log_debug(f"_build_performance_text failed: {e}")
        return "Performance unavailable."

@bot.command(name='performance')
async def performance_command(ctx):
    try:
        text = _build_performance_text()
        await ctx.send(f"```\n{text}\n```")
    except Exception as e:
        _log_error(f"!performance failed: {e}")
        await ctx.send("Chyba při sestavování performance.")

@bot.command(name='status')
async def status_command(ctx):
    try:
        acc = _safe_account_info()
        connected = bool(acc)
        trade_enabled = bool(getattr(config, "TRADE_ENABLED", False))
        notify_only = bool(getattr(config, "NOTIFY_ONLY", False))
        symbols = getattr(config, "SYMBOLS", [])
        pos_count = 0
        try:
            pos = mt5.positions_get() or []
            pos_count = len(pos)
        except Exception:
            pos_count = 0
        msg = (f"MT5 connected: {connected}\n"
               f"Trading enabled: {trade_enabled}\n"
               f"Notify only: {notify_only}\n"
               f"Tracked symbols: {', '.join(symbols)}\n"
               f"Open positions: {pos_count}")
        await ctx.send(msg)
    except Exception as e:
        _log_error(f"!status failed: {e}")
        await ctx.send("Chyba při získávání statusu.")

@bot.command(name='tradingmode')
async def tradingmode_command(ctx, action: str = None):
    try:
        if action is None:
            te = getattr(config, "TRADE_ENABLED", True)
            no = getattr(config, "NOTIFY_ONLY", False)
            await ctx.send(f"TRADE_ENABLED={te} NOTIFY_ONLY={no}")
            return

        if action.lower() == "toggle":
            current = getattr(config, "NOTIFY_ONLY", False)
            config.NOTIFY_ONLY = not current
            await ctx.send(f"NOTIFY_ONLY set to {config.NOTIFY_ONLY}")
            return

        if action.lower() in ("live", "on", "true"):
            config.NOTIFY_ONLY = False
            config.TRADE_ENABLED = True
            await ctx.send("Trading mode set to LIVE (executing orders).")
            return
        if action.lower() in ("notify", "off", "false"):
            config.NOTIFY_ONLY = True
            config.TRADE_ENABLED = False
            await ctx.send("Trading mode set to NOTIFY ONLY (no execution).")
            return

        await ctx.send("Unsupported action. Use `toggle`, `live` or `notify`.")
    except Exception as e:
        _log_error(f"!tradingmode failed: {e}")
        await ctx.send("Chyba při nastavování trading módu.")

@bot.command(name='calendar')
async def calendar_command(ctx, days: int = 2):
    try:
        if econ_calendar is None:
            await ctx.send("Economic calendar not available.")
            return
        events = econ_calendar.get_high_impact_upcoming(days=days)
        if not events:
            await ctx.send("No high-impact events found in the next {} day(s).".format(days))
            return
        lines = []
        for e in events[:10]:
            dt = e.get("datetime_utc")
            dt_s = dt.isoformat() if dt else "TBD"
            cur = e.get("currency") or ""
            impact = e.get("impact") or ""
            title = e.get("event") or ""
            forecast = e.get("forecast", "")
            lines.append(f"{dt_s} | {cur} | {impact} | {title} | f:{forecast}")
        msg = "Upcoming high-impact events:\n" + "\n".join(lines)
        await ctx.send(f"```\n{msg}\n```")
    except Exception as e:
        _log_error(f"!calendar failed: {e}")
        await ctx.send("Chyba při načítání ekonomického kalendáře.")

@bot.command(name='zones')
async def zones_command(ctx, symbol: str = None, tf: str = None):
    try:
        if symbol is None:
            symbols = getattr(config, "SYMBOLS", [])
            symbol = symbols[0] if symbols else None
        if not symbol:
            await ctx.send("Specify a symbol or configure SYMBOLS in .env.")
            return

        symbol = symbol.strip().upper()
        tf_use = tf or getattr(config, "ALIGN_TF_MID", "H1")

        levels = []
        if strategy_bias and hasattr(strategy_bias, "compute_key_levels"):
            try:
                levels = strategy_bias.compute_key_levels(symbol, tf_high=getattr(config, "ALIGN_TF_HIGH", "H4"),
                                                         tf_mid=getattr(config, "ALIGN_TF_MID", "H1"))
            except Exception as e:
                _log_debug(f"strategy_bias.compute_key_levels failed: {e}")
                levels = []

        if not levels and zones_mod and hasattr(zones_mod, "compute_zones_for_symbol"):
            try:
                levels = zones_mod.compute_zones_for_symbol(symbol, tfs=[getattr(config,"ALIGN_TF_HIGH","D1"), tf_use], lookback_bars=400, keep_top=20)
            except Exception as e:
                _log_debug(f"zones_mod.compute_zones_for_symbol failed: {e}")
                levels = []

        if not levels:
            try:
                from strategy import build_plan
                plan = build_plan(symbol)
                if plan:
                    levels = [plan.entry_price, plan.sl, plan.tp]
            except Exception as e:
                _log_debug(f"Fallback build_plan failed: {e}")

        if not levels:
            await ctx.send(f"No levels found for {symbol}.")
            return

        if image_generator and hasattr(image_generator, "generate_zones_image"):
            try:
                img_path = None
                try:
                    img_path = image_generator.generate_zones_image(symbol=symbol, tf=tf_use, lookback_bars=400, zones=levels)
                except TypeError:
                    try:
                        img_path = image_generator.generate_zones_image(symbol, tf_use, 400, levels)
                    except Exception as e:
                        _log_debug(f"image_generator.generate_zones_image fallback failed: {e}")
                        img_path = None

                if img_path and os.path.exists(img_path):
                    await ctx.send(file=discord.File(img_path))
                    _log_file(f"!zones: image {img_path} sent for {symbol}")
                    return
            except Exception as e:
                _log_debug(f"image_generator.generate_zones_image failed: {e}")

        lines = [f"{symbol} levels:"]
        for i, lv in enumerate(sorted(set(levels), reverse=True)):
            try:
                lines.append(f"{i+1}. {float(lv):.5f}")
            except Exception:
                lines.append(f"{i+1}. {lv}")
        await ctx.send("```\n" + "\n".join(lines) + "\n```")
    except Exception as e:
        _log_error(f"!zones failed: {e}")
        await ctx.send("Chyba při generování zón.")

@bot.command(name='comment')
async def comment_command(ctx):
    try:
        comment = ""
        if comments_mod and hasattr(comments_mod, "load_motivation"):
            try:
                comment = comments_mod.load_motivation()
            except Exception:
                comment = ""
        if not comment and comments_mod and hasattr(comments_mod, "load_comment"):
            comment = comments_mod.load_comment("long") or ""
        if not comment:
            comment = "Komentáře nejsou dostupné."
        await ctx.send(comment)
    except Exception as e:
        _log_error(f"!comment failed: {e}")
        await ctx.send("Chyba při načítání komentáře.")

@bot.command(name='myid')
async def myid_command(ctx):
    await ctx.send(f"Your ID: {ctx.author.id}")

@bot.command(name='log')
async def log_command(ctx, lines: str = "50", logfile: str = None):
    """
    Flexible log viewer:
      !log [lines_or_filter] [logfile]
    logfile can be:
      - omitted: uses the latest .log file (default)
      - 'discord' or 'bot': uses the main discord_bot.log
      - 'daily' or a date 'YYYY-MM-DD' or 'YYYYMMDD' to match a daily rotated file
      - filename or substring to match a file in LOG_DIR
    Examples:
      !log 100
      !log error 2025-11-11
      !log 200 discord
      !log today discord_bot.log
    """
    try:
        if not _is_admin(ctx):
            await ctx.send("Permission denied.")
            return

        # determine which file to open
        target_file = None
        if logfile:
            lf = logfile.strip().lower()
            if lf in ("discord", "bot", "discord_bot.log"):
                target_file = LOG_PATH
            elif re.match(r'^\d{4}-\d{2}-\d{2}$', lf) or re.match(r'^\d{8}$', lf):
                found = _find_log_by_date_str(lf)
                if found:
                    target_file = found
            else:
                # try direct name or substring match
                found = _find_log_by_name_or_substring(logfile)
                if found:
                    target_file = found

            # fallback: if not found but logfile looks like a path
            if not target_file and os.path.exists(logfile):
                target_file = logfile

        if not target_file:
            target_file = _latest_log_file()
        if not target_file:
            await ctx.send("No log files found.")
            return

        try:
            with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.read().splitlines()
        except Exception as e:
            await ctx.send(f"Failed to read log {target_file}: {e}")
            return

        # try to treat lines as integer first
        N = None
        filter_str = None
        try:
            N = int(float(lines))
        except Exception:
            # not a plain number -> treat as filter string
            filter_str = str(lines).strip()
            N = 200  # default cap when filtering

        if filter_str:
            # return last N lines containing filter_str (case-insensitive)
            matched = [ln for ln in all_lines if filter_str.lower() in ln.lower()]
            tail = matched[-max(1, min(N, len(matched))):] if matched else []
        else:
            tail = all_lines[-max(1, int(N)):]

        content = "\n".join(tail) if tail else "(no matching lines)"
        if len(content) > 1900:
            content = "...(truncated)\n" + content[-1800:]
        # Include which file we read from for clarity
        header = f"Log file: {os.path.basename(target_file)}\n"
        await ctx.send(f"```\n{header}{content}\n```")
    except Exception as e:
        _log_error(f"!log failed: {e}")
        await ctx.send("Chyba při čtení logu.")

@bot.command(name='restart')
async def restart_command(ctx):
    try:
        if not _is_admin(ctx):
            await ctx.send("Permission denied.")
            return
        await ctx.send("Restarting bot now...")
        _log_info(f"Restart requested by {ctx.author} ({ctx.author.id}) via Discord.")
        await asyncio.sleep(1)
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            _log_error(f"Restart exec failed: {e}")
            await ctx.send(f"Restart failed: {e}")
    except Exception as e:
        _log_error(f"!restart failed: {e}")
        await ctx.send("Chyba při restartu bota.")

# ----------------------
# Alerts commands (group)
# ----------------------
@bot.group(name="alerts", invoke_without_command=True)
async def alerts_group(ctx):
    await ctx.send("Použij `!alerts add [price] [pair] [above|below]`, `!alerts list`, `!alerts remove [id]`")

@alerts_group.command(name="add")
async def alerts_add(ctx, price: str = None, pair: str = None, side: str = "above"):
    if price is None or pair is None:
        await ctx.send("Použij: `!alerts add [price] [pair] [above|below]`")
        return
    p = _parse_price(price)
    if p is None:
        await ctx.send("Neplatná cena.")
        return
    pair_n = _normalize_pair(pair)
    side_n = "above" if str(side).lower() not in ("below", "b") else "below"
    aid = _next_alert_id()
    alert = {"id": aid, "price": p, "pair": pair_n, "side": side_n, "created_at": datetime.utcnow().isoformat()}
    _alerts_cache[aid] = alert
    await _save_alerts_from_memory()
    await ctx.send(f"Alert přidán id={aid} {pair_n} {side_n} {p}")

@alerts_group.command(name="list")
async def alerts_list(ctx):
    if not _alerts_cache:
        await ctx.send("Žádné alerty.")
        return
    lines = [f"{aid}: {a['pair']} {a['side']} {a['price']} (added {a.get('created_at')})" for aid, a in sorted(_alerts_cache.items())]
    for chunk in _chunk_text("\n".join(lines), 1900):
        await ctx.send(f"```{chunk}```")

@alerts_group.command(name="remove")
async def alerts_remove(ctx, aid: int = None):
    if aid is None:
        await ctx.send("Použij: `!alerts remove [id]`")
        return
    if aid not in _alerts_cache:
        await ctx.send("ID nenalezeno.")
        return
    _alerts_cache.pop(aid, None)
    await _save_alerts_from_memory()
    await ctx.send(f"Alert {aid} odstraněn.")

# ----------------------
# Trading-related / utility commands
# ----------------------
@bot.command(name='positions')
async def positions_command(ctx, symbol: str = None):
    """Zobrazí aktuální otevřené pozice: !positions [symbol]"""
    try:
        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not positions:
            await ctx.send("Žádné otevřené pozice.")
            return

        lines = []
        total_profit = 0.0
        for pos in positions:
            try:
                sym = getattr(pos, "symbol", "N/A")
                typ = getattr(pos, "type", "N/A")
                vol = getattr(pos, "volume", 0.0)
                profit = float(getattr(pos, "profit", 0.0) or 0.0)
                opened = getattr(pos, "time", None)
                if isinstance(opened, (int, float)):
                    opened_s = datetime.utcfromtimestamp(opened).isoformat()
                else:
                    opened_s = str(opened)
                total_profit += profit
                lines.append(f"{sym} | type={typ} | vol={vol} | profit=${profit:.2f} | opened={opened_s}")
            except Exception:
                continue

        summary = f"Celkový profit: ${total_profit:.2f}\nPočet pozic: {len(positions)}"
        for chunk in _chunk_text("\n".join(lines) + "\n\n" + summary, 1900):
            await ctx.send(f"```\n{chunk}\n```")
    except Exception as e:
        _log_error(f"!positions failed: {e}")
        await ctx.send("Chyba při načítání pozic.")

@bot.command(name='closeposition')
async def close_position_command(ctx, ticket: int = None):
    """Uzavře pozici podle ticket ID (admin)"""
    try:
        if not _is_admin(ctx):
            await ctx.send("Permission denied.")
            return

        if ticket is None:
            await ctx.send("Použij: `!closeposition [ticket]`")
            return

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            await ctx.send(f"Pozice {ticket} nenalezena.")
            return

        position = positions[0]
        # Implement safe close: create opposite market order to close full volume
        try:
            vol = float(getattr(position, "volume", 0.0))
            sym = position.symbol
            order_type = mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(sym)
            price = float(tick.bid) if order_type == mt5.ORDER_TYPE_SELL else float(tick.ask)
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym,
                "volume": vol,
                "type": order_type,
                "price": price,
                "deviation": 50,
                "magic": 424242,
                "comment": f"close_by_command {ticket}",
                "type_time": getattr(mt5, "ORDER_TIME_GTC", 0)
            }
            r = mt5.order_send(req)
            _log_debug(f"close_position order_send result: {r}")
            await ctx.send(f"Close requested for {ticket}. Result: {getattr(r, 'retcode', str(r))}")
        except Exception as e:
            _log_error(f"Error closing position {ticket}: {e}")
            await ctx.send(f"Chyba při uzavírání pozice: {e}")

    except Exception as e:
        _log_error(f"!closeposition failed: {e}")
        await ctx.send("Chyba při uzavírání pozice.")

@bot.command(name='quote')
async def quote_command(ctx, symbol: str):
    """Zobrazí aktuální ceny pro symbol: !quote EURUSD"""
    try:
        symbol = symbol.upper()
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            await ctx.send(f"Symbol {symbol} nenalezen.")
            return

        info = mt5.symbol_info(symbol)
        spread = None
        try:
            spread = (float(tick.ask) - float(tick.bid)) * (10000 if "JPY" not in symbol else 100)
        except Exception:
            spread = "N/A"

        last = getattr(tick, "last", "N/A")
        volume = getattr(tick, "volume", "N/A")
        msg = (
            f"{symbol}:\n"
            f"Bid: {getattr(tick,'bid','N/A')}\n"
            f"Ask: {getattr(tick,'ask','N/A')}\n"
            f"Spread: {spread} pips\n"
            f"Last: {last}\n"
            f"Volume: {volume}"
        )
        await ctx.send(f"```{msg}```")
    except Exception as e:
        _log_error(f"!quote failed: {e}")
        await ctx.send("Chyba při získávání quote.")

@bot.command(name='risk')
async def risk_command(ctx):
    """Zobrazí aktuální risk management stav"""
    try:
        acc = _safe_account_info()
        if not acc:
            await ctx.send("MT5 není připojen.")
            return

        balance = getattr(acc, "balance", 0.0)
        equity = getattr(acc, "equity", 0.0)
        margin = getattr(acc, "margin", 0.0)
        free_margin = getattr(acc, "margin_free", 0.0)
        margin_level = getattr(acc, "margin_level", 0.0)

        try:
            daily_pnl = metrics.get_daily_pnl()
        except Exception:
            daily_pnl = "N/A"

        msg = (
            f"Balance: ${float(balance):.2f}\n"
            f"Equity: ${float(equity):.2f}\n"
            f"Margin: ${float(margin):.2f}\n"
            f"Free Margin: ${float(free_margin):.2f}\n"
            f"Margin Level: {float(margin_level):.1f}%\n"
            f"Denní PnL: {daily_pnl}\n"
            f"Risk na trade: {getattr(config, 'RISK_PCT', 'N/A')}"
        )
        await ctx.send(f"```{msg}```")
    except Exception as e:
        _log_error(f"!risk failed: {e}")
        await ctx.send("Chyba při získávání risk info.")

@bot.command(name='backup')
async def backup_command(ctx):
    """Vytvoří backup konfigurace a alertů (admin only)"""
    try:
        if not _is_admin(ctx):
            await ctx.send("Permission denied.")
            return

        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(backup_dir, f"backup_{timestamp}.zip")

        files_to_backup = [
            ".env", "config.py", _ALERTS_FILE,
            getattr(config, "JOURNAL_CSV", "trading_journal.csv")
        ]
        with zipfile.ZipFile(backup_file, 'w') as zipf:
            for file in files_to_backup:
                if file and os.path.exists(file):
                    try:
                        zipf.write(file)
                    except Exception as e:
                        _log_debug(f"Failed to add {file} to backup: {e}")

        await ctx.send(f"Backup vytvořen: {backup_file}", file=discord.File(backup_file))
    except Exception as e:
        _log_error(f"!backup failed: {e}")
        await ctx.send("Chyba při vytváření backupu.")

@bot.command(name='export')
async def export_command(ctx, what: str = "alerts"):
    """Exportuje data (alerts, config)"""
    try:
        if what == "alerts":
            data = json.dumps(_alerts_cache, indent=2, ensure_ascii=False)
            for chunk in _chunk_text(data, 1900):
                await ctx.send(f"```json\n{chunk}\n```")
        elif what == "config":
            config_data = {k: v for k, v in vars(config).items() if not k.startswith('_')}
            await ctx.send("```python\n" + str(config_data) + "\n```")
        else:
            await ctx.send("Použij: `!export alerts` nebo `!export config`")
    except Exception as e:
        _log_error(f"!export failed: {e}")
        await ctx.send("Chyba při exportu.")

@bot.command(name='system')
async def system_command(ctx):
    """Detailní systémové informace"""
    try:
        if not psutil:
            await ctx.send("Nainstaluj `psutil` pro systémové informace.")
            return

        process = psutil.Process()
        memory_info = process.memory_info()
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        bot_start_time = datetime.fromtimestamp(process.create_time())
        bot_uptime = datetime.now() - bot_start_time

        msg = (
            f"Systém:\n"
            f"CPU: {psutil.cpu_percent()}%\n"
            f"RAM: {psutil.virtual_memory().percent}%\n"
            f"Disk: {psutil.disk_usage('.').percent}%\n"
            f"Uptime systému: {str(uptime).split('.')[0]}\n"
            f"Uptime bota: {str(bot_uptime).split('.')[0]}\n"
            f"Paměť bota: {memory_info.rss / 1024 / 1024:.1f} MB\n"
            f"Vlákna: {process.num_threads()}"
        )
        await ctx.send(f"```{msg}```")
    except Exception as e:
        _log_error(f"!system failed: {e}")
        await ctx.send("Chyba při získávání systémových informací.")

@bot.command(name='settings')
async def settings_command(ctx):
    """Zobrazí aktuální konfiguraci a důležitá nastavení"""
    try:
        cfg_items = {
            "TRADE_ENABLED": getattr(config, "TRADE_ENABLED", None),
            "NOTIFY_ONLY": getattr(config, "NOTIFY_ONLY", None),
            "SYMBOLS": getattr(config, "SYMBOLS", None),
            "ENTRY_TFS": getattr(config, "ENTRY_TFS", None),
            "MAX_TRADES_PER_DAY": getattr(config, "MAX_TRADES_PER_DAY", None),
            "MAX_POSITIONS_PER_SYMBOL": getattr(config, "MAX_POSITIONS_PER_SYMBOL", None),
            "MAX_SPREAD_PIPS": getattr(config, "MAX_SPREAD_PIPS", None),
            "RISK_PCT": getattr(config, "RISK_PCT", None),
            "MAX_RISK_PER_TRADE_PCT": getattr(config, "MAX_RISK_PER_TRADE_PCT", None),
            "MAX_DAILY_LOSS_PCT": getattr(config, "MAX_DAILY_LOSS_PCT", None),
            "START_BALANCE": getattr(config, "START_BALANCE", None),
            "ALIGN_TF_HIGH": getattr(config, "ALIGN_TF_HIGH", None),
            "ALIGN_TF_MID": getattr(config, "ALIGN_TF_MID", None),
        }
        lines = [f"{k}: {v}" for k, v in cfg_items.items()]
        await ctx.send("```Settings:\n" + "\n".join(lines) + "\n```")
    except Exception as e:
        _log_error(f"!settings failed: {e}")
        await ctx.send("Chyba při získávání nastavení.")

@bot.command(name='health')
async def health_command(ctx):
    """Zobrazí health informace o systému a MT5 (connection, loops, alerts)"""
    try:
        acc = None
        try:
            acc = mt5.account_info()
        except Exception:
            acc = None
        mt5_connected = bool(acc)
        last_state = _last_mt5_state
        alerts_count = len(_alerts_cache) if _alerts_cache is not None else 0
        sender_running = sender_loop.is_running() if hasattr(sender_loop, "is_running") else False
        alerts_running = alerts_check_loop.is_running() if hasattr(alerts_check_loop, "is_running") else False
        mt5_health_interval = MT5_HEALTH_INTERVAL_S

        msg = (
            f"MT5 connected: {mt5_connected}\n"
            f"Last MT5 known state: {last_state}\n"
            f"Alerts in memory: {alerts_count}\n"
            f"Sender loop running: {sender_running}\n"
            f"Alerts check loop running: {alerts_running}\n"
            f"MT5 health check interval (s): {mt5_health_interval}\n"
            f"MT5 path configured: {'yes' if MT5_PATH else 'no'}"
        )
        await ctx.send(f"```{msg}\n```")
    except Exception as e:
        _log_error(f"!health failed: {e}")
        await ctx.send("Chyba při získávání health informací.")

@bot.command(name='helpme')
async def helpme_command(ctx, command: str = None):
    help_texts = {
        'stats': 'Zobrazí statistiky obchodování, volitelně s zónami pro symbol',
        'balance': 'Zobrazí informace o účtu',
        'positions': 'Zobrazí otevřené pozice',
        'quote': 'Zobrazí aktuální ceny pro symbol',
        'zones': 'Zobrazí cenové zóny pro symbol',
        'alerts': 'Správa cenových alertů',
        'risk': 'Zobrazí risk management informace',
        'calendar': 'Zobrazí ekonomický kalendář',
        'settings': 'Zobrazí aktuální nastavení',
        'health': 'Zobrazí zdraví systému',
        'system': 'Detailní systémové informace',
        'backup': 'Vytvoří backup konfigurace',
        'export': 'Exportuje data',
        'restart': 'Restartuje bota (admin only)',
        'log': 'Zobrazí logy (admin only) - params: number or text filter, optional filename/date',
        'tradingmode': 'Nastaví trading mód (live/notify)',
        'performance': 'Zobrazí performance metriky',
        'comment': 'Zobrazí náhodný komentář/motivaci',
        'myid': 'Zobrazí tvé Discord ID',
        'closeposition': 'Uzavře pozici (admin only)',
    }
    if command:
        if command in help_texts:
            await ctx.send(f"`!{command}`: {help_texts[command]}")
        else:
            await ctx.send(f"Příkaz `{command}` neexistuje.")
    else:
        commands_list = "\n".join([f"!{cmd}: {desc}" for cmd, desc in help_texts.items()])
        await ctx.send(f"```Dostupné příkazy:\n\n{commands_list}```")

# ----------------------
# Error handling
# ---------------------- 
@bot.event
async def on_command_error(ctx, error):
    """Globální error handler - friendly messages for common issues"""
    try:
        if isinstance(error, commands.CommandNotFound):
            await ctx.send("Neznámý příkaz. Napiš `!helpme` pro seznam příkazů.")
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("Nemáš oprávnění pro tento příkaz.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("Špatný formát argumentů. Zkontroluj syntaxi.")
        else:
            _log_error(f"Command error: {error}")
            await ctx.send("Došlo k chybě při provádění příkazu. Zkontroluj logy.")
    except Exception as e:
        _log_debug(f"on_command_error failed: {e}")

# ----------------------
# Run
# ----------------------
def run_bot():
    token = os.getenv("DISCORD_TOKEN", DISCORD_TOKEN)
    ch_id = os.getenv("DISCORD_CHANNEL_ID", DISCORD_CHANNEL_ID)
    if not token or not ch_id:
        print("Discord credentials missing.")
        return
    bot.run(token)

if __name__ == "__main__":
    run_bot()