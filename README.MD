# Trading Bot — Swing Strategy (ICT/SMC)

## Sekce 1 — Přehled systému

Moderní algoritmický trading bot navržený pro automatizované obchodování na platformě MetaTrader 5, s primárním zaměřením na splnění podmínek a správu kapitálu u prop-firem (např. FTMO). Systém implementuje pokročilou **Swing strategii** postavenou na konceptech institucionálního obchodování (Inner Circle Trader / Smart Money Concepts), využívající detekci Supply/Demand zón, Market Structure Shift (MSS) a likvidity. Bot obsahuje robustní modul správy rizik, automatizovaný deník obchodů, Discord notifikace a interaktivní webový dashboard pro sledování výkonnosti v reálném čase.

| Vlastnost | Hodnota |
|---|---|
| Jazyk | Python 3.10+ |
| Broker připojení | MetaTrader 5 (přes `MetaTrader5` Python package) |
| Strategie | Swing (ICT/SMC koncepty) + SMT divergence |
| Obchodované páry | EURUSD, GBPUSD, USDJPY, USDCAD, AUDUSD, NZDUSD |
| Timeframe vstupu | H1 |
| Analytické TF | D1 (zóny, trend), H4 (trend), H1 (entry), M30 (konfirmace) |
| Session | New York 13:00–17:00 UTC |
| Notifikace | Discord bot |
| Dashboard | WebSocket server + Cloudflare tunnel |
| Persistence | SQLite (`db/trades.db`) + `state.json` |
| Konfigurace | `.env` soubor |

---

## Sekce 2 — Struktura projektu

```
ftmo/
├── main.py                   # Hlavní vstupní bod, trading loop, orchestrace vláken
├── config.py                 # Načítání .env, dataclassy AppConfig, validace
├── models.py                 # Datové modely: TradePlan, TradeResult, TradeDirection
├── data.py                   # MT5 wrapper pro fetch_rates() s lru_cache a retry logikou
├── executor.py               # TradeManager — vysoká úroveň správy exekuce (cooldown, startup protection)
├── trade.py                  # TradeExecutor — nízkoúrovňová MT5 exekuce, BE, Trailing SL, Partial TP
├── risk.py                   # Výpočty lotů, risk_gates_ok (drawdown/loss limity), spread check
├── position_watcher.py       # Scanování historie MT5 a synchronizace stavu
├── metrics.py                # PnL kalkulace, Sharpe ratio, winrate, broker-time offset synchronizace
├── optimizer.py              # StrategyOptimizer — Walk-Forward Optimization ATR multiplikátorů
├── state_manager.py          # Synchronní a asynchronní persistence stavu do state.json
├── notifications.py          # Abstrakce notifikací s rate-limiterem
├── discord_bot.py            # Discord integrace, příkazy, alerts loop, message queue
├── status_display.py         # Terminálový dashboard a WebSocket session status
├── live_alerts.py            # Monitorovací smyčka pro kritické drawdowny a winrate upozornění
├── comments.py               # Generátor náhodných komentářů k obchodům
├── image_generator.py        # Generování vizualizací statistik a Supply/Demand zón
├── monitoring.py             # Monitorování systémových prostředků (RAM/CPU)
├── runtime_store.py          # In-memory úložiště pro změny konfigurace za běhu
├── logger.py                 # Rotační logging systém, structured logs (JSON)
├── analysis/
│   ├── zones.py              # Detekce Supply/Demand zón filtrem lokálních extrémů a shlukování
│   ├── volume.py             # Analýza FVG a Equilibrium (50% retracement)
│   ├── mtf.py                # Multi-Timeframe trend analýza (Weekly, Daily, H4)
│   └── econ_calendar.py      # EconomicCalendar — scraping zpráv z Forex Factory / Myfxbook
├── strategy/
│   ├── __init__.py           # Factory pro výběr strategie (SWING/SMT)
│   ├── swing_strategy.py     # Implementace 7-úrovňového Swing filtru
│   ├── mss_strategy.py       # Market Structure Shift, Liquidity Sweep a Swing Points
│   └── smt_strategy.py       # SMT Divergence (korelace ES/NQ)
├── db/
│   ├── dao.py                # SQLite Data Access Object pro tabulku trades
│   ├── migrate.py            # Skript pro inicializaci a migrace schématu
│   └── migrations/           # SQL skripty pro schema
└── logs/                     # Denní logy aplikace
```

