#!/usr/bin/env python3
import os
import sys
import time
import threading
import traceback
import random
import signal
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv()

import MetaTrader5 as mt5

from logger import (
    log_info, log_error, log_debug, cleanup_old_logs,
    log_sleep, reset_sleep_flag
)
from strategy import build_plan
from executor import execute_plan, tick_symbol, clear_signal
from position_watcher import scan_history_and_update
from trade import manage_open_positions
from risk import risk_gates_ok
from utils import in_ny_session, parse_env_time, time_until_session, get_local_now
import discord_bot

from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER,
    SYMBOLS, ENTRY_TFS, WATCHDOG_INTERVAL_HOURS,
    USE_TRAILING, TRAILING_R_MULT, POLL_INTERVAL_SECONDS,
    SELF_RESTART, NEWYORK_START, NEWYORK_END
)

NY_START_TIME = parse_env_time(NEWYORK_START)
NY_END_TIME = parse_env_time(NEWYORK_END)

_last_plan_signature = {}
_stop_event = threading.Event()

def mt5_init():
    try:
        if not mt5.initialize():
            if not mt5.initialize():
                raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        if MT5_LOGIN:
            if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
                raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")
        log_info("MT5 initialized and logged in.")
    except Exception:
        raise

def mt5_shutdown_quiet():
    try:
        mt5.shutdown()
        log_info("MT5 shutdown complete.")
    except Exception:
        pass

def plan_signature(plan):
    try:
        return f"{plan.symbol}|{plan.direction}|{round(plan.entry_price, 5)}"
    except Exception:
        return str(plan)

def trading_loop():
    global _last_plan_signature
    try:
        cleanup_old_logs()
    except Exception as e:
        log_debug(f"cleanup_old_logs failed: {e}")

    try:
        mt5_init()
    except Exception as e:
        log_error(f"MT5 init failed: {e}")
        return

    try:
        acc = mt5.account_info()
        equity_start = float(acc.equity) if acc else 0.0
    except Exception:
        equity_start = 0.0

    last_watchdog = datetime.now(timezone.utc) - timedelta(hours=WATCHDOG_INTERVAL_HOURS)
    last_history_check = datetime.now(timezone.utc) - timedelta(minutes=15)

    sleeping_mode = False
    log_info("Trading loop started.")

    while not _stop_event.is_set():
        try:
            now = datetime.now(timezone.utc)
            if not in_ny_session(now, NY_START_TIME, NY_END_TIME):
                sleep_seconds = time_until_session(now, NY_START_TIME)
                if sleep_seconds < 1:
                    sleep_seconds = max(60, POLL_INTERVAL_SECONDS)
                if not sleeping_mode:
                    log_sleep("Outside trading hours", sleep_seconds)
                    sleeping_mode = True
                for _ in range(int(max(1, sleep_seconds))):
                    if _stop_event.is_set():
                        break
                    time.sleep(1)
                continue

            if sleeping_mode:
                reset_sleep_flag()
                sleeping_mode = False
                log_info("RESUME | Returned to trading hours")

            try:
                acc_info = mt5.account_info()
                balance = float(acc_info.balance) if acc_info else 0.0
                equity = float(acc_info.equity) if acc_info else 0.0
            except Exception:
                balance, equity = 0.0, 0.0

            try:
                if not risk_gates_ok(equity_start, balance, equity):
                    discord_bot.enqueue_message("RISK GATE | Trading paused")
                    log_info("RISK GATE | Trading paused", unique=True, key="RISK_GATE_PAUSED")
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue
            except Exception as e:
                log_debug(f"risk_gates_ok check failed: {e}")

            for s in SYMBOLS:
                if _stop_event.is_set():
                    break
                try:
                    tick_symbol(s)
                    tf_entry = ENTRY_TFS[0] if ENTRY_TFS else "M5"
                    plan = build_plan(s, tf_high="H4", tf_mid="H1", tf_entry=tf_entry)
                except Exception as e:
                    log_error(f"Error building plan for {s}: {e}")
                    continue

                if plan is None:
                    try:
                        clear_signal(s)
                    except Exception:
                        pass
                    continue

                sig = plan_signature(plan)
                if _last_plan_signature.get(s) == sig:
                    log_debug(f"{s}: duplicate plan signature, skipping.")
                    continue

                time.sleep(random.uniform(0.2, 1.5))

                try:
                    ticket = execute_plan(plan)
                except Exception as e:
                    log_error(f"execute_plan failed for {s}: {e}")
                    ticket = None

                if ticket:
                    _last_plan_signature[s] = sig

            try:
                manage_open_positions(use_trailing=USE_TRAILING, trailing_r_mult=TRAILING_R_MULT)
            except Exception as e:
                log_error(f"manage_open_positions failed: {e}")

            try:
                new_ts = scan_history_and_update(last_history_check)
                if new_ts:
                    last_history_check = new_ts
            except Exception as e:
                log_debug(f"scan_history_and_update error: {e}")

            try:
                if (datetime.now(timezone.utc) - last_watchdog).total_seconds() >= WATCHDOG_INTERVAL_HOURS * 3600:
                    acci = mt5.account_info()
                    bal = float(acci.balance) if acci else 0.0
                    eq = float(acci.equity) if acci else 0.0
                    discord_bot.enqueue_message(f"WATCHDOG | balance={bal:.2f} | equity={eq:.2f}")
                    last_watchdog = datetime.now(timezone.utc)
            except Exception as e:
                log_debug(f"watchdog notify failed: {e}")

            slept = 0
            while slept < POLL_INTERVAL_SECONDS and not _stop_event.is_set():
                time.sleep(1)
                slept += 1

        except Exception as e:
            log_error(f"Trading loop exception: {e}")
            traceback.print_exc()
            try:
                mt5.shutdown()
            except Exception:
                pass
            raise

    try:
        mt5_shutdown_quiet()
    except Exception:
        pass
    log_info("Trading loop stopped.")

def start_trading_thread():
    t = threading.Thread(target=trading_loop, name="trading-thread", daemon=True)
    t.start()
    return t

def restart_program():
    python = sys.executable
    os.execv(python, [python] + sys.argv)

def _handle_terminate(signum, frame):
    log_info(f"Signal {signum} received. Stopping.")
    _stop_event.set()

def main_entrypoint():
    signal.signal(signal.SIGINT, _handle_terminate)
    signal.signal(signal.SIGTERM, _handle_terminate)

    t = start_trading_thread()

    try:
        log_info(f"Starting Discord bot.")
        discord_bot.run_bot()
    except Exception as e:
        log_error(f"Discord bot crashed: {e}")
        traceback.print_exc()
        _stop_event.set()
        t.join(timeout=5)
        raise

if __name__ == "__main__":
    while True:
        try:
            log_info(f"Starting bot. SELF_RESTART={os.getenv('SELF_RESTART')}")
            main_entrypoint()
            log_info("Main exited normally.")
            break
        except KeyboardInterrupt:
            log_info("KeyboardInterrupt received. Exiting.")
            _stop_event.set()
            try: mt5.shutdown()
            except Exception: pass
            break
        except Exception as e:
            log_error(f"Fatal error in main: {e}")
            traceback.print_exc()
            try: mt5.shutdown()
            except Exception: pass
            if os.getenv("SELF_RESTART", "false").lower() in ("1","true","yes"):
                wait = 10
                log_info(f"SELF_RESTART enabled — restarting in {wait}s.")
                time.sleep(wait)
                try:
                    restart_program()
                except Exception as ex:
                    log_error(f"Restart failed: {ex}")
                    break
            else:
                log_info("SELF_RESTART disabled — exiting.")
                break
