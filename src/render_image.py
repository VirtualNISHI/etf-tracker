"""ETF Custody Flow Report の画像生成(Discord embed そっくりレイアウト)。

レイアウト方針: Discord で投稿される embed と見た目を揃える。
    ┌────────────────────────────────────────────────┐
    │▌📊 ETF Custody Flow Report                      │  cyan title (左に縦バー)
    │ 2026-05-08 08:54 JST · 過去24時間                │
    │                                                  │
    │▌🟠 BTC ETF Custody                              │  オレンジ縦バー
    │   IBIT       -147 BTC  (in: 0 / out: 147)        │
    │   FBTC     -3,113 BTC  (in: 569 / out: 3,683)    │
    │   BITB       -141 BTC  (in: 5 / out: 146)        │
    │   GBTC         +0 BTC  (in: 0 / out: 0)          │
    │   ────────────                                   │
    │   Net Total -3,402 BTC  ≈ -$272M                 │
    │                                                  │
    │▌🟣 ETH ETF Custody                              │  紫縦バー
    │   ETHA         +0 ETH  (in: 0 / out: 0)          │
    │   FETH         +0 ETH  (in: 249 / out: 249)      │
    │   ETHE         +0 ETH  (in: 0 / out: 0)          │
    │   ────────────                                   │
    │   Net Total    +0 ETH  ≈ +$0M                    │
    │                                                  │
    │▌📌 Notable                                      │  黄縦バー
    │   ・FBTC (BTC) は3日連続で流出継続               │
    │                                                  │
    └────────────────────────────────────────────────┘
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji

from src.flows import ChainSummary

JST = ZoneInfo("Asia/Tokyo")

# ============== カラー (Discord embed準拠) ==============
BG_COLOR = (47, 49, 54)         # #2F3136 Discord embed background
TEXT_COLOR = (220, 221, 222)
SUBTEXT_COLOR = (185, 187, 190)
MUTED_COLOR = (140, 144, 150)
DIVIDER_COLOR = (79, 84, 92)

CYAN_COLOR = (88, 196, 220)     # title
GREEN_COLOR = (87, 242, 135)
RED_COLOR = (237, 66, 69)

ORANGE_COLOR = (247, 147, 26)   # 🟠 BTC
PURPLE_COLOR = (139, 116, 230)  # 🟣 ETH
GOLD_COLOR = (255, 188, 60)     # 📌 Notable

WIDTH = 1100
MIN_HEIGHT = 600
MAX_HEIGHT = 1500

# ============== フォント ==============
JP_REGULAR_CANDIDATES = [
    "C:/Windows/Fonts/YuGothM.ttc",
    "C:/Windows/Fonts/YuGothR.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]
JP_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/meiryob.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]
MONO_CANDIDATES = [
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]
MONO_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/consolab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
]


def _find_font(candidates: list[str]) -> str:
    for c in candidates:
        if Path(c).exists():
            return c
    raise FileNotFoundError(f"No font: {candidates}")


def _signed(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.0f}"


def _signed_usd(v: float) -> str:
    m = v / 1_000_000
    sign = "+" if m >= 0 else ""
    if abs(m) >= 1:
        return f"{sign}${m:,.0f}M"
    k = v / 1_000
    return f"{sign}${k:,.0f}K"


def _color_for_net(v: float) -> tuple[int, int, int]:
    if v > 0:
        return GREEN_COLOR
    if v < 0:
        return RED_COLOR
    return MUTED_COLOR


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def render_daily_report(
    btc: ChainSummary,
    eth: ChainSummary,
    notable_lines: list[str] | None = None,
    now_jst: datetime | None = None,
) -> bytes:
    notable_lines = notable_lines or []

    title_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 36)
    date_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 22)
    section_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 28)
    mono_font = ImageFont.truetype(_find_font(MONO_CANDIDATES), 22)
    mono_bold_font = ImageFont.truetype(_find_font(MONO_BOLD_CANDIDATES), 24)
    notable_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 22)

    img = Image.new("RGB", (WIDTH, MAX_HEIGHT), color=BG_COLOR)
    draw = ImageDraw.Draw(img)
    pm = Pilmoji(img)  # 絵文字描画用(Twemoji)

    BAR_W = 6
    pad_x = 40

    y = 36
    # タイトル(左に縦バー = cyan)
    draw.rectangle([(pad_x - 18, y - 2), (pad_x - 18 + BAR_W, y + 38)], fill=CYAN_COLOR)
    pm.text((pad_x, y), "📊 ETF Custody Flow Report", font=title_font, fill=CYAN_COLOR)
    y += 50

    # 日付
    now = (now_jst or datetime.now(JST)).strftime("%Y-%m-%d %H:%M JST")
    draw.text((pad_x, y), f"{now} · 過去24時間", font=date_font, fill=TEXT_COLOR)
    y += 50

    # ============== BTC セクション ==============
    y = _draw_chain_section(
        draw, pm, y, pad_x, "🟠 BTC ETF Custody", btc, "BTC",
        ORANGE_COLOR, BAR_W, section_font, mono_font, mono_bold_font,
    )
    y += 16

    # ============== ETH セクション ==============
    y = _draw_chain_section(
        draw, pm, y, pad_x, "🟣 ETH ETF Custody", eth, "ETH",
        PURPLE_COLOR, BAR_W, section_font, mono_font, mono_bold_font,
    )
    y += 16

    # ============== Notable ==============
    if notable_lines:
        draw.rectangle(
            [(pad_x - 18, y - 2), (pad_x - 18 + BAR_W, y + 36)], fill=GOLD_COLOR
        )
        pm.text((pad_x, y), "📌 Notable", font=section_font, fill=GOLD_COLOR)
        y += 44
        for line in notable_lines:
            text = line if line.startswith("・") else "・" + line
            draw.text((pad_x + 8, y), text, font=notable_font, fill=TEXT_COLOR)
            y += 32
        y += 12

    # トリミング
    y += 30
    final_h = max(MIN_HEIGHT, min(y, MAX_HEIGHT))
    img = img.crop((0, 0, WIDTH, final_h))

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _draw_chain_section(
    draw: ImageDraw.ImageDraw,
    pm: Pilmoji,
    y0: int,
    pad_x: int,
    title: str,
    summary: ChainSummary,
    unit: str,
    bar_color: tuple[int, int, int],
    bar_w: int,
    section_font: ImageFont.FreeTypeFont,
    mono_font: ImageFont.FreeTypeFont,
    mono_bold_font: ImageFont.FreeTypeFont,
) -> int:
    y = y0
    draw.rectangle(
        [(pad_x - 18, y - 2), (pad_x - 18 + bar_w, y + 36)], fill=bar_color
    )
    pm.text((pad_x, y), title, font=section_font, fill=TEXT_COLOR)
    y += 50

    # 各 ETF 行(等幅)
    # レイアウト: [TICKER (~120px)] [signed value (~130px right-aligned)] [unit (~50px)] [(in: X / out: Y)]
    # ※ "Net Total" ラベル(9文字)が "-105,000" のような大きな値と被らないよう、
    #    value 右端を ticker 領域からしっかり離す。
    label_x = pad_x + 24
    value_right_x = pad_x + 24 + 130 + 130  # ticker 領域 130px + 値 130px = 260px 確保
    unit_x = value_right_x + 14
    inout_x = unit_x + 70

    for c in summary.clusters:
        # ticker
        draw.text((label_x, y), c.label, font=mono_font, fill=TEXT_COLOR)
        # signed value (色付き、右寄せ)
        v_text = _signed(c.net_flow)
        v_color = _color_for_net(c.net_flow)
        v_w = _text_w(draw, v_text, mono_font)
        draw.text((value_right_x - v_w, y), v_text, font=mono_font, fill=v_color)
        # unit
        draw.text((unit_x, y), unit, font=mono_font, fill=TEXT_COLOR)
        # (in: X / out: Y)
        inout = f"(in: {c.inflow:,.0f} / out: {c.outflow:,.0f})"
        draw.text((inout_x, y), inout, font=mono_font, fill=MUTED_COLOR)
        y += 32

    # 区切り線
    draw.line(
        [(label_x, y + 4), (label_x + 380, y + 4)], fill=DIVIDER_COLOR, width=1
    )
    y += 16

    # Net Total
    draw.text((label_x, y), "Net Total", font=mono_font, fill=TEXT_COLOR)
    v_text = _signed(summary.total_net_flow)
    v_color = _color_for_net(summary.total_net_flow)
    v_w = _text_w(draw, v_text, mono_bold_font)
    draw.text((value_right_x - v_w, y - 1), v_text, font=mono_bold_font, fill=v_color)
    draw.text((unit_x, y), unit, font=mono_font, fill=TEXT_COLOR)
    # ≈ +$XM
    usd_text = f"≈ {_signed_usd(summary.net_flow_usd)}"
    draw.text((inout_x, y), usd_text, font=mono_font, fill=v_color)
    y += 38

    return y