---

## Sekce 3 — Architektura a tok dat

### 3.1 Diagram spuštění (ASCII)

```
main.py __name__=="__main__"
    └─► main_entrypoint()
            ├─► load_config()
            ├─► TradeManager.__init__()
            ├─► watchdog_thread [daemon]
            ├─► trading_loop() [daemon thread]
            │       └─► [každých 60s pro každý symbol]
            │               ├─► build_plan(symbol)
            │               ├─► risk_gates_ok()
            │               ├─► TradeManager.execute_plan()
            │               ├─► trade_executor.manage_open_positions()
            │               └─► scan_history_and_update()
            ├─► WebSocket server [background thread]
            └─► discord_bot.run_bot() [blocking — main thread]
```

### 3.2 Vlákna a jejich zodpovědnosti

| Vlákno | Funkce | Typ | Co dělá |
|---|---|---|---|
| main thread | `discord_bot.run_bot()` | blocking | Spouští asyncio event loop pro Discord bota a příkazy. |
| trading-thread | `trading_loop()` | daemon | Hlavní smyčka — skenování trhu, exekuce plánů, správa pozic. |
| watchdog | `watchdog_thread_func()` | daemon | Sleduje heartbeat `main.py`, provádí restart při zamrznutí logic threadu. |
| ws-server | `websocket_server.start_background_thread()` | daemon | Obsluhuje dashboard přes WebSocket pro real-time status. |
| optimizer | `StrategyOptimizer.run_optimization()` | daemon | Běží v pozadí při splnění podmínek triggeru (čas, volatilita, winrate). |

### 3.3 Tok zpráv — notifikace

```
trading_loop / discord_command
    └─► notifications.notify_*()
            └─► _post_discord()
                    └─► _sender(content)   ← funkce nastavená přes set_sender()
                            └─► asyncio queue (discord_bot._message_queue)
                                    └─► sender_loop [tasks.loop(seconds=2)]
                                            └─► discord_channel.send()
```

---

## Sekce 4 — Konfigurace (.env)

