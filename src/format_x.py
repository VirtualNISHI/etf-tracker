"""X (Twitter) 用の短縮テキスト生成。

Discord の embed と違って 280 文字制限があるため、要点だけを抽出。
レイアウト方針:
    📊 ETF Custody Flow Report
    YYYY-MM-DD HH:MM JST · 過去24h

    🟠 BTC: +X,XXX BTC (+$XXM)
    🟣 ETH: +XX,XXX ETH (+$XM)

    📌 Notable行(あれば最大2行)

    #BTC #ETH #ETF #SmartMoney
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.flows import ChainSummary

JST = ZoneInfo("Asia/Tokyo")
HASHTAGS = "#BTC #ETH #ETF #SmartMoney"
MAX_LEN = 280


def _signed_native(v: float, unit: str) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.0f} {unit}"


def _signed_usd(v: float) -> str:
    m = v / 1_000_000
    sign = "+" if m >= 0 else ""
    if abs(m) >= 1:
        return f"{sign}${m:,.0f}M"
    # $1M 未満は K 表記
    k = v / 1_000
    return f"{sign}${k:,.0f}K"


def build_daily_text(
    btc: ChainSummary,
    eth: ChainSummary,
    notable_lines: list[str] | None = None,
    now_jst: datetime | None = None,
) -> str:
    now = (now_jst or datetime.now(JST)).strftime("%Y-%m-%d %H:%M JST")

    lines = [
        "📊 ETF Custody Flow Report",
        f"{now} · 過去12h",
        "",
        f"🟠 BTC: {_signed_native(btc.total_net_flow, 'BTC')} ({_signed_usd(btc.net_flow_usd)})",
        f"🟣 ETH: {_signed_native(eth.total_net_flow, 'ETH')} ({_signed_usd(eth.net_flow_usd)})",
    ]

    # Notable は文字数に余裕があれば最大2行追加(先頭の "・" は X では普通の中点 "・")
    if notable_lines:
        candidate_lines = list(lines) + [""]
        for nl in notable_lines[:2]:
            test = "\n".join(candidate_lines + [nl] + ["", HASHTAGS])
            if len(test) <= MAX_LEN:
                candidate_lines.append(nl)
            else:
                break
        if len(candidate_lines) > len(lines) + 1:
            lines = candidate_lines

    lines.append("")
    lines.append(HASHTAGS)
    text = "\n".join(lines)

    # 万一の過長対策(本来はここまで来ない)
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 3] + "..."

    return text


def build_alert_text(
    *,
    issuer: str,
    ticker: str,
    amount: float,        # 符号付きネイティブ量 (＋=流入 / −=流出)
    unit: str,            # "BTC" | "ETH"
    price_usd: float,
    block_time: datetime,
    flow_line: str = "",  # "取引所 Coinbase → BlackRock IBIT" 等。空なら発行体行にフォールバック。
) -> str:
    """単発大口アラート用の ≤280 字キャプション。

    画像カードに詳細があるので、ここはタイトル・要点・ハッシュタグのみ。
    """
    is_inflow = amount >= 0
    dir_jp = "流入(預入)" if is_inflow else "流出(引出)"
    usd_value = amount * price_usd
    ts = block_time.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")

    # 発行体名をハッシュタグ化 (英数のみ、スペース除去)
    issuer_tag = "#" + "".join(ch for ch in issuer if ch.isalnum()) if issuer else ""
    tags = " ".join(t for t in [f"#{unit}", "#ETF", issuer_tag, "#SmartMoney"] if t)

    headline = flow_line if flow_line else f"{issuer} {ticker}".strip()
    lines = [
        "🐋 機関カストディ 大口検知",
        headline,
        "",
        f"{_signed_native(amount, unit)} ({_signed_usd(usd_value)}) {dir_jp}",
        ts,
        "",
        tags,
    ]
    text = "\n".join(lines)
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 3] + "..."
    return text
