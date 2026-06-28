"""トランザクションのカウンターパーティ解決(誰から誰へ)。

BTCは UTXO 多入力多出力のため、正本tx(GET /tx/{txid})から cluster-net を1回だけ計算し、
所有権分類(D1-D5)とエンティティ別集計で送信元/宛先を1つに決める。誤りやすい点は全て保守側
(不明ウォレット / low_confidence)に倒す。ETHは from/to が1対1なので高信頼。

設計の出所: 多レンズ設計→敵対的検証→統合(workflow counterparty-label-design)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from loguru import logger

from src.clients.etherscan_client import ETHTransfer
from src.config import Cluster, CounterpartyConfig
from src.labels import CounterpartyResolver, _short


@dataclass
class Party:
    kind: str  # 'exchange' | 'etf' | 'unknown' | 'internal'
    display: str  # 表示(private/既定)。公開Xでは _public_cp_display() でゲート。
    name: str | None = None  # 確定エンティティ名(signal hint / 集計キー)
    address: str | None = None  # 代表生アドレス
    low_confidence: bool = False


@dataclass
class ResolvedTx:
    tx_hash: str
    chain: str  # 'bitcoin' | 'ethereum'
    cluster_id: str
    issuer: str
    ticker: str
    custodian: str
    amount: float  # 符号付きネイティブ(公開headline。流出=負)
    external_amount: float  # |外部payment合計|(cluster-netと乖離時の代替headline)
    direction: str  # 'inflow' | 'outflow' | 'internal'
    is_internal: bool
    block_time: datetime
    counterparty: Party  # inflowなら送信元 / outflowなら宛先
    our_side: Party  # 常に発火中の自ETF
    other_count: int = 0  # 「他N件」表示用


# ---------- 共通ヘルパ ----------

def _party_from_address(
    resolver: CounterpartyResolver, chain: str, addr: str, firing_id: str
) -> Party:
    """単一アドレスを Party 化。stale ラベルは public で不明落ち=low_confidence印にする。"""
    priv = resolver.resolve(chain, addr, firing_id, public=False)
    pub = resolver.resolve(chain, addr, firing_id, public=True)
    low = priv.kind != pub.kind  # public でラベルが落ちる(=stale)なら公開時は名指ししない
    return Party(priv.kind, priv.display, priv.name, addr, low)


def _pick_external_counterparty(
    resolver: CounterpartyResolver,
    chain: str,
    firing: Cluster,
    legs: list[tuple[str, int]],  # (address, value_sat_or_wei) 外部脚
    direction: str,
    cfg: CounterpartyConfig,
) -> tuple[Party, int, int]:
    """外部脚を「解決エンティティ単位」で集計し、代表カウンターパーティを1つ選ぶ。

    returns (Party, external_value, other_count)。
    UTXO単体ではなくエンティティ価値合計で選ぶ(統合多数UTXOの名寄せ崩れ対策)。
    """
    total = sum(v for _, v in legs)
    if not legs or total <= 0:
        return Party("unknown", "不明ウォレット", None, None, True), 0, 0

    distinct_addrs = {a for a, _ in legs}
    # バケット: name(確定) または '__unknown__'。値合計 + 代表(最大)アドレスを保持。
    buckets: dict[str, dict] = {}
    named_entities: set[str] = set()
    for a, v in legs:
        lbl = resolver.resolve(chain, a, firing.id, public=False)
        key = lbl.name if (lbl.kind in ("exchange", "etf") and lbl.name) else "__unknown__"
        b = buckets.setdefault(key, {"value": 0, "rep_addr": a, "rep_val": -1, "kind": lbl.kind})
        b["value"] += v
        if v > b["rep_val"]:
            b["rep_val"] = v
            b["rep_addr"] = a
        if key != "__unknown__":
            named_entities.add(key)

    # ファンアウト: 受取先が多すぎ/別エンティティ複数 → 1つに名寄せしない
    if len(distinct_addrs) >= cfg.batch_fanout_max or len(named_entities) >= 2:
        return (
            Party("unknown", f"複数アドレスへ分散 ({len(distinct_addrs)}件)", None, None, True),
            total,
            0,
        )

    # 最大バケット
    win_key = max(buckets, key=lambda k: buckets[k]["value"])
    win = buckets[win_key]
    share = win["value"] / total if total else 0.0

    if win_key != "__unknown__" and share >= cfg.dominance_ratio:
        cp = _party_from_address(resolver, chain, win["rep_addr"], firing.id)
        # 単独支配でなければ他件数を付す
        win_addrs = {a for a, _ in legs if (resolver.resolve(chain, a, firing.id).name == win_key)}
        other_count = len(distinct_addrs) - len(win_addrs)
        return cp, total, max(0, other_count)

    # フラグメント/不明優勢 → 不明ウォレット(最大外部脚を代表に)
    rep = max(legs, key=lambda kv: kv[1])[0]
    return Party("unknown", f"不明ウォレット ({_short(chain, rep)})", None, rep, True), total, 0


def _our_side(firing: Cluster) -> Party:
    return Party("etf", f"{firing.issuer or firing.label} {firing.label}", f"{firing.issuer} {firing.label}", None, False)


# ---------- BTC ----------

def resolve_btc(
    raw_tx: dict,
    firing: Cluster,
    resolver: CounterpartyResolver,
    cfg: CounterpartyConfig,
) -> ResolvedTx | None:
    """正本raw_txから (tx, firing cluster) を1回だけ解決。None=純net0(発火対象外)。"""
    chain = "bitcoin"
    OURS = set(firing.addresses)  # case-sensitive

    IN: list[tuple[str, int]] = []
    for vin in raw_tx.get("vin", []):
        pv = vin.get("prevout") or {}
        a = pv.get("scriptpubkey_address")
        if a is None:  # coinbase / 解析不能 prevout
            continue
        IN.append((a, int(pv.get("value", 0))))
    OUT: list[tuple[str, int]] = []
    for vout in raw_tx.get("vout", []):
        a = vout.get("scriptpubkey_address")
        if a is None:  # OP_RETURN / nulldata
            continue
        OUT.append((a, int(vout.get("value", 0))))

    our_in = sum(v for a, v in IN if a in OURS)
    our_out = sum(v for a, v in OUT if a in OURS)
    net_sat = our_out - our_in
    amount = net_sat / 1e8

    block_time = datetime.fromtimestamp(
        int(raw_tx.get("status", {}).get("block_time", 0)), tz=timezone.utc
    )

    def mk(direction: str, is_internal: bool, cp: Party, ext_sat: int, other: int = 0) -> ResolvedTx:
        return ResolvedTx(
            tx_hash=raw_tx.get("txid", ""),
            chain=chain,
            cluster_id=firing.id,
            issuer=firing.issuer or firing.label,
            ticker=firing.label,
            custodian=firing.custodian,
            amount=amount,
            external_amount=ext_sat / 1e8,
            direction=direction,
            is_internal=is_internal,
            block_time=block_time,
            counterparty=cp,
            our_side=_our_side(firing),
            other_count=other,
        )

    is_all_ours = lambda a: resolver.is_ours(chain, a)
    all_in_ours = len(IN) >= 1 and all(is_all_ours(a) for a, _ in IN)
    some_in_ours = any(is_all_ours(a) for a, _ in IN)
    some_in_foreign = any(not is_all_ours(a) for a, _ in IN)
    mixed_inputs = some_in_ours and some_in_foreign

    # D2: 混在入力(CoinJoin/co-spend) → 方向問わず不明・low_confidence(公開X抑止)
    if mixed_inputs:
        if net_sat == 0:
            return None
        rep = max((kv for kv in IN if not is_all_ours(kv[0])), key=lambda kv: kv[1], default=(None, 0))[0]
        cp = Party("unknown", f"不明ウォレット ({_short(chain, rep)})" if rep else "不明ウォレット", None, rep, True)
        return mk("inflow" if net_sat > 0 else "outflow", False, cp, abs(net_sat))

    all_out_ours = len(OUT) >= 1 and all(is_all_ours(a) for a, _ in OUT)

    # D1: 入出力とも全て自前ユニバース → intra-universe(内部 or cross-ETF)
    if all_in_ours and all_out_ours:
        sibling_out = [(a, v) for a, v in OUT if a not in OURS]  # 他クラスタ宛(自cluster戻り=change)
        if not sibling_out:
            cp = Party("internal", "内部移動", None, None, False)
            return mk("internal", True, cp, 0)
        fcust = firing.custodian
        sib_custs = []
        for a, _ in sibling_out:
            meta = resolver.cluster_of(chain, a)
            sib_custs.append(meta[3] if meta else "")
        if fcust and all(sc == fcust for sc in sib_custs):
            cp = Party("internal", "内部移動", None, None, False)
            return mk("internal", True, cp, 0)
        # 異custody間 ETF->ETF: 実移動として発火
        cp, ext, other = _pick_external_counterparty(resolver, chain, firing, sibling_out, "outflow", cfg)
        return mk("outflow", False, cp, ext, other)

    if net_sat == 0:
        return None

    # D3: INFLOW(送信元=外部入力)
    if net_sat > 0:
        foreign_in = [(a, v) for a, v in IN if a not in OURS]
        if not foreign_in:
            return None
        cp, ext, other = _pick_external_counterparty(resolver, chain, firing, foreign_in, "inflow", cfg)
        return mk("inflow", False, cp, ext, other)

    # D4: OUTFLOW(宛先=自cluster戻り以外の出力)。同custodyのsibling出力はchange扱いでdrop。
    if net_sat < 0:
        payment: list[tuple[str, int]] = []
        for a, v in OUT:
            if a in OURS:
                continue  # firing clusterへのchange
            meta = resolver.cluster_of(chain, a)
            if meta and meta[2] != firing.id and firing.custodian and meta[3] == firing.custodian:
                continue  # 同custodyのsibling = 内部change、宛先ではない
            payment.append((a, v))
        if not payment:
            cp = Party("internal", "内部移動", None, None, False)
            return mk("internal", True, cp, 0)
        cp, ext, other = _pick_external_counterparty(resolver, chain, firing, payment, "outflow", cfg)
        return mk("outflow", False, cp, ext, other)

    # D5: 想定外の組み合わせ → 保守的に不明(発火は止めない)
    cp = Party("unknown", "不明ウォレット", None, None, True)
    return mk("inflow" if net_sat > 0 else "outflow", False, cp, abs(net_sat))


# ---------- ETH ----------

def resolve_eth(
    legs: list[ETHTransfer],
    firing: Cluster,
    resolver: CounterpartyResolver,
    cfg: CounterpartyConfig,
) -> ResolvedTx | None:
    """1 tx_hash 分の firing cluster 脚をまとめて解決。from/toは1対1なので高信頼。"""
    chain = "ethereum"
    if not legs:
        return None
    net = sum(t.amount_eth for t in legs)
    from_l = (legs[0].from_addr or "").lower()
    to_l = (legs[0].to_addr or "").lower()
    block_time = legs[0].block_time
    OURS = {a.lower() for a in firing.addresses}

    def mk(direction: str, is_internal: bool, cp: Party) -> ResolvedTx:
        return ResolvedTx(
            tx_hash=legs[0].tx_hash,
            chain=chain,
            cluster_id=firing.id,
            issuer=firing.issuer or firing.label,
            ticker=firing.label,
            custodian=firing.custodian,
            amount=net,
            external_amount=abs(net),
            direction=direction,
            is_internal=is_internal,
            block_time=block_time,
            counterparty=cp,
            our_side=_our_side(firing),
            other_count=0,
        )

    from_ours_uni = resolver.is_ours(chain, from_l)
    to_ours_uni = resolver.is_ours(chain, to_l)

    # 両端が自前ユニバース → intra-universe
    if from_ours_uni and to_ours_uni:
        other_addr = to_l if from_l in OURS else from_l
        meta = resolver.cluster_of(chain, other_addr)
        other_cid = meta[2] if meta else firing.id
        other_cust = meta[3] if meta else ""
        if other_cid == firing.id or (firing.custodian and other_cust == firing.custodian):
            return mk("internal", True, Party("internal", "内部移動", None, None, False))
        # 異custody ETF->ETF
        direction = "inflow" if net > 0 else "outflow"
        cp = _party_from_address(resolver, chain, other_addr, firing.id)
        return mk(direction, False, cp)

    if net == 0:
        return None

    if to_l in OURS and not from_ours_uni:
        return mk("inflow", False, _party_from_address(resolver, chain, from_l, firing.id))
    if from_l in OURS and not to_ours_uni:
        return mk("outflow", False, _party_from_address(resolver, chain, to_l, firing.id))

    logger.debug(f"resolve_eth: undecidable tx {legs[0].tx_hash[:12]} from={from_l[:10]} to={to_l[:10]}")
    return None


def public_cp_display(party: Party, chain: str) -> str:
    """公開X用の表示。確定名でも low_confidence(stale/曖昧)なら不明に落とす。"""
    if party.kind in ("exchange", "etf") and party.low_confidence:
        return f"不明ウォレット ({_short(chain, party.address)})" if party.address else "不明ウォレット"
    return party.display