| Proměnná | Typ | Default | Popis |
|---|---|---|---|
| `MT5_LOGIN` | int | — | Přihlašovací číslo MT5 účtu. |
| `MT5_PASSWORD` | str | — | Heslo MT5 účtu. |
| `MT5_SERVER` | str | — | Název brokera (např. `FTMO-Demo`). |
| `MT5_PATH` | str | — | Cesta k `terminal64.exe`. |
| `APP_MODE` | str | `offline` | `live` = vyžaduje MT5; `offline` = Discord/backtest bez MT5. |
| `STRATEGY` | str | `SWING` | Vybraná strategie: `SWING` nebo `SMT`. |
| `SYMBOLS` | str | `EURUSD` | Čárkou oddělený seznam symbolů pro trading. |
| `ENTRY_TFS` | str | `H1` | Timeframe pro vstupní signál. |
| `POSITION_MODE` | str | `PERCENT` | Metoda výpočtu lotů: `% equity` nebo `fixní`. |
| `RISK_PCT` | float | `0.03` | Riziko na obchod (0.01 = 1% equity). |
| `MAX_RISK_PER_TRADE_PCT` | float | `0.005` | Bezpečnostní hard limit rizika. |
| `FAT_FINGER_MAX_LOTS` | float | `2.0` | Absolutní maximum lotů jedné pozice. |
| `MAX_DRAWDOWN_PCT` | float | `0.03` | Limit celkového drawdownu (zastaví bota). |
| `MAX_DAILY_LOSS_PCT` | float | `0.008` | Limit denní ztráty vyhodnocovaný z deníku. |
| `MAX_WEEKLY_DRAWDOWN` | float | `0.02` | Limit týdenního drawdownu. |
| `MAX_MONTHLY_DRAWDOWN` | float | `0.03` | Limit měsíčního drawdownu. |
| `CONSECUTIVE_LOSS_LIMIT` | int | `3` | Stop-loss pro obchodování po sérii ztrát. |
| `MAX_SPREAD_PIPS` | float | `2.5` | Maximální povolený spread pro vstup. |
| `MAX_POSITIONS_PER_SYMBOL` | int | `2` | Počet povolených pozic na jeden pár. |
| `MAX_TRADES_PER_DAY` | int | `10` | Maximální počet obchodů za den. |
| `USE_TRAILING` | bool | `false` | Povolení Trailing Stopu. |
| `TRAILING_R_MULT` | float | `0.5` | Vzdálenost trailingu v R-multu. |
| `BREAKEVEN_RR` | float | `1.5` | Úroveň RR pro posun na Break-Even. |
| `PARTIAL_TP_ENABLED` | bool | `true` | Povolení částečného uzavírání zisku. |
| `PARTIAL_TP_RR` | float | `1.0` | Úroveň RR pro první partial TP. |
| `PARTIAL_TP_PERCENT` | float | `50` | Kolik % pozice se má zavřít při partial TP. |
| `USE_LIMIT_ORDERS` | bool | `false` | Používat Buy/Sell Limit místo Market. |
| `SL_POINTS` | int | `200` | Fixní SL v bodech (0 = strategický SL). |
| `TP_POINTS` | int | `400` | Fixní TP v bodech. |
| `SL_MULTIPLIER` | float | `2.5` | ATR násobič pro SL (použito optimizerem). |
| `TP_MULTIPLIER` | float | `4.0` | ATR násobič pro TP (použito optimizerem). |
| `NEWYORK_START` | str | `13:00` | Start trading okna (Broker/UTC). |
| `NEWYORK_END` | str | `17:00` | Konec trading okna (Broker/UTC). |
| `POLL_INTERVAL_SECONDS` | int | `60` | Čekání mezi cykly trading loopu. |
| `CONSOLIDATION_ENABLED` | bool | `true` | Globální filtr tržní konsolidace. |
| `NEWS_LOOKAHEAD_MIN` | int | `120` | Kolik minut před zprávou neobchodovat. |
| `TRADE_ENABLED` | bool | `true` | Master switch pro vypnutí obchodování. |
| `START_WEBSOCKET` | int | `1` | Povolení dashboardu. |
| `DISCORD_TOKEN` | str | — | Token pro Discord bota. |

---

## Sekce 5 — Moduly — detailní reference

#### `main.py`
**Zodpovědnost:** Hlavní mozek aplikace, provádí orchestraci všech vláken, zajišťuje inicializaci MT5 a spravuje hlavní obchodní smyčku `trading_loop`.
**Veřejné API:** `main_entrypoint()` — inicializuje bota. `trading_loop()` — prochází symboly, volá buildery plánů a exekutory.
**Důležité chování:** Obsahuje `self-restart` logiku na základě memory limitu a watchdogu.

#### `config.py`
**Zodpovědnost:** Načítání konfigurace z `.env` a její validace.
**Veřejné API:** `load_config()`, `reload_config()`.
**Důležité chování:** Exportuje globální instanci `config`, která je sdílena napříč aplikací (i když se v každém modulu importuje nově).

#### `data.py`
**Zodpovědnost:** Nízkoúrovňová komunikace s MT5 API.
**Veřejné API:** `fetch_rates(symbol, tf, bars)` — vrací DataFrame.
**Důležité chování:** Používá `@lru_cache` pro rychlé opakované dotazy a `mt5_retry` dekorátor pro ošetření dočasných výpadků spojení.

