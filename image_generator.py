# image_generator.py
import os
from PIL import Image, ImageDraw, ImageFont
from metrics import month_stats
from config import BACKGROUND_DIR, OUTPUT_IMAGE, LAST_BG_INDEX_FILE, FONT_PATH
from logger import log_debug, log_error
from typing import List, Optional
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from datetime import datetime

def _fonts():
    try:
        font_title = ImageFont.truetype(FONT_PATH, 80)
        font_label = ImageFont.truetype(FONT_PATH, 30)
        font_value = ImageFont.truetype(FONT_PATH, 90)
        font_usd = ImageFont.truetype(FONT_PATH, 25)
        font_percentage = ImageFont.truetype(FONT_PATH, 150)
        font_small = ImageFont.truetype(FONT_PATH, 25)
        return font_title, font_label, font_value, font_usd, font_percentage, font_small
    except Exception as e:
        log_debug(f"Font load failed: {e}")
        f = ImageFont.load_default()
        return (f, f, f, f, f, f)

def _sequential_background():
    os.makedirs(BACKGROUND_DIR, exist_ok=True)
    files = sorted([f for f in os.listdir(BACKGROUND_DIR) if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    if not files:
        img = Image.new("RGBA", (1200, 675), "#20232a")
        tmp = os.path.join(BACKGROUND_DIR, "__blank_bg.png")
        try:
            img.save(tmp)
        except Exception:
            pass
        return tmp
    try:
        with open(LAST_BG_INDEX_FILE, "r", encoding="utf-8") as f:
            last_index = int(f.read().strip())
    except Exception:
        last_index = -1
    next_index = (last_index + 1) % len(files)
    try:
        os.makedirs(os.path.dirname(LAST_BG_INDEX_FILE) or ".", exist_ok=True)
        with open(LAST_BG_INDEX_FILE, "w", encoding="utf-8") as f:
            f.write(str(next_index))
    except Exception:
        pass
    return os.path.join(BACKGROUND_DIR, files[next_index])

def generate_stats_image(output_path: str = OUTPUT_IMAGE) -> str:
    try:
        trades, pnl_usd, pnl_czk, pct = month_stats()
    except Exception as e:
        log_debug(f"month_stats failed: {e}")
        trades, pnl_usd, pnl_czk, pct = 0, 0.0, 0.0, 0.0

    try:
        bg = _sequential_background()
        img = Image.open(bg).convert("RGBA")
    except Exception as e:
        log_debug(f"Background load failed: {e}")
        img = Image.new("RGBA", (1200, 675), "#20232a")

    draw = ImageDraw.Draw(img)

    creb_color = "#DFDFDF"
    white = "#FFFFFF"
    grey = "#AAAAAA"

    if pct > 0:
        percent_color = "#A3FFE0"
        pct_text = f"+{pct:.2f}%"
    elif pct < 0:
        percent_color = "#FF8590"
        pct_text = f"{pct:.2f}%"
    else:
        percent_color = "#AAAAAA"
        pct_text = f"{pct:.2f}%"

    font_title, font_label, font_value, font_usd, font_percentage, font_small = _fonts()

    draw.text((180, 165), "jirass", font=font_title, fill=creb_color)
    draw.text((150, 310), "TRADES", font=font_label, fill=grey)
    draw.text((150, 370), f"{trades}", font=font_value, fill=white)
    draw.text((150, 470), "This month", font=font_usd, fill=grey)
    draw.text((550, 310), "MONTH PNL", font=font_label, fill=grey)

    usd_formatted = f"{int(round(pnl_usd)):,}".replace(",", ",")
    draw.text((550, 370), f"${usd_formatted}", font=font_value, fill=white)

    czk_formatted = f"{int(round(pnl_czk)):,}".replace(",", " ")
    draw.text((550, 470), f"{czk_formatted} CZK", font=font_small, fill=grey)

    draw.text((180, 580), pct_text, font=font_percentage, fill=percent_color)

    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.save(output_path)
        return output_path
    except Exception as e:
        log_debug(f"Saving image to {output_path} failed: {e}")
        out = os.path.basename(output_path)
        img.save(out)
        return out

# -----------------------
# NEW: generate_zones_image
# -----------------------
def generate_zones_image(symbol: str,
                         tf: str = "H4",
                         lookback_bars: int = 400,
                         zones: Optional[List[float]] = None,
                         output_path: Optional[str] = None) -> str:
    """
    Generate a PNG chart for the given symbol and timeframe and overlay zones (horizontal lines).
    Returns path to saved image.
    """
    if output_path is None:
        safe_name = f"{symbol}_{tf}_zones.png"
        output_path = os.path.join(os.path.dirname(OUTPUT_IMAGE) or ".", safe_name)

    try:
        # lazy import to avoid overhead if unused
        from data import fetch_rates
    except Exception as e:
        log_error(f"generate_zones_image: cannot import data.fetch_rates: {e}")
        raise

    try:
        df = fetch_rates(symbol, tf, bars=lookback_bars)
    except Exception as e:
        log_debug(f"generate_zones_image: fetch_rates failed for {symbol} {tf}: {e}")
        raise

    if df is None or df.empty:
        raise RuntimeError("No rate data available for plotting.")

    # preprocess times
    try:
        df = df.copy()
        df['time_dt'] = pd.to_datetime(df['time'], utc=True)
        df['mdates'] = mdates.date2num(df['time_dt'].dt.tz_convert(None))
    except Exception:
        df['time_dt'] = pd.to_datetime(df['time'], utc=True)
        df['mdates'] = mdates.date2num(df['time_dt'].dt.tz_convert(None))

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.set_title(f"{symbol} {tf} — last {len(df)} bars ({datetime.utcnow().isoformat()})")
    ax.set_ylabel("Price")

    # Draw candlesticks manually
    width = 0.6 * (df['mdates'].iloc[1] - df['mdates'].iloc[0]) if len(df) > 1 else 0.01
    for idx, row in df.iterrows():
        o = float(row['open'])
        h = float(row['high'])
        l = float(row['low'])
        c = float(row['close'])
        x = row['mdates']
        color = 'g' if c >= o else 'r'
        # wick
        ax.plot([x, x], [l, h], color='k', linewidth=0.8)
        # body
        rect_bottom = min(o, c)
        rect_height = abs(c - o)
        ax.add_patch(plt.Rectangle((x - width/2, rect_bottom), width, rect_height if rect_height > 0 else width*0.001,
                                   facecolor=color, edgecolor='k', linewidth=0.4, alpha=0.9))

    # Format x-axis
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d\n%H:%M'))
    plt.xticks(rotation=45, ha='right')

    # Plot zones if provided
    if zones:
        try:
            # choose colors and alpha
            for i, z in enumerate(sorted(set(zones))):
                # stronger visual for higher-index zones
                alpha = 0.6 if i < 6 else 0.35
                ax.axhline(z, linestyle='--', linewidth=1.4, alpha=alpha)
                # annotate left
                ax.text(df['mdates'].iloc[0], z, f" {z:.5f}", va='center', ha='left', fontsize=9, bbox=dict(facecolor='white', alpha=0.0))
        except Exception as e:
            log_debug(f"generate_zones_image: error plotting zones: {e}")

    # tight layout and save
    fig.tight_layout()
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig.savefig(output_path, dpi=150)
    except Exception as e:
        log_debug(f"generate_zones_image: save failed to {output_path}: {e}")
        # fallback to pwd
        fallback = os.path.basename(output_path)
        fig.savefig(fallback, dpi=150)
        output_path = fallback
    plt.close(fig)
    return output_path

if __name__ == "__main__":
    # quick local test (only if run directly; assumes data.fetch_rates can access MT5)
    try:
        p = generate_zones_image("EURUSD", tf="H4", lookback_bars=200, zones=None)
        print("Saved:", p)
    except Exception as e:
        print("Error:", e)
