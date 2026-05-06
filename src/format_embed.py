"""Discord Embed整形(B案: クラスタ単位表示)。"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.config import EmbedConfig
from src.flows import ChainSummary

JST = ZoneInfo("Asia/Tokyo")


def _signed_int(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.0f}"


def _cluster_lines(summary: ChainSummary, unit: str) -> str:
    """クラスタ別の "Label  +X UNIT (in: A / out: B)" 行を生成。"""
    if not summary.clusters:
        return "(データなし)"

    rows: list[str] = []
    for c in summary.clusters:
        net = _signed_int(c.net_flow)
        rows.append(
            f"`{c.label:<18} {net:>10} {unit}` (in: {c.inflow:,.0f} / out: {c.outflow:,.0f})"
        )

    # Net Total
    total_usd_m = summary.net_flow_usd / 1_000_000
    sign = "+" if total_usd_m >= 0 else ""
    rows.append(
        f"`{'Net Total':<18} {_signed_int(summary.total_net_flow):>10} {unit}` "
        f"≒ {sign}${total_usd_m:,.0f}M"
    )
    return "\n".join(rows)


def build_daily_embed(
    btc: ChainSummary,
    eth: ChainSummary,
    notable_lines: list[str],
    cfg: EmbedConfig,
    now_jst: datetime | None = None,
) -> dict[str, Any]:
    now = (now_jst or datetime.now(JST)).strftime("%Y-%m-%d %H:%M JST")

    fields: list[dict[str, Any]] = [
        {
            "name": "🟠 BTC ETF Custody",
            "value": _cluster_lines(btc, "BTC"),
            "inline": False,
        },
        {
            "name": "🟣 ETH ETF Custody",
            "value": _cluster_lines(eth, "ETH"),
            "inline": False,
        },
    ]

    if notable_lines:
        fields.append(
            {
                "name": "📌 Notable",
                "value": "\n".join(notable_lines),
                "inline": False,
            }
        )

    return {
        "title": "📊 ETF Custody Flow Report",
        "description": f"**{now}** · 過去24時間",
        "color": cfg.color_btc,
        "fields": fields,
        "timestamp": datetime.utcnow().isoformat(),
    }


def build_alert_embed(
    cluster_label: str,
    chain: str,
    amount: float,
    direction: str,  # 'inflow' or 'outflow'
    tx_hash: str,
    cfg: EmbedConfig,
) -> dict[str, Any]:
    unit = "BTC" if chain == "bitcoin" else "ETH"
    arrow = "🟢 流入" if direction == "inflow" else "🔴 流出"
    explorer_url = (
        f"https://mempool.space/tx/{tx_hash}"
        if chain == "bitcoin"
        else f"https://etherscan.io/tx/{tx_hash}"
    )
    return {
        "title": f"⚡ 大口検知: {cluster_label}",
        "description": f"{arrow} **{abs(amount):,.0f} {unit}**",
        "color": cfg.color_alert,
        "fields": [
            {"name": "Cluster", "value": cluster_label, "inline": True},
            {"name": "Chain", "value": chain, "inline": True},
            {"name": "Tx", "value": f"[Explorer]({explorer_url})", "inline": False},
        ],
        "timestamp": datetime.utcnow().isoformat(),
    }