#### `executor.py` (TradeManager)
**Zodpovědnost:** Logika vyšší úrovně nad exekucí.
**Veřejné API:** `execute_plan(plan)` — hlavní brána pro otevření obchodu.
**Důležité chování:** Implementuje `startup protection` (čekání N barů po startu) a `cooldown` mezi obchody na stejném symbolu.

#### `trade.py` (TradeExecutor)
**Zodpovědnost:** Konkrétní MT5 odesílání orders a správa otevřených pozic.
**Veřejné API:** `place_trade()`, `manage_open_positions()`.
**Důležité chování:** Zajišťuje logiku trailing stopu, break-even a partial TP bleskovými modifikacemi tiketů.

#### `optimizer.py`
**Zodpovědnost:** Automatizovaná Walk-Forward optimalizace ATR multiplikátorů.
**Veřejné API:** `run_optimization()`, `should_run_optimization()`.
**Důležité chování:** Implementuje fitness funkci založenou na Sharpe Ratio R-násobků a multi-point grid search.

---

## Sekce 6 — Swing strategie — kompletní rozhodovací logika

### 6.1 Vstupní podmínky (7 filtrů v pořadí)

**Filtr 1 — Zone Touch**
- Bot načte denní zóny (D1) a hledá, zda se aktuální cena nachází v toleranci zóny.
- Tolerance: `max(ATR(14) * 0.25, price * 0.0006)`.
- **Cíl:** Obchodovat pouze z míst, kde se očekává institucionální zájem.

**Filtr 2 — Zone Significance**
- Zóna musí být "silná". Bot počítá dotyky zóny v historii.
- **Podmínka:** Aspoň 3 dotyky na D1 grafech nebo 3 na týdenních (Weekly).
- Pokud je zóna příliš čerstvá a neověřená, signál se ignoruje.

**Filtr 3 — Entry Pattern (Reversal)**
Na H1 timeframe bot hledá potvrzení směru:
1. **Bullish/Bearish Engulfing**: Klasický pohlcující svíčkový pattern.
2. **Rejection Wick**: Svíčka s dlouhým knotem signalizujícím odmítnutí ceny v zóně.
3. **FVG (Fair Value Gap)**: Detekce nerovnováhy směrem od zóny.

**Filtr 4 — Multi-TF Trend Consensus**
- Kontrola trendu na Weekly (SMA), Daily (SMA20) a H4 (SMA20).
- Skutečný konsensus nastává, pokud většina TF souhlasí s detekovaným patternem.

**Filtr 5 — H1/M30 Confirmation**
- Musí existovat potvrzení (Engulfing/Wick) na alespoň jednom z nižších TF (H1 nebo M30).

**Filtr 6 — MSS Confluence**
- Vyžaduje **Market Structure Shift**: Breakout předchozího swing bodu po vymetení likvidity (Sweep).

**Filtr 7 — Continuation**
- Cena nesmí být příliš natažená. Vstup by měl být blízko **Equilibrium** (50 % posledního pohybu) nebo doprovázen čerstvým FVG.

### 6.2 Výpočet SL a TP (ATR Multipliers)
Bot dynamicky počítá stop-loss a take-profit podle volatility.
- **Stop-Loss:** `entry ± (ATR(14) * SL_MULTIPLIER)`.
- **Take-Profit:** `entry ∓ (ATR(14) * TP_MULTIPLIER)`.

### 6.3 Position sizing
Risk je fixní % z equity. Velikost lotů se vypočítává tak, aby ztráta na SL přesně odpovídala zvolenému riziku s ohledem na `tick_value` daného instrumentu.

---

## Sekce 7 — MSS strategie (Market Structure Shift)

#### 7.1 Detekce swing pointů
Používá fraktálové pravidlo (window=2): Candle je Swing High, pokud má vyšší High než dvě svíčky před ní a dvě po ní.

#### 7.2 Liquidity Sweep detekce
Sleduje se včerejší nebo týdenní High/Low. Pokud cena prorazí toto High/Low, ale následně se uzavře zpět dovnitř, je to klasifikováno jako vymetení likvidity.

