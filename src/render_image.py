"""ETF Custody Flow Report の画像生成(Pillow)。

レイアウト方針(目標画像準拠):
    ┌─────────────────────────────────────────────────────┐
    │  📊 ETF Custody Flow Report                          │ シアンタイトル
    │  YYYY-MM-DD HH:MM JST · 過去24時間                    │
    │                                                       │
    │  ● BTC ETF Net Flow      ● ETH ETF Net Flow         │
    │  +X,XXX BTC              +XX,XXX ETH                │ 大きい緑/赤
    │  ≈ +$XXM                 ≈ +$XM                      │
    │                                                       │
    │  ┌─ BTC ETF・クラスタ別 ─┐  ┌─ ETH ETF・クラスタ別 ─┐│
    │  │ Coinbase Custody +X  │  │ Coinbase Custody +X  ││
    │  │ ▓▓▓▓░░░░░            │  │ ▓░░░░░░░             ││
    │  │ Fidelity         +X  │  │ Fidelity         +X  ││
    │  │ ▓▓▓▓▓▓▓▓░            │  │ ▓▓▓▓▓▓▓▓▓▓░          ││
    │  └──────────────────────┘  └──────────────────────┘│
    │                                                       │
    │  📌 Notable                                          │
    │  ・xxx                                                │
    │                                                       │
    │  Powered by mempool.space + Etherscan
    └─────────────────────────────────────────────────────┘

絵文字は色付き円・五芒星・小さな矩形チャート(画像処理で描画)で代替。
"""
from __future__ import annotations

import math
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from src.flows import ChainSummary

JST = ZoneInfo("Asia/Tokyo")

# ============== カラーパレット ==============
BG_COLOR = (35, 39, 42)            # #23272A 全体背景
CARD_COLOR = (47, 52, 55)          # #2F3437 カード背景
DIVIDER_COLOR = (79, 84, 92)       # #4F545C
TEXT_COLOR = (255, 255, 255)
SUBTEXT_COLOR = (185, 187, 190)
MUTED_COLOR = (140, 144, 150)

GREEN_COLOR = (87, 242, 135)
RED_COLOR = (237, 66, 69)

CYAN_COLOR = (67, 181, 230)        # 目標画像のタイトル色
ORANGE_COLOR = (247, 147, 26)
PURPLE_COLOR = (139, 116, 230)
GOLD_COLOR = (255, 188, 60)

BAR_FILL = (170, 174, 178)
BAR_HATCH = (60, 64, 68)
BAR_FILL_NEG = (237, 66, 69, 180)  # 流出時

# 画像サイズ(目標画像比率に近い、Twitter 横長 16:10)
WIDTH = 1200
MIN_HEIGHT = 900
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

FOOTER_TEXT_LEFT = "Powered by mempool.space + Etherscan"
FOOTER_TEXT_RIGHT = ""


def _find_font(candidates: list[str]) -> str:
    for c in candidates:
        if Path(c).exists():
            return c
    raise FileNotFoundError(f"No font found from: {candidates}")


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


def _draw_circle(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int],
) -> None:
    cx, cy = center
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color)


def _draw_star(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    r: int,
    color: tuple[int, int, int],
) -> None:
    cx, cy = center
    pts = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        radius = r if i % 2 == 0 else r * 0.45
        pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(pts, fill=color)


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int],
) -> None:
    """角丸矩形(Pillow 8.2+)。"""
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    ratio: float,  # -1.0 .. 1.0
) -> None:
    """目標画像風の進捗バー(残りはハッチング)。

    ratio が正なら左から白っぽい塗り、負なら赤い塗り(逆方向はしない、絶対値で)。
    """
    # 全体背景(濃いグレー)
    draw.rectangle([x, y, x + width, y + height], fill=(70, 75, 80))

    # 塗り部分
    fill_w = int(min(1.0, abs(ratio)) * width)
    if ratio > 0:
        draw.rectangle([x, y, x + fill_w, y + height], fill=BAR_FILL)
    elif ratio < 0:
        draw.rectangle([x, y, x + fill_w, y + height], fill=RED_COLOR)
    # ratio == 0 は背景のみ

    # 残りハッチング(目標画像風)
    if fill_w < width:
        for hx in range(x + fill_w, x + width, 14):
            draw.line(
                [(hx, y + height), (hx + height, y)],
                fill=BAR_HATCH,
                width=2,
            )


# ============== メイン描画 ==============


