
# Trading Bot

# **MT5 Automatic Trading System**

Plně automatický systém pro trading přes MetaTrader 5 s řízením rizika, high-impact news filtrem a Discord monitoringem.

---

## Funkce

* Multi-TF price-action systém: D1-H4-H1-M30-M5
* SR zóny, wick rejection, engulfing, FVG
* NY session only
* MT5 auto-execution
* Max trades/day, DD guard, spread filter
* Web scraping + MT5 calendar
* Discord kontrola, příkazy, restart
* Self-heal bezpečnostní restart

---

## Požadavky

| Komponenta | Verze         |
| ---------- | ------------- |
| OS         | Windows 10/11 |
| Python     | 3.10+         |
| MetaTrader | MT5           |

---

## Instalace

### **1. Instalace Pythonu**

* stáhni z python.org
* během instalace zaškrtni **Add to PATH**
* ověření:

```
python --version
```

### **2. Instalace VSCode**

* stáhni z code.visualstudio.com
* extensions:

  * Python
  * Pylance

---
pip install -r requirements.txt                    nainstaluje knihovny

python -m pip freeze | % { $_.split('==')[0] } | % { python -m pip uninstall -y $_ }        odinstaluje vsechny knihovny

python -m pip cache purge                       odstrani cache a mezipamet po zbyvajicich knihovnach



### **3. Instalace MetaTrader 5**

#### Stažení

* z oficiálních stránek nebo vývojáře brokera

#### Přihlášení

* založ demo nebo reálný účet
* zapni:

`Tools → Options → Expert Advisors → Allow automated trading`

#### Ověř síť

Terminál MT5 musí být spuštěný **před botem**.

---

### **4. Přidání symbolů**

Otevři MT5 → Market Watch → klik pravým:

`Symbols → enable páry které obchoduješ`

---

### **5. Git projekt / stažení kódu**

Extrahuj projekt do složky:

```
C:\Users\Jiras\OneDrive\Plocha\ftmo          napriklad, kod si muzes umistit kamkoliv
```

---

### **6. Instalace Python knihoven**

V projektu spusť:

```
pip install -r requirements.txt
```

---

## .env konfigurace

vypln informace v .env

### Nutné proměnné

```
MT5_LOGIN=xxxx
MT5_PASSWORD=xxxx
MT5_SERVER=Broker-Server
SYMBOLS=EURUSD,GBPUSD,USDJPY
RISK_PER_TRADE=0.01
MAX_TRADES_PER_DAY=4
COMMENTS_DIR=./comments
NEWYORK_START=14:30
NEWYORK_END=21:00
SELF_RESTART=true
```

### Discord

```
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_ID=
BOT_OWNER_ID=
```

---

## Vytvoření bota
Udělení oprávnění pro čtení/posílání zpráv a získat token + channel ID.

