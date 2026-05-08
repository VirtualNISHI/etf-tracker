"""ETF Custody フローを Gemini で日本語解説に変換。

旧来の Notable は notable.py の決定論的ルール(過去30日最大 / 連続日数)。
これを補完/置換するために、Gemini に BTC/ETH の発行体別フローを渡し、
1〜3行の自然言語解説を返す。

API キーが空 or 失敗時は空リストを返す → main 側は notable.py の出力に
フォールバックする。
"""
from __future__ import annotations

import logging

from src.flows import ChainSummary

log = logging.getLogger(__name__)

PROMPT_TEMPLATE = """\
あなたは仮想通貨 ETF のオンチェーンフロー解説者です。
以下は BTC/ETH スポット ETF のカストディアドレス群を観測した、過去24時間の発行体別フローです。
日本語で **2〜3 行**(各行 35〜55 文字、合計 200 字以内)、市場の特徴を解説してください。

【厳守】
- 出力は本文のみ。各行が 1 つの観点。改行で区切る。
- 各行の先頭に「・」を必ず付ける(箇条書き)
- ティッカー(IBIT, FBTC, BITB, GBTC, ETHA, FETH, ETHE)はそのまま
- 数値や「+/-」記号は省略せず使う
- 日本語の自然な口調(「〜が継続」「〜が目立つ」「〜が特徴」)
- 装飾(絵文字・見出し・「以下」「結論」等のメタ語)なし
- ETF 全体の動向と、特に大きく動いた発行体に言及

【BTC ETF(過去24h・発行体別)】
{btc_block}

合計: BTC Net Total = {btc_net} BTC ≈ {btc_usd}

【ETH ETF(過去24h・発行体別)】
{eth_block}

合計: ETH Net Total = {eth_net} ETH ≈ {eth_usd}
"""


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


def _block(summary: ChainSummary, unit: str) -> str:
    lines = []
    for c in summary.clusters:
        lines.append(
            f"  - {c.label}: {_signed(c.net_flow)} {unit} (in: {c.inflow:,.0f} / out: {c.outflow:,.0f})"
        )
    return "\n".join(lines)


def generate_ai_summary(
    btc: ChainSummary,
    eth: ChainSummary,
    *,
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
) -> list[str]:
    """Gemini で生成した日本語解説を箇条書きの行リストで返す。

    - 各行は先頭が「・」で始まる
    - 失敗時は [] を返す(呼び出し側で notable.py の決定論版にフォールバック)
    """
    if not api_key:
        log.info("gemini api key not set, skipping ai summary")
        return []

    try:
        from google import genai
    except ImportError:
        log.warning("google-genai not installed, skipping ai summary")
        return []

    prompt = PROMPT_TEMPLATE.format(
        btc_block=_block(btc, "BTC"),
        btc_net=_signed(btc.total_net_flow),
        btc_usd=_signed_usd(btc.net_flow_usd),
        eth_block=_block(eth, "ETH"),
        eth_net=_signed(eth.total_net_flow),
        eth_usd=_signed_usd(eth.net_flow_usd),
    )

    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model=model, contents=prompt)
        text = (resp.text or "").strip()
        # 行ごとに分割、先頭の "・" を保証
        lines: list[str] = []
        for raw in text.split("\n"):
            ln = raw.strip()
            if not ln:
                continue
            # 「- 」「* 」など他の bullet を「・」に正規化
            for prefix in ("- ", "* ", "• ", "・ "):
                if ln.startswith(prefix):
                    ln = "・" + ln[len(prefix):]
                    break
            if not ln.startswith("・"):
                ln = "・" + ln
            lines.append(ln)
        log.info(f"gemini ai summary: {len(lines)} lines, {len(text)} chars")
        return lines[:3]  # 最大3行
    except Exception as e:
        log.warning(f"gemini ai summary failed: {e}")
        return []
