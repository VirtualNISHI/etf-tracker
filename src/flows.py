"""クラスタ別フロー集計。

各クラスタについて、過去24時間の inflow/outflow/net を計算する。
クラスタ内アドレス間の自己内移動は重複除外。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.clients.coingecko_client import CoinGeckoClient
from src.clients.etherscan_client import EtherscanClient, ETHTransfer
from src.clients.mempool_client import BTCTransfer, MempoolClient
from src.config import Cluster


@dataclass
class ClusterFlow:
    cluster_id: str
    label: str
    chain: str
    inflow: float
    outflow: float
    net_flow: float
    tx_count: int

    @property
    def is_estimated(self) -> bool:
        return True  # B案では常に推定値


@dataclass
class ChainSummary:
    chain: str
    clusters: list[ClusterFlow]
    native_price_usd: float
    notable_alert_txs: list[str] = field(default_factory=list)

    @property
    def total_net_flow(self) -> float:
        return sum(c.net_flow for c in self.clusters)

    @property
    def total_inflow(self) -> float:
        return sum(c.inflow for c in self.clusters)

    @property
    def total_outflow(self) -> float:
        return sum(c.outflow for c in self.clusters)

    @property
    def net_flow_usd(self) -> float:
        return self.total_net_flow * self.native_price_usd


def _aggregate_btc(transfers: list[BTCTransfer], cluster_addrs: set[str]) -> tuple[float, float, int]:
    """BTC: クラスタ内自己移動を除外して集計。"""
    seen_tx: set[str] = set()
    inflow = 0.0
    outflow = 0.0

    # tx_hashで重複排除しつつ、各txの「クラスタ外との純フロー」を計算
    by_tx: dict[str, list[BTCTransfer]] = {}
    for t in transfers:
        by_tx.setdefault(t.tx_hash, []).append(t)

    for tx_hash, group in by_tx.items():
        if tx_hash in seen_tx:
            continue
        seen_tx.add(tx_hash)
        # クラスタ内のアドレスが関与する金額を相殺
        net_for_cluster = sum(t.amount_btc for t in group)
        if net_for_cluster > 0:
            inflow += net_for_cluster
        elif net_for_cluster < 0:
            outflow += abs(net_for_cluster)

    return inflow, outflow, len(seen_tx)


def _aggregate_eth(transfers: list[ETHTransfer]) -> tuple[float, float, int]:
    """ETH: 同じtx_hashが複数アドレスで現れたら相殺。"""
    by_tx: dict[str, list[ETHTransfer]] = {}
    for t in transfers:
        by_tx.setdefault(t.tx_hash, []).append(t)

    inflow = 0.0
    outflow = 0.0
    for group in by_tx.values():
        net = sum(t.amount_eth for t in group)
        if net > 0:
            inflow += net
        elif net < 0:
            outflow += abs(net)
    return inflow, outflow, len(by_tx)


async def collect_btc_clusters(
    client: MempoolClient,
    clusters: list[Cluster],
    period_hours: int,
) -> list[ClusterFlow]:
    since = int((datetime.now(timezone.utc) - timedelta(hours=period_hours)).timestamp())
    flows: list[ClusterFlow] = []
    for cluster in clusters:
        if not cluster.addresses:
            flows.append(ClusterFlow(cluster.id, cluster.label, cluster.chain, 0, 0, 0, 0))
            continue
        transfers = await client.get_cluster_transfers(cluster.addresses, since)
        inflow, outflow, tx_count = _aggregate_btc(transfers, set(cluster.addresses))
        flows.append(
            ClusterFlow(
                cluster_id=cluster.id,
                label=cluster.label,
                chain=cluster.chain,
                inflow=inflow,
                outflow=outflow,
                net_flow=inflow - outflow,
                tx_count=tx_count,
            )
        )
    return flows


async def collect_eth_clusters(
    client: EtherscanClient,
    clusters: list[Cluster],
    period_hours: int,
) -> list[ClusterFlow]:
    since = int((datetime.now(timezone.utc) - timedelta(hours=period_hours)).timestamp())
    flows: list[ClusterFlow] = []
    for cluster in clusters:
        if not cluster.addresses:
            flows.append(ClusterFlow(cluster.id, cluster.label, cluster.chain, 0, 0, 0, 0))
            continue
        transfers = await client.get_cluster_transfers(cluster.addresses, since)
        inflow, outflow, tx_count = _aggregate_eth(transfers)
        flows.append(
            ClusterFlow(
                cluster_id=cluster.id,
                label=cluster.label,
                chain=cluster.chain,
                inflow=inflow,
                outflow=outflow,
                net_flow=inflow - outflow,
                tx_count=tx_count,
            )
        )
    return flows


def sort_by_display_order(flows: list[ClusterFlow], order: list[str]) -> list[ClusterFlow]:
    order_map = {cid: i for i, cid in enumerate(order)}
    return sorted(flows, key=lambda f: order_map.get(f.cluster_id, 999))


async def fetch_prices(client: CoinGeckoClient) -> dict[str, float]:
    return await client.get_prices_usd(["bitcoin", "ethereum"])
