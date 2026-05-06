"""Arkham から手動収集したアドレス一覧を検証して clusters.yaml に投入する。

Usage:
    # ファイルから読み込み(形式バリデーションのみ)
    uv run python scripts/ingest_addresses.py --input addresses_input.txt

    # 残高疎通確認も行う(BTC: mempool.space / ETH: Etherscan)
    uv run python scripts/ingest_addresses.py --input addresses_input.txt --probe

    # dry-run(yaml は書き換えず、結果を表示のみ)
    uv run python scripts/ingest_addresses.py --input addresses_input.txt --dry

Input format (テキスト):
    # coinbase_custody_btc
    bc1qxxxxxxxx
    bc1qyyyyyyyy

    # fidelity_btc
    bc1qzzzzzzzz
    3xxxxxxxxxxx

    # coinbase_custody_eth
    0xabc...

    # fidelity_eth
    0xdef...

ヘッダ "# <cluster_id>" の後ろが clusters.yaml の cluster id と一致する必要があります。
コメント行(空白除外して // または ; で始まる行も無視)、空行は無視。
末尾のコメント(アドレスの後ろ)も # 以降は捨てます。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import httpx
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CLUSTERS_PATH = ROOT / "config" / "clusters.yaml"
BACKUP_PATH = ROOT / "config" / "clusters.yaml.bak"

# 形式チェック(タイポ検出目的、checksum 検証はしない)
BTC_RE = re.compile(r"^(bc1[a-z0-9]{6,87}|[13][a-zA-HJ-NP-Z1-9]{25,34})$")
ETH_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

VALID_CLUSTERS = {
    "coinbase_custody_btc": "bitcoin",
    "fidelity_btc": "bitcoin",
    "coinbase_custody_eth": "ethereum",
    "fidelity_eth": "ethereum",
}


def parse_input(text: str) -> dict[str, list[str]]:
    """ヘッダ '# cluster_id' で区切られた入力をパース。"""
    result: dict[str, list[str]] = {cid: [] for cid in VALID_CLUSTERS}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 行末コメント除去 (アドレス hash # comment 形式)
        # ただし行頭の # ヘッダだけは別処理
        if line.startswith("#"):
            header = line.lstrip("#").strip()
            if header in VALID_CLUSTERS:
                current = header
            else:
                # 単なるコメント行
                pass
            continue
        if line.startswith("//") or line.startswith(";"):
            continue
        # 行末コメント
        if "#" in line:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
        if current is None:
            print(f"[WARN] アドレスがヘッダ前に出現: {line}", file=sys.stderr)
            continue
        result[current].append(line)
    return result


def validate_format(addr: str, chain: str) -> tuple[bool, str]:
    addr = addr.strip()
    if chain == "bitcoin":
        if BTC_RE.match(addr):
            return True, ""
        return False, "BTC形式不一致 (bc1.../1.../3...)"
    if chain == "ethereum":
        if ETH_RE.match(addr):
            return True, ""
        return False, "ETH形式不一致 (0x + 40 hex)"
    return False, f"unknown chain: {chain}"


async def probe_btc(client: httpx.AsyncClient, addr: str) -> tuple[bool, str]:
    """mempool.space で残高取得。"""
    try:
        r = await client.get(f"https://mempool.space/api/address/{addr}", timeout=10.0)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        d = r.json()
        funded = d["chain_stats"]["funded_txo_sum"]
        spent = d["chain_stats"]["spent_txo_sum"]
        bal_btc = (funded - spent) / 1e8
        tx_count = d["chain_stats"]["tx_count"]
        return True, f"{bal_btc:>10,.4f} BTC (tx: {tx_count:>5})"
    except Exception as e:
        return False, f"err: {e}"


async def probe_eth(client: httpx.AsyncClient, addr: str, api_key: str) -> tuple[bool, str]:
    """Etherscan V2 で残高取得 (chainid=1)。"""
    if not api_key or api_key.startswith("DRYRUN"):
        return False, "ETHERSCAN_API_KEY 未設定"
    try:
        r = await client.get(
            "https://api.etherscan.io/v2/api",
            params={
                "chainid": 1,
                "module": "account",
                "action": "balance",
                "address": addr,
                "tag": "latest",
                "apikey": api_key,
            },
            timeout=10.0,
        )
        d = r.json()
        if d.get("status") != "1":
            return False, f"err: {d.get('message')}"
        bal_eth = int(d["result"]) / 1e18
        return True, f"{bal_eth:>14,.4f} ETH"
    except Exception as e:
        return False, f"err: {e}"


async def run_probes(
    parsed: dict[str, list[str]], etherscan_key: str
) -> dict[str, list[tuple[str, bool, str]]]:
    """各アドレスを probe して (addr, ok, msg) のリストを返す。"""
    out: dict[str, list[tuple[str, bool, str]]] = {}
    async with httpx.AsyncClient() as client:
        for cid, addrs in parsed.items():
            chain = VALID_CLUSTERS[cid]
            results: list[tuple[str, bool, str]] = []
            for addr in addrs:
                if chain == "bitcoin":
                    ok, msg = await probe_btc(client, addr)
                else:
                    ok, msg = await probe_eth(client, addr, etherscan_key)
                results.append((addr, ok, msg))
                # mempool.space は緩いが連続バーストは避ける(.envと同じ100ms間隔)
                await asyncio.sleep(0.15)
            out[cid] = results
    return out


def write_clusters_yaml(parsed: dict[str, list[str]], dry: bool) -> None:
    """既存 clusters.yaml の addresses を更新して書き戻す。

    PyYAML はコメントを保存できないので、コメントは失われる(note フィールドは保たれる)。
    """
    with CLUSTERS_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    for chain_key in ("btc_clusters", "eth_clusters"):
        for cluster in data.get(chain_key, []):
            cid = cluster["id"]
            if cid in parsed:
                # 重複除外しつつ順序保持
                seen: set[str] = set()
                deduped: list[str] = []
                for a in parsed[cid]:
                    if a not in seen:
                        deduped.append(a)
                        seen.add(a)
                cluster["addresses"] = deduped

    new_yaml = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, indent=2)

    if dry:
        print("--- (dry-run) clusters.yaml の内容 ---")
        print(new_yaml)
        return

    # バックアップ
    if CLUSTERS_PATH.exists():
        BACKUP_PATH.write_text(CLUSTERS_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    CLUSTERS_PATH.write_text(new_yaml, encoding="utf-8")
    print(f"✅ {CLUSTERS_PATH} を更新しました(バックアップ: {BACKUP_PATH})")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", "-i", type=str, help="アドレス入力ファイル(無指定でstdin)")
    parser.add_argument("--probe", action="store_true", help="残高疎通確認(API実打ち)")
    parser.add_argument("--dry", action="store_true", help="yaml書き換えせず内容表示のみ")
    args = parser.parse_args()

    if args.input:
        text = Path(args.input).read_text(encoding="utf-8")
    else:
        print("(stdin から読み込み中... Ctrl+D / Ctrl+Z で終了)", file=sys.stderr)
        text = sys.stdin.read()

    parsed = parse_input(text)

    # 形式バリデーション
    print("=" * 70)
    print("形式バリデーション")
    print("=" * 70)
    total_ok = 0
    total_ng = 0
    cleaned: dict[str, list[str]] = {}
    for cid, addrs in parsed.items():
        chain = VALID_CLUSTERS[cid]
        ok_addrs: list[str] = []
        print(f"\n[{cid}] ({len(addrs)}件)")
        if not addrs:
            print("  (なし)")
            cleaned[cid] = []
            continue
        for addr in addrs:
            ok, reason = validate_format(addr, chain)
            mark = "✓" if ok else "✗"
            print(f"  {mark} {addr}  {reason}")
            if ok:
                ok_addrs.append(addr)
                total_ok += 1
            else:
                total_ng += 1
        cleaned[cid] = ok_addrs

    print(f"\n形式OK: {total_ok}件 / NG: {total_ng}件")
    if total_ng > 0:
        print("⚠ NG行を入力ファイルから取り除いてから再実行を推奨")

    # 残高疎通(オプション)
    if args.probe and total_ok > 0:
        load_dotenv(ROOT / ".env")
        etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
        print("\n" + "=" * 70)
        print("残高疎通確認(BTC: mempool.space / ETH: Etherscan)")
        print("=" * 70)
        probed = await run_probes(cleaned, etherscan_key)
        for cid, results in probed.items():
            if not results:
                continue
            print(f"\n[{cid}]")
            for addr, ok, msg in results:
                mark = "✓" if ok else "✗"
                print(f"  {mark} {addr}  {msg}")

    # yaml 書き戻し
    print("\n" + "=" * 70)
    write_clusters_yaml(cleaned, args.dry)


if __name__ == "__main__":
    asyncio.run(main())
