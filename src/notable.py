"""Notable section の自動文言生成(クラスタ単位)。"""
from __future__ import annotations

from src.config import NotableConfig
from src.db import get_recent_snapshots
from src.flows import ChainSummary, ClusterFlow


def _is_max_in_window(current: float, history: list[float]) -> bool:
    if not history:
        return False
    return abs(current) >= max(abs(h) for h in history)


def _consecutive_direction_days(history: list[float], current: float, min_days: int) -> int:
    seq = history + [current]
    if not seq or seq[-1] == 0:
        return 0
    sign = 1 if seq[-1] > 0 else -1
    count = 0
    for v in reversed(seq):
        if v == 0:
            break
        if (v > 0 and sign > 0) or (v < 0 and sign < 0):
            count += 1
        else:
            break
    return count if count >= min_days else 0


def _format_signed(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,.0f}"


def generate_notable_lines(
    btc: ChainSummary,
    eth: ChainSummary,
    cfg: NotableConfig,
) -> list[str]:
    lines: list[str] = []

    def check_cluster(cluster: ClusterFlow, threshold: float) -> None:
        if abs(cluster.net_flow) < threshold:
            return
        history = get_recent_snapshots(cluster.cluster_id, days=cfg.lookback_days)
        net_history = [h.net_flow for h in history]

        if _is_max_in_window(cluster.net_flow, net_history):
            direction = "流入" if cluster.net_flow > 0 else "流出"
            unit = "BTC" if cluster.chain == "bitcoin" else "ETH"
            lines.append(
                f"・{cluster.label} ({unit}) に過去{cfg.lookback_days}日最大の単日{direction}"
                f"({_format_signed(cluster.net_flow)} {unit})"
            )

        consec = _consecutive_direction_days(
            net_history, cluster.net_flow, cfg.consecutive_flow_min_days
        )
        if consec > 0:
            direction = "流入" if cluster.net_flow > 0 else "流出"
            unit = "BTC" if cluster.chain == "bitcoin" else "ETH"
            lines.append(f"・{cluster.label} ({unit}) は{consec}日連続で{direction}継続")

    for c in btc.clusters:
        check_cluster(c, cfg.significant_net_flow_btc)
    for c in eth.clusters:
        check_cluster(c, cfg.significant_net_flow_eth)

    if btc.notable_alert_txs:
        lines.append(f"・大口BTC tx を {len(btc.notable_alert_txs)} 件検知 → #etf-flow-alert")
    if eth.notable_alert_txs:
        lines.append(f"・大口ETH tx を {len(eth.notable_alert_txs)} 件検知 → #etf-flow-alert")

    return lines[:5]