#### 7.3 MSS potvrzení
Kombinace Sweepu následovaného silným svíčkovým uzávěrem pod/nad opačným swing bodem. Toto signalizuje změnu nálady na trhu.

---

## Sekce 8 — Risk Management systém

#### 8.1 Hierarchie kontrol před vstupem
Bot provádí kaskádu kontrol od globálních (session, trade enabled) přes rizikové (spread, drawdown) až po analytické (zóny, MSS). Pokud selže jediná, obchod není realizován.

#### 8.2 Drawdown výpočty
1. **Celkový**: Z `equity_start` uloženého v `state.json`.
2. **Denní**: Kalkulováno synchronizací historických dealů v reálném čase.
3. **Pauza**: Při dosažení limitů bot okamžitě uzavře pozice a zastaví otevírání nových na zbytek dne/týdne.

---

## Sekce 9 — Discord bot — příkazy reference

| Příkaz | Admin? | Popis |
|---|---|---|
| `!status` | ne | Přehled účtu, PnL a MT5 zdraví. |
| `!why` | ne | Diagnostika posledních zamítnutých signálů. |
| `!trade live/notify` | ano | Přepínání mezi ostrým a notifikačním režimem. |
| `!panic` | ano | Nouzové zavření všeho a zastavení bota. |
| `!riskset X` | ano | Nastavení rizika v % za běhu. |
| `!zones SYMBOL` | ne | Vygeneruje a pošle graf se zónami. |

---

## Sekce 10 — Spuštění a deployment

### 10.1 Požadavky
- Windows 10/11 (MT5 native requirement).
- Python 3.10 nebo vyšší.
- MetaTrader 5 terminál přihlášený k účtu.

### 10.2 Spuštění
Nejjednodušší spuštění je přes `start.bat`, který automaticky:
1. Spustí MT5 terminál.
2. Spustí hlavního bota (Python).
3. Nastartuje Cloudflare tunnel pro přístup k dashboardu.

---

## Sekce 11 — Optimizer — Walk-Forward

Optimizer běží v pozadí v samostatném vlákně. Používá historická data (3000 svíček) k nalezení ideální kombinace ATR multiplikátorů. Výsledek se aplikuje pouze tehdy, pokud prokáže stabilitu v Out-of-Sample okně (budoucí neznámá data) se Sharpe Ratiem nad 0.3 a win rate nad 35 %.

---

## Sekce 14 — Známé limitace a technický dluh

1. **Konfigurace**: `load_config()` je voláno v každém modulu, což ztěžuje propagaci runtime změn, které se neuloží do `.env`.
2. **Watchdog**: Při tvrdém restartu watchdogem se resetuje i počítadlo `equity_start`, což dočasně "vymaže" historii drawdownu pro daný den.
3. **Risk Gates**: Pokud selže čtení CSV deníku, brána může v některých verzích povolit trading (fallback k `True`).
4. **Drawdown limity**: Týdenní a měsíční limity jsou v configu, ale `risk_gates_ok` v aktuální verzi prioritizuje denní a celkový limit.
5. **Async**: Míchání `asyncio.run()` v synchronní trading smyčce může způsobovat výpadky WebSocket spojení při dlouhých I/O operacích.
6. **Cache**: `@lru_cache` na `fetch_rates` může způsobovat problémy při dynamických backtestech na datech, která se mění v reálném čase.
7. **Multi-symbol Optimizer**: Optimizer v defaultu optimalizuje hlavní symbol, ale výsledná ATR nastavení se v této verzi aplikují globálně.
8. **File System**: Mezery v názvech cest k projektu (`ftmo ukazka`) mohou způsobit pád batch skriptů.

---

## Sekce 15 — Bezpečnostní poznámky
- Nikdy necommitujte soubor `.env` — obsahuje vaše MT5 a Discord klíče.
- `BOT_OWNER_ID` v `.env` je klíčový — bez něj má k admin příkazům přístup každý administrátor vašeho Discord serveru.
- Dashboard URL z Cloudflare tunnelu by měla zůstat soukromá.