def render_daily_report(
    btc: ChainSummary,
    eth: ChainSummary,
    notable_lines: list[str] | None = None,
    now_jst: datetime | None = None,
) -> bytes:
    notable_lines = notable_lines or []

    # フォント読み込み
    title_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 50)
    date_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 36)
    section_label_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 26)
    big_num_font = ImageFont.truetype(_find_font(MONO_BOLD_CANDIDATES), 64)
    usd_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 24)
    card_title_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 22)
    cluster_label_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 22)
    cluster_num_font = ImageFont.truetype(_find_font(MONO_BOLD_CANDIDATES), 28)
    notable_title_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 28)
    notable_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 24)
    footer_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 20)

    img = Image.new("RGB", (WIDTH, MAX_HEIGHT), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    pad_x = 60
    y = 50

    # ============== タイトル ==============
    draw.text((pad_x, y), "ETF Custody Flow Report", font=title_font, fill=CYAN_COLOR)
    y += 70

    # 日付
    now = (now_jst or datetime.now(JST)).strftime("%Y-%m-%d %H:%M JST")
    draw.text((pad_x, y), f"{now} · 過去12時間", font=date_font, fill=TEXT_COLOR)
    y += 70

    # ============== 大型 Net Flow 2カラム ==============
    col_w = (WIDTH - pad_x * 2 - 40) // 2  # 中央 40px gap
    col_left_x = pad_x
    col_right_x = pad_x + col_w + 40

    y_net = y
    y = _draw_net_block(
        draw, col_left_x, y_net, col_w, "BTC ETF Net Flow", btc, "BTC",
        ORANGE_COLOR, section_label_font, big_num_font, usd_font,
    )
    _draw_net_block(
        draw, col_right_x, y_net, col_w, "ETH ETF Net Flow", eth, "ETH",
        PURPLE_COLOR, section_label_font, big_num_font, usd_font,
    )
    y += 30

    # ============== 発行体別カード(2カラム) ==============
    card_h = _calc_card_h(btc, eth, line_h=46, header_h=50, padding_v=24)
    _draw_cluster_card(
        draw, col_left_x, y, col_w, card_h, "BTC ETF・発行体別",
        btc, "BTC", card_title_font, cluster_label_font, cluster_num_font,
    )
    _draw_cluster_card(
        draw, col_right_x, y, col_w, card_h, "ETH ETF・発行体別",
        eth, "ETH", card_title_font, cluster_label_font, cluster_num_font,
    )
    y += card_h + 30

    # ============== AI 解説(Gemini)==============
    if notable_lines:
        _draw_star(draw, (pad_x + 12, y + 18), 14, GOLD_COLOR)
        draw.text((pad_x + 36, y), "AI解説", font=notable_title_font, fill=GOLD_COLOR)
        y += 50
        for line in notable_lines:
            text = line if line.startswith("・") else "・" + line
            draw.text((pad_x + 16, y), text, font=notable_font, fill=SUBTEXT_COLOR)
            y += 38
        y += 20
    else:
        y += 20

    # ============== フッター ==============
    y_footer = y + 20
    draw.line([(pad_x, y), (WIDTH - pad_x, y)], fill=DIVIDER_COLOR, width=1)
    draw.text((pad_x, y_footer), FOOTER_TEXT_LEFT, font=footer_font, fill=MUTED_COLOR)
    if FOOTER_TEXT_RIGHT:
        right_w = _text_w(draw, FOOTER_TEXT_RIGHT, footer_font)
        draw.text((WIDTH - pad_x - right_w, y_footer), FOOTER_TEXT_RIGHT, font=footer_font, fill=MUTED_COLOR)
    y = y_footer + 50

    # ============== トリミング ==============
    final_h = max(MIN_HEIGHT, min(y, MAX_HEIGHT))
    img = img.crop((0, 0, WIDTH, final_h))

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# -------- ヘルパー: Net ブロック --------
def _draw_net_block(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    label_text: str,
    summary: ChainSummary,
    unit: str,
    marker_color: tuple[int, int, int],
    label_font: ImageFont.FreeTypeFont,
    big_font: ImageFont.FreeTypeFont,
    usd_font: ImageFont.FreeTypeFont,
) -> int:
    # マーカー + ラベル
    _draw_circle(draw, (x + 12, y + 16), 12, marker_color)
    draw.text((x + 36, y), label_text, font=label_font, fill=SUBTEXT_COLOR)

    # 大型 Net 数値
    big_text = f"{_signed(summary.total_net_flow)} {unit}"
    big_color = _color_for_net(summary.total_net_flow)
    draw.text((x, y + 40), big_text, font=big_font, fill=big_color)

    # USD
    usd_text = f"≈ {_signed_usd(summary.net_flow_usd)}"
    draw.text((x, y + 115), usd_text, font=usd_font, fill=big_color)

    return y + 165


# -------- ヘルパー: クラスタ別カード --------
def _calc_card_h(btc: ChainSummary, eth: ChainSummary, line_h: int, header_h: int, padding_v: int) -> int:
    n = max(len(btc.clusters), len(eth.clusters), 1)
    return header_h + line_h * n + padding_v * 2


def _draw_cluster_card(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    title: str,
    summary: ChainSummary,
    unit: str,
    title_font: ImageFont.FreeTypeFont,
    label_font: ImageFont.FreeTypeFont,
    num_font: ImageFont.FreeTypeFont,
) -> None:
    _draw_rounded_rect(draw, (x, y, x + width, y + height), 12, CARD_COLOR)
    inner_pad = 22
    # タイトル
    draw.text((x + inner_pad, y + inner_pad), title, font=title_font, fill=SUBTEXT_COLOR)
    # 区切り
    draw.line(
        [(x + inner_pad, y + inner_pad + 32), (x + width - inner_pad, y + inner_pad + 32)],
        fill=DIVIDER_COLOR, width=1,
    )

    # 各クラスタ行
    line_y = y + inner_pad + 50

    # 最大絶対値で正規化(バーの長さに使う)
    max_abs = max((abs(c.net_flow) for c in summary.clusters), default=1)
    if max_abs == 0:
        max_abs = 1

    # 横並びレイアウト: [ラベル右寄せ] [Net 値左寄せ・色付き] [バー]
    label_w = 90       # IBIT/FBTC など 4文字想定
    net_w = 120        # +1,659 など想定
    gap = 14
    bar_left = x + inner_pad + label_w + gap + net_w + gap
    bar_right = x + width - inner_pad
    bar_w = bar_right - bar_left
    bar_h = 22

    for c in summary.clusters:
        # ラベル(右寄せ)
        lw = _text_w(draw, c.label, label_font)
        draw.text((x + inner_pad + label_w - lw, line_y + 6), c.label, font=label_font, fill=TEXT_COLOR)
        # 値(緑/赤)
        net_text = _signed(c.net_flow)
        net_color = _color_for_net(c.net_flow)
        nw = _text_w(draw, net_text, num_font)
        # 右寄せで Net を表示
        net_x_end = x + inner_pad + label_w + gap + net_w
        draw.text((net_x_end - nw, line_y), net_text, font=num_font, fill=net_color)
        # バー
        ratio = c.net_flow / max_abs
        _draw_progress_bar(
            draw,
            bar_left, line_y + 9,
            bar_w, bar_h,
            ratio,
        )
        line_y += 46


# ============== リアルタイム大口アラート用カード ==============

ALERT_W = 1200
ALERT_H = 675  # 16:9 — X large-image preview 比率


def render_alert_card(
    *,
    issuer: str,
    ticker: str,
    chain: str,            # "bitcoin" | "ethereum"
    amount: float,         # 符号付きネイティブ量 (＋=流入 / −=流出)
    unit: str,             # "BTC" | "ETH"
    price_usd: float,
    direction: str,        # "inflow" | "outflow"
    tx_hash: str,
    block_time: datetime,
    now_jst: datetime | None = None,
    flow_label: str = "",  # "取引所 Coinbase  →  BlackRock IBIT" 等。空なら発行体行にフォールバック。
) -> bytes:
    """単発の機関カストディ大口送金を 1 枚カードに描画して PNG bytes を返す。

    daily カードと同じパレット/フォント資産を流用。絵文字は使わず色付き図形で代替
    (Linux ランナーでも確実にレンダリングされる)。
    """
    is_inflow = amount >= 0
    accent = GREEN_COLOR if is_inflow else RED_COLOR
    chain_accent = ORANGE_COLOR if chain == "bitcoin" else PURPLE_COLOR
    dir_label = "INFLOW" if is_inflow else "OUTFLOW"
    dir_label_jp = "流入(預入)" if is_inflow else "流出(引出)"
    usd_value = amount * price_usd

    title_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 56)
    issuer_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 46)
    label_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 26)
    big_num_font = ImageFont.truetype(_find_font(MONO_BOLD_CANDIDATES), 78)
    usd_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 34)
    badge_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 30)
    pill_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 22)
    mono_font = ImageFont.truetype(_find_font(MONO_CANDIDATES), 24)
    footer_font = ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 20)

    img = Image.new("RGB", (ALERT_W, ALERT_H), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    pad_x = 60

    # ---- ヘッダ: 太いアクセントバー + 大きめタイトル(方向色で目立たせる)
    _draw_rounded_rect(draw, (pad_x, 36, pad_x + 10, 96), 5, accent)
    draw.text((pad_x + 28, 32), "大型送金速報", font=title_font, fill=CYAN_COLOR)

    # ---- チェーン pill (右上)
    chain_text = chain.upper()
    pill_pad = 18
    pill_tw = _text_w(draw, chain_text, pill_font)
    pill_w = pill_tw + pill_pad * 2
    pill_h = 40
    pill_x = ALERT_W - pad_x - pill_w
    pill_y = 44
    _draw_rounded_rect(draw, (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h), 20, CARD_COLOR)
    draw.text((pill_x + pill_pad, pill_y + 7), chain_text, font=pill_font, fill=chain_accent)

    # ---- 区切り
    draw.line([(pad_x, 108), (ALERT_W - pad_x, 108)], fill=DIVIDER_COLOR, width=1)

    # ---- 発行体行 or 送金経路(誰から誰へ)。flow_label があれば経路を見出しにする(冗長回避)。
    max_w = ALERT_W - pad_x * 2
    if flow_label:
        draw.text((pad_x, 120), "送金経路", font=ImageFont.truetype(_find_font(JP_REGULAR_CANDIDATES), 20), fill=MUTED_COLOR)
        # 幅に収まるようフォントサイズを段階的に縮小
        headline_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), 40)
        for size in (40, 36, 32, 28, 24):
            headline_font = ImageFont.truetype(_find_font(JP_BOLD_CANDIDATES), size)
            if _text_w(draw, flow_label, headline_font) <= max_w:
                break
        draw.text((pad_x, 150), flow_label, font=headline_font, fill=TEXT_COLOR)
    else:
        draw.text((pad_x, 138), f"{issuer} {ticker}".strip(), font=issuer_font, fill=TEXT_COLOR)

    # ---- 大型 金額ブロック (左) + 方向バッジ (右)
    draw.text((pad_x, 232), "送金量(主要wallet基準・推定)", font=label_font, fill=SUBTEXT_COLOR)
    big_text = f"{_signed(amount)} {unit}"
    draw.text((pad_x, 266), big_text, font=big_num_font, fill=accent)
    draw.text((pad_x, 372), f"≈ {_signed_usd(usd_value)}", font=usd_font, fill=accent)

    # 方向バッジ
    badge_tw = _text_w(draw, dir_label, badge_font)
    badge_pad = 26
    badge_w = badge_tw + badge_pad * 2
    badge_h = 60
    badge_x = ALERT_W - pad_x - badge_w
    badge_y = 250
    _draw_rounded_rect(draw, (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h), 14, accent)
    draw.text((badge_x + badge_pad, badge_y + 13), dir_label, font=badge_font, fill=BG_COLOR)
    jp_tw = _text_w(draw, dir_label_jp, label_font)
    draw.text((badge_x + badge_w - jp_tw, badge_y + badge_h + 12), dir_label_jp, font=label_font, fill=accent)

    # ---- 区切り
    draw.line([(pad_x, 452), (ALERT_W - pad_x, 452)], fill=DIVIDER_COLOR, width=1)

    # ---- Tx / 時刻
    short_tx = f"{tx_hash[:10]}…{tx_hash[-8:]}" if len(tx_hash) > 22 else tx_hash
    draw.text((pad_x, 480), "Tx", font=label_font, fill=MUTED_COLOR)
    draw.text((pad_x + 60, 482), short_tx, font=mono_font, fill=SUBTEXT_COLOR)

    ts_jst = block_time.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")
    draw.text((pad_x, 524), "検知時刻", font=label_font, fill=MUTED_COLOR)
    draw.text((pad_x + 120, 526), ts_jst, font=mono_font, fill=SUBTEXT_COLOR)

    # ---- フッター
    draw.line([(pad_x, ALERT_H - 64), (ALERT_W - pad_x, ALERT_H - 64)], fill=DIVIDER_COLOR, width=1)
    foot_left = "Powered by mempool.space + Etherscan · 推定値"
    draw.text((pad_x, ALERT_H - 44), foot_left, font=footer_font, fill=MUTED_COLOR)
    if FOOTER_TEXT_RIGHT:
        right_w = _text_w(draw, FOOTER_TEXT_RIGHT, footer_font)
        draw.text((ALERT_W - pad_x - right_w, ALERT_H - 44), FOOTER_TEXT_RIGHT, font=footer_font, fill=MUTED_COLOR)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
