"""カウンターパーティ・ラベル解決(取引所 / ETF / 不明 / 内部移動)。

ToSクリーンの要(かなめ): この解決器は実行時に **一切ネットワークI/Oをしない**。
入力は clusters.yaml(自前ETF custody) と labels.yaml(operatorがofflineで構築した編集ラベル)のみ。
Nansen/Arkham は labels.yaml を作る offline 工程でのみ使う(実行時に第三者フィードを参照しない)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from loguru import logger

from src.config import ClusterBook, CounterpartyConfig, LabelBook


@dataclass(frozen=True)
class Label:
    kind: str  # 'exchange' | 'etf' | 'unknown' | 'internal'
    display: str  # 表示文字列(例: '取引所 Coinbase' / 'BlackRock IBIT' / '不明ウォレット (bc1q8x…r2l3)')
    name: str | None  # 確定エンティティ名(exchange/etf のみ)。集計の名寄せキーに使う。
    address: str | None  # 代表生アドレス(不明の短縮・監査用)


def _norm(chain: str, addr: str) -> str:
    # ETHはcase-insensitive(lower)、BTCはcase-sensitive(verbatim)。
    return addr.lower() if chain == "ethereum" else addr.strip()


def _short(chain: str, a: str) -> str:
    if chain == "bitcoin":
        return f"{a[:8]}…{a[-6:]}" if len(a) > 16 else a
    return f"{a[:6]}…{a[-4:]}" if len(a) > 12 else a


def _is_stale(as_of: str, max_age_days: int) -> bool:
    if not as_of:
        return False
    try:
        return (date.today() - date.fromisoformat(as_of)).days > max_age_days
    except ValueError:
        return False


class CounterpartyResolver:
    """clusters.yaml + labels.yaml から in-memory index を1回だけ構築し、O(1)で解決する。"""

    def __init__(self, clusters: ClusterBook, labels: LabelBook, cfg: CounterpartyConfig):
        self.cfg = cfg
        # (chain, norm_addr) -> (issuer, ticker, cluster_id, custodian)
        self.cluster_index: dict[tuple[str, str], tuple[str, str, str, str]] = {}
        for c in list(clusters.btc_clusters) + list(clusters.eth_clusters):
            for a in c.addresses:
                self.cluster_index[(c.chain, _norm(c.chain, a))] = (
                    c.issuer or c.label,
                    c.label,
                    c.id,
                    c.custodian,
                )
        # (chain, norm_addr) -> (name, type, as_of)
        self.exchange_index: dict[tuple[str, str], tuple[str, str, str]] = {}
        for e in labels.exchanges:
            for a in e.addresses:
                key = (e.chain, _norm(e.chain, a))
                if key in self.exchange_index or key in self.cluster_index:
                    logger.warning(f"labels.yaml dup/cluster-collision addr {a} ({e.name}); keeping first")
                    continue
                self.exchange_index[key] = (e.name, e.type, e.as_of)

    def cluster_of(self, chain: str, address: str) -> tuple[str, str, str, str] | None:
        """address が自前ETFクラスタなら (issuer, ticker, cluster_id, custodian)、なければ None。"""
        if address is None:
            return None
        return self.cluster_index.get((chain, _norm(chain, address)))

    def is_ours(self, chain: str, address: str | None) -> bool:
        return address is not None and (chain, _norm(chain, address)) in self.cluster_index

    def resolve(self, chain: str, address: str | None, firing_cluster_id: str, *, public: bool = False) -> Label:
        """1アドレスを表示用Labelに解決。lookup順: 自前ETF -> 取引所 -> 不明。"""
        if not address:
            return Label("unknown", "不明ウォレット", None, None)
        key = (chain, _norm(chain, address))

        # 1) 自前ETF(clusters.yaml)を最優先(自custodyの素性は generic な取引所タグに勝る)
        meta = self.cluster_index.get(key)
        if meta is not None:
            issuer, ticker, cid, _custodian = meta
            if cid == firing_cluster_id:
                return Label("internal", "内部移動", None, address)
            return Label("etf", f"{issuer} {ticker}", f"{issuer} {ticker}", address)

        # 2) 取引所/エンティティ(labels.yaml)
        ex = self.exchange_index.get(key)
        if ex is not None:
            name, etype, as_of = ex
            if public and _is_stale(as_of, self.cfg.label_max_age_days):
                # 古いラベルは公開Xでは出さない(誤ラベル防止)
                return Label("unknown", f"不明ウォレット ({_short(chain, address)})", None, address)
            prefix = "取引所" if etype == "exchange" else etype
            return Label("exchange", f"{prefix} {name}", name, address)

        # 3) フォールバック
        return Label("unknown", f"不明ウォレット ({_short(chain, address)})", None, address)


def build_flow_line(cp_display: str, our_label: str, direction: str, other_count: int = 0) -> str:
    """カード/キャプション共通の 送金経路 文字列。物理的に左=送信元、右=宛先。

    inflow  : counterparty -> our ETF
    outflow : our ETF -> counterparty
    """
    suffix = f" 他{other_count}件" if other_count > 0 else ""
    if direction == "inflow":
        return f"{cp_display}  →  {our_label}{suffix}"
    return f"{our_label}  →  {cp_display}{suffix}"