1. Vytvoření aplikace a bota (Discord Developer Portal)
   1.1 Otevři: [https://discord.com/developers/applications](https://discord.com/developers/applications)
   1.2 Klikni „New Application“. Dej název. Ulož.
   1.3 V levém menu zvol „Bot“. Klikni „Add Bot“ → „Yes, do it“.
   1.4 Pod položkou Bot najdeš „TOKEN“ (Click to Reveal). **Token si ulož**.

   * Nikomu ho nedávej. Pokud se token kompromituje, Regenerate.

2. Povolení intents (důležité)
   2.1 Na téže stránce Bot najdi sekci „Privileged Gateway Intents“.
   2.2 Zapni **Message Content Intent** (nutné pro čtení obsahu příkazů).
   2.3 Pokud chceš shromažďovat členy, zapni také Presence / Server Members podle potřeby.
   2.4 Klikni Save Changes.

3. Nastavení oprávnění (OAuth2 invite)
   3.1 V levém menu otevři „OAuth2“ → „URL Generator“.
   3.2 V „SCOPES“ zaškrtni: **bot**, případně **applications.commands** (pokud chceš slash příkazy).
   3.3 V části „BOT PERMISSIONS“ vyber přesně:

   * View Channels
   * Read Message History
   * Send Messages
   * Embed Links
   * Attach Files
   * Manage Messages (volitelně, pokud chceš mazat zprávy)
   * Add Reactions (volitelně)
   * Use External Emojis (volitelně)
     3.4 Zkopíruj vygenerovaný URL, otevři ho v prohlížeči, vyber server a přidej bota.

Poznámka: pokud nechceš riskovat, použij jen Body: View, Read, Send.

4. Získání Channel ID (kam bude bot posílat)
   4.1 V Discord klientu jdi do User Settings → Advanced → zapni **Developer Mode**.
   4.2 Pravým tlačítkem na kanál → „Copy ID“. To je `DISCORD_CHANNEL_ID`.
   4.3 Pokud chceš bot do více kanálů, můžeš použít více ID nebo nechat bot reagovat globálně.

5. Uložení do .env
   Do projektu vlož / uprav `.env`:

```
DISCORD_TOKEN=tvuj_token_z_developer_portalu
DISCORD_CHANNEL_ID=123456789012345678

```
6. Nastavení v kódu (ověření intentu)
   6.1 V kódu při tvorbě bota musíš povolit `message_content` intent:

```py
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
```

6.2 Zajisti, že v Developer Portalu je Message Content Intent povolený. Jinak bot sice poběží, ale příkazy nečetl.

7. Testování bota
   7.1 Spusť `python discord_bot.py` nebo spuštění přes `main.py`.
   7.2 Na serveru napiš `!myid`. Bot by měl odpovědět s tvým ID.
   7.3 Napiš `!status`. Bot odešle přehled stavu.
   7.4 Pokud nedostaneš odpověď:

* Zkontroluj token v `.env`.
* Zkontroluj, že bot má oprávnění posílat zprávy v kanálu.
* Zapni v DevPortalu Message Content Intent.
* Zkontroluj konzoli bota pro chyby.

8. Dodatečné bezpečnostní a provozní kroky
   8.1 Token nikdy neposílej veřejně. Pokud unikne, v Dev Portal zvol „Regenerate“.
   8.2 Pro produkci dej bota do vlastního Discord serveru nebo jen omezeného kanálu.
   8.3 Pokud nasazuješ na VPS, nastav systemd/tmux/screen aby bot běžel non-stop.
   8.4 Doporučené logs: povolit logování do souboru (máš `logs/`) a rotovat.

9. Checklist před spuštěním

* MT5 běží a je přihlášený do stejného účtu jako v `.env` (MT5_LOGIN apod.).
* `.env` obsahuje `DISCORD_TOKEN` a `DISCORD_CHANNEL_ID`.
* Bot je pozván na server a má oprávnění pro daný kanál.
* V Dev Portal povolen `Message Content Intent`.
* V kódu intents nastaveny (`intents.message_content = True`).
* Logy fungují, případné chyby kontroluj v `logs/discord_bot.log` nebo v konzoli.

---

Krátký troubleshooting:

* Bot online v Dev Portal ale nereaguje → chybí Message Content Intent nebo chybné intents v kódu.
* Bot nereaguje v konkrétním kanálu → zkontroluj práva kanálu (Send Messages, View Channel).
* `Missing Permissions` v konzoli → uprav role bota na serveru nebo pozvi znovu s vyššími oprávněními.
* Channel ID = 0 nebo prázdné → zkopíruj ID správně (Developer Mode).

Pokud chceš, přidám do README i příklad invite URL s doporučenými permisemi a přesný screenshot-by-screenshot průvodce (popíšu textově nebo ti vygeneruji obrázkové kroky). Chceš to do README jako text nebo jako inline sekci se screenshoty?


## Spuštění

```
python main.py
```

Terminál/logs musí ukázat:

```
MT5 initialized and logged in.
Trading loop started.
Starting Discord bot.
```

---

## Strategie — Přehled

### Timeframe logika

| TF     | Účel               |
| ------ | ------------------ |
| D1     | hlavní trend       |
| H4     | swing bias         |
| H1/M30 | momentum potvrzení |
| M5     | vstupní trigger    |

### Podmínky vstupu

* Cena testuje validní D1 S/R zónu
* ATR tolerance
* Min. počet dotyků (D1+W)
* Bull/bear rejection nebo engulfing
* FVG detekce
* Higher TF potvrzení
* Spread OK
* Risk OK

### Výpočet risku

Lot size = fixní procento equity a vzdálenost SL.

---

## Discord příkazy

| Příkaz         | Funkce                 |
| -------------- | ---------------------- |
| `!status`      | stav MT5, trading mode |
| `!balance`     | equity / balance       |
| `!performance` | winrate, RR            |
| `!calendar`    | nadcházející news      |
| `!zones`       | SR graf                |
| `!log x`       | posledních x logů      |
| `!restart`     | restart robota         |
| `!tradingmode` | notify/live            |
| `!health  `    | aktualni vyuziti PC    |
| `!settings`    | zobrazí nastavení bota |
| `!alerts`      | alerty o pohybu ceny   |


---

## Struktura projektu

```
📁 PROJECT_ROOT/
├── 📁 analysis/
│   ├── mtf.py
│   ├── volume.py
│   ├── zones.py
│   └── entry.py
├── 📁 assets/
│   ├── 📁 backgrounds/
│   ├── Montserrat-Bold.tlf
│   └──README.md
├── 📁 backtest/
├── 📁 comments/
│   ├── takeprofit.txt
│   ├── stoploss.txt
│   ├── motivation.txt
│   ├── buy.txt
│   └── sell.txt
├── 📁 features/
│   ├── 📁 gamesy/
│   ├── 📁 env/ kalkulacky
│   ├── barvy.html
│   ├── cas.py
│   ├── csv-data.py
│   ├── diag.py
│   ├── kalkulacka.py
│   ├── README.md
│   ├── stats.py (nepouzivano/nefunkcni)
│   └── test.py  (nepouzivano/nefunkcni)
├── 📁 logs/
├── 📁 strategy/
│   ├── __init__.py
│   ├── swing_strategy.py
│   └── utils.py
├── 📁 env/
├── comments.py
├── config.py
├── data.py
├── discord_bot.py
├── executor.py
├── image_generator.py
├── logger.py
├── main.py
├── metrics.py
├── monitoring.py
├── notifications.py
├── position_watcher.py
├── README.MD
├── requirements.txt
├── risk.py
├── trade.py
├── utils.py
└── watchdog.py
```

---

## Typický flow

1. Bot načte price data z MT5
2. Analyzuje D1-H4-H1-M30-M5
3. Identifikuje S/R zone touch
4. Test reversalu a continuation
5. Výpočet lot size
6. Spread check
7. Risk gates
8. Vstup nebo skip
9. Řízení pozice
10. Update logů a Discordu

---

## Debug a testy

### Test MT5 připojení

```
python -c "import metatrader5 as mt5; print(mt5.initialize())"
```

### Test knihoven

```
pip install --force-reinstall -r requirements.txt
```

---

## Nejčastější chyby

| Problém               | Řešení                                         |
| --------------------- | ---------------------------------------------- |
| Bot nepřipojen k MT5  | špatné přihlášení nebo MT5 není spuštěn        |
| Neobchoduje           | mimo session, spread, news filter, žádný setup |
| Discord bot nereaguje | špatný token / chybí permissions               |
| Pouze signály         | NOTIFY_ONLY=true                               |
| MT5 dělá zvukové signály        | vypni zvuky MetaTraderu, jsou normální         |

---

## FAQ

**Q: Funguje na funded acc?**
Ano. Stavěno pro trading všeobecně, pouze upravit .env.

**Q: Umí hedging?**
Ne. Standardní one-position risk model.

**Q: Proč žádné obchody?**
Ne každý den je validní confluence. To je záměr.

**Q: Můžu přidat další páry?**
Ano, úprava `.env → SYMBOLS`.

---

## Bezpečnostní zásady

* nepoužívat martingale
* nepoužívat na účtu bez SL
* testovat 2–4 týdny


---

## Licence

Pouze pro osobní výzkum a výuku.

