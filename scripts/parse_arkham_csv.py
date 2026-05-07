"""Arkham からダウンロードした transactions CSV を解析し、
ラベルから cluster_id を自動判定して ingest_addresses.py 互換のテキスト形式で出力する。

Usage:
    # 単体の CSV を処理(プレビュー: 標準出力)
    uv run python scripts/parse_arkham_csv.py PATH_TO_CSV

    # 複数 CSV を一括処理
    uv run python scripts/parse_arkham_csv.py blackrock.csv grayscale.csv bitwise.csv

    # パイプで ingest_addresses.py に流す(yaml 投入 + 残高確認まで一発)
    uv run python scripts/parse_arkham_csv.py *.csv | uv run python scripts/ingest_addresses.py --probe

CSV カラム想定 (Arkham 形式):
    transactionHash, fromAddress, fromLabel, fromIsContract, toAddress, toLabel,
    toIsContract, ..., chain (= "bitcoin" or "ethereum")

ラベル → クラスタ判定ルール (RULES):
    "Fidelity FBTC..."         → fidelity_btc
    "Fidelity FETH..." / "Fidelity Ethereum ETF..." → fidelity_eth
    "Fidelity Custody..."      → chain で振り分け
    "BlackRock..."             → coinbase_custody_*  (BlackRock のカストディアンは Coinbase Custody)
    "Grayscale..."             → coinbase_custody_*
    "Bitwise..."               → coinbase_custody_*
    "Coinbase Prime: Custody Deposit..." → coinbase_custody_*  (ETF 用)

除外ルール (None):
    "Coinbase (xxxx)"          → 一般取引所 hot wallet (ETF custody ではない、ノイズ)
    "Coinbase Prime: Hot Wallet" → omnibus、ambiguous なので保留
    その他不明ラベル            → 警告して除外
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# (regex, mapping or None for explicit exclude)
# 上から順に評価、最初にマッチしたものを採用
# Phase 2: 発行体別 (ETF ticker) に振り分け
RULES: list[tuple[re.Pattern[str], Optional[dict[str, str]]]] = [
    # === BTC ETF 発行体別 ===
    # BlackRock IBIT
    (re.compile(r"BlackRock.*IBIT", re.I), {"bitcoin": "ibit"}),
    # Fidelity FBTC
    (re.compile(r"Fidelity FBTC", re.I), {"bitcoin": "fbtc"}),
    # Bitwise BITB / Core Bitcoin ETP も BITB に統合(同じ Bitwise BTC ETF カテゴリ)
    (re.compile(r"Bitwise.*(BITB|Core Bitcoin)", re.I), {"bitcoin": "bitb"}),
    # Grayscale BTC (Bitcoin Trust / Bitcoin Mini Trust 含む)
    (re.compile(r"Grayscale.*(Bitcoin|GBTC|BTC)", re.I), {"bitcoin": "gbtc"}),

    # === ETH ETF 発行体別 ===
    # BlackRock ETHA
    (re.compile(r"BlackRock.*ETHA", re.I), {"ethereum": "etha"}),
    # Fidelity FETH / Ethereum ETF
    (re.compile(r"Fidelity (FETH|Ethereum ETF)", re.I), {"ethereum": "feth"}),
    # Grayscale ETH (Ethereum Trust / Ethereum Mini Trust)
    (re.compile(r"Grayscale.*(Ethereum|ETHE|ETH)", re.I), {"ethereum": "ethe"}),

    # === Fidelity 汎用ラベル(ETF specific でない場合) ===
    # 「Fidelity Custody: Hot Wallet」のような omnibus はチェーン別に按分
    (re.compile(r"Fidelity Custody", re.I), {"bitcoin": "fbtc", "ethereum": "feth"}),
    (re.compile(r"\bFidelity\b", re.I), {"bitcoin": "fbtc", "ethereum": "feth"}),

    # === BlackRock / Bitwise / Grayscale の汎用フォールバック ===
    (re.compile(r"BlackRock", re.I), {"bitcoin": "ibit", "ethereum": "etha"}),
    (re.compile(r"Bitwise", re.I), {"bitcoin": "bitb"}),  # ETH の Bitwise は現状なし
    (re.compile(r"Grayscale", re.I), {"bitcoin": "gbtc", "ethereum": "ethe"}),

    # === Coinbase Prime 系すべて除外 ===
    # ETF カストディアンだが omnibus(複数 ETF の共有 wallet)で、特定の ETF に紐付け不能
    (re.compile(r"Coinbase Prime", re.I), None),
    # ARK Invest (ARKB の発行体) — 将来 CSV 提供されたら ARKB に振る
    # (re.compile(r"ARK Invest|ARK 21Shares|ARKB", re.I), {"bitcoin": "arkb"}),

    # === 除外ルール ===
    # 一般取引所 hot wallet
    (re.compile(r"^Coinbase \(", re.I), None),
    (re.compile(r"^Coinbase \d+", re.I), None),
    # omnibus(ETF と一般顧客の両方を抱えるので除外)
    (re.compile(r"Coinbase Prime:?\s*Hot Wallet", re.I), None),
    (re.compile(r"Coinbase:?\s*Hot Wallet", re.I), None),
    (re.compile(r"Coinbase:?\s*(Cold|Fees|Deposit\b)", re.I), None),
    # MEV builders(ETH の block builder、ETF と無関係)
    (re.compile(r"BuilderNet|Titan Builder|Quasar Builder|Beaver\s*Build|Flashbots Builder|rsync.builder|MEV Builder", re.I), None),
    # 既知のexchange/protocol
    (re.compile(r"Wintermute|Flow Trad", re.I), None),
]


def classify(label: str, chain: str) -> tuple[Optional[str], str]:
    """(cluster_id or None, reason) を返す。除外時は cluster_id=None。"""
    if not label:
        return None, "(empty label)"
    for pattern, mapping in RULES:
        if pattern.search(label):
            if mapping is None:
                return None, f"excluded by pattern: {pattern.pattern}"
            cluster = mapping.get(chain)
            if cluster:
                return cluster, f"matched: {pattern.pattern}"
            return None, f"matched {pattern.pattern} but no mapping for chain={chain}"
    # ラベルが自分のアドレス文字列そのもの(自己ラベル)= entity 内部のアドレス
    # この場合は CSV の row context 全体で判定すべきだが、ここでは保留扱い
    if re.match(r"^(bc1[a-z0-9]+|0x[a-fA-F0-9]{40}|[13][a-zA-HJ-NP-Z1-9]+)$", label):
        return None, "self-labeled (need context)"
    return None, "no rule matched"


def parse_csv(path: Path, verbose: bool = False) -> tuple[dict[str, set[str]], list[str]]:
    """CSV を読んで (cluster_id -> アドレス集合) と (除外/不明のwarning list) を返す。

    判定方針:
        1. 各 row の from/to 両側を classify
        2. 一方が明示的に ETF cluster にマッチ、他方が self-labeled / no-rule(除外パターンではない)
           なら、他方も同じ cluster に継承する(同一 tx 内で entity 関連アドレスとみなす)
        3. ただし他方が「明示的に excluded」(Coinbase 取引所 hot wallet 等)なら継承しない
    """
    by_cluster: dict[str, set[str]] = {}
    warnings: list[str] = []

    csv.field_size_limit(10_000_000)

    # delimiter auto-detect (tab if more tabs than commas in header line)
    with path.open("r", encoding="utf-8", newline="") as f:
        first_line = f.readline()
    delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row_num, row in enumerate(reader, start=2):
            chain = (row.get("chain") or "").strip().lower()

            sides: dict[str, dict] = {}
            for addr_col, label_col, side_name in [
                ("fromAddress", "fromLabel", "from"),
                ("toAddress", "toLabel", "to"),
            ]:
                raw_addrs = (row.get(addr_col) or "").strip().strip('"')
                label = (row.get(label_col) or "").strip()
                cluster, reason = classify(label, chain)
                sides[side_name] = {
                    "addrs": [a.strip() for a in raw_addrs.split(",") if a.strip()],
                    "label": label,
                    "cluster": cluster,
                    "reason": reason,
                }

            # 反対側継承: 一方が明示マッチ・他方が self-labeled / no-rule なら継承
            def can_inherit(side: dict) -> bool:
                if side["cluster"] is not None:
                    return False
                # 明示的に除外されたものは継承しない
                return "excluded" not in side["reason"]

            if sides["to"]["cluster"] and can_inherit(sides["from"]):
                sides["from"]["cluster"] = sides["to"]["cluster"]
                sides["from"]["reason"] = f"inherited from to ({sides['to']['cluster']})"
            elif sides["from"]["cluster"] and can_inherit(sides["to"]):
                sides["to"]["cluster"] = sides["from"]["cluster"]
                sides["to"]["reason"] = f"inherited from from ({sides['from']['cluster']})"

            # 各 side のアドレスをクラスタへ追加
            for side_name, side in sides.items():
                if side["cluster"] is None:
                    if verbose and side["label"]:
                        warnings.append(
                            f"  row{row_num} {side_name}: {side['label'][:60]:<60} → SKIP ({side['reason']})"
                        )
                    continue
                for addr in side["addrs"]:
                    by_cluster.setdefault(side["cluster"], set()).add(addr)
    return by_cluster, warnings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_files", nargs="+", type=Path)
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="除外/不明ラベルを stderr に列挙")
    args = parser.parse_args()

    # 各 CSV を処理してマージ
    merged: dict[str, set[str]] = {}
    all_warnings: list[str] = []
    for f in args.csv_files:
        if not f.exists():
            print(f"# ERROR: {f} not found", file=sys.stderr)
            sys.exit(1)
        print(f"# parsing: {f}", file=sys.stderr)
        result, warnings = parse_csv(f, verbose=args.verbose)
        for k, v in result.items():
            merged.setdefault(k, set()).update(v)
        all_warnings.extend([f"[{f.name}] {w}" for w in warnings])

    # 結果出力(ingest_addresses.py 互換形式) — Phase 2: 発行体別
    cluster_order = [
        "ibit", "fbtc", "bitb", "gbtc",
        "etha", "feth", "ethe",
    ]
    # 同一アドレスが複数 ETF に現れた場合、優先順位最初のクラスタに固定
    # (例: bc1qfse9t6... が fbtc と bitb の両方で見つかる時は fbtc 優先)
    seen: set[str] = set()
    dedupe_log: list[str] = []
    for cid in cluster_order:
        addrs = merged.get(cid, set())
        unique = addrs - seen
        dropped = addrs - unique
        if dropped:
            for a in dropped:
                dedupe_log.append(f"  {cid}: {a} (already in earlier cluster)")
        merged[cid] = unique
        seen.update(unique)

    total = 0
    for cid in cluster_order:
        addrs = merged.get(cid, set())
        if addrs:
            print(f"# {cid}")
            for a in sorted(addrs):
                print(a)
            print()
            total += len(addrs)

    if dedupe_log:
        print(f"\n# === dedupe (duplicates removed) ===", file=sys.stderr)
        for line in dedupe_log[:20]:
            print(line, file=sys.stderr)
        if len(dedupe_log) > 20:
            print(f"  ...({len(dedupe_log) - 20} more)", file=sys.stderr)

    # サマリ(stderr)
    print(f"\n# === summary ===", file=sys.stderr)
    for cid in cluster_order:
        addrs = merged.get(cid, set())
        print(f"#   {cid:<25} {len(addrs):>4} addresses", file=sys.stderr)
    print(f"#   total: {total}", file=sys.stderr)

    if args.verbose and all_warnings:
        print(f"\n# === excluded/skipped (verbose) ===", file=sys.stderr)
        for w in all_warnings[:50]:
            print(w, file=sys.stderr)
        if len(all_warnings) > 50:
            print(f"  ...({len(all_warnings) - 50} more)", file=sys.stderr)


if __name__ == "__main__":
    main()
