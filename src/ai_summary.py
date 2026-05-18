"""ETF Custody フローを LLM で日本語解説に変換。

旧来の Notable は notable.py の決定論的ルール (過去30日最大 / 連続日数)。
これを補完/置換するために、LLM に BTC/ETH の発行体別フローを渡し、
1〜3行の自然言語解説を返す。

フォールバック順: Gemini → OpenAI → Grok (jp_translator 経由)
全段失敗 or 全 API キー未設定なら空リストを返す → main 側は notable.py の
決定論版にフォールバックする。
"""
from __future__ import annotations

import logging

from src.flows import ChainSummary
from src.jp_translator import generate

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
あなたは仮想通貨 ETF のオンチェーンフロー解説者です。
入力として与えられる BTC/ETH スポット ETF の発行体別フローを元に、
日本語で **2〜3 行** (各行 35〜55 文字、合計 200 字以内)、市場の特徴を
解説してください。

【厳守】
- 出力は本文のみ。各行が 1 つの観点。改行で区切る。
- 各行の先頭に「・」を必ず付ける (箇条書き)
- ティッカー (IBIT, FBTC, BITB, GBTC, ETHA, FETH, ETHE) はそのまま
- 数値や「+/-」記号は省略せず使う
- 日本語の自然な口調 (「〜が継続」「〜が目立つ」「〜が特徴」)
- 装飾 (絵文字・見出し・「以下」「結論」等のメタ語) なし
- ETF 全体の動向と、特に大きく動いた発行体に言及
"""

USER_TEMPLATE = """\
【BTC ETF (過去12h・発行体別)】
{btc_block}

合計: BTC Net Total = {btc_net} BTC ≈ {btc_usd}

【ETH ETF (過去12h・発行体別)】
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
    api_key: str = "",  # legacy: Gemini key. Now optional, jp_translator reads env.
    model: str | None = None,  # legacy: ignored (jp_translator picks per provider).
    gemini_api_key: str | None = None,
    openai_api_key: str | None = None,
    xai_api_key: str | None = None,
) -> list[str]:
    """LLM で生成した日本語解説を箇条書きの行リストで返す。

    フォールバック: Gemini → OpenAI → Grok (DeepL は生成不可なので除外)。

    - 各行は先頭が「・」で始まる
    - 全段失敗時は [] を返す (呼び出し側で notable.py の決定論版にフォールバック)
    - ``api_key`` (旧 Gemini 専用) は後方互換のため受け取り、未指定の
      ``gemini_api_key`` に転送する
    """
    # Backward compat: old call sites pass api_key= as the Gemini key.
    if api_key and not gemini_api_key:
        gemini_api_key = api_key

    user = USER_TEMPLATE.format(
        btc_block=_block(btc, "BTC"),
        btc_net=_signed(btc.total_net_flow),
        btc_usd=_signed_usd(btc.net_flow_usd),
        eth_block=_block(eth, "ETH"),
        eth_net=_signed(eth.total_net_flow),
        eth_usd=_signed_usd(eth.net_flow_usd),
    )

    text = generate(
        system=SYSTEM_PROMPT,
        user=user,
        max_tokens=600,
        temperature=0.3,
        gemini_api_key=gemini_api_key,
        openai_api_key=openai_api_key,
        xai_api_key=xai_api_key,
    )

    if not text:
        log.info("ai summary: jp_translator chain returned None")
        return []

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
    log.info("ai summary: %d lines, %d chars", len(lines), len(text))
    return lines[:3]  # 最大3行
