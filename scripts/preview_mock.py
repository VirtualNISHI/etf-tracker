"""画像のサンプルと同じ数値を ChainSummary に流し込み、
build_daily_embed の出力を確認する。

Usage:
    uv run python scripts/preview_mock.py            # JSON+テキスト出力のみ
    uv run python scripts/preview_mock.py --send     # .env の DISCORD_WEBHOOK_DAILY に送信
                                                       (DRY_RUN=true のままならログのみ)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_settings, load_thresholds  # noqa: E402
from src.flows import ChainSummary, ClusterFlow  # noqa: E402
from src.format_embed import build_daily_embed  # noqa: E402
from src.send_discord import send_embed  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")

# 画像から逆算した価格 (USD)
BTC_PRICE = 294_000_000 / 2_900  # ≒ $101,379
ETH_PRICE = 67_000_000 / 18_200  # ≒ $3,681


def build_mock_summaries() -> tuple[ChainSummary, ChainSummary, list[str], datetime]:
    btc = ChainSummary(
        chain="bitcoin",
        clusters=[
            ClusterFlow(
                cluster_id="coinbase_custody_btc",
                label="Coinbase Custody",
                chain="bitcoin",
                inflow=2_450,
                outflow=200,
                net_flow=2_250,
                tx_count=12,
            ),
            ClusterFlow(
                cluster_id="fidelity_btc",
                label="Fidelity",
                chain="bitcoin",
                inflow=650,
                outflow=0,
                net_flow=650,
                tx_count=4,
            ),
        ],
        native_price_usd=BTC_PRICE,
    )

    eth = ChainSummary(
        chain="ethereum",
        clusters=[
            ClusterFlow(
                cluster_id="coinbase_custody_eth",
                label="Coinbase Custody",
                chain="ethereum",
                inflow=12_000,
                outflow=0,
                net_flow=12_000,
                tx_count=18,
            ),
            ClusterFlow(
                cluster_id="fidelity_eth",
                label="Fidelity",
                chain="ethereum",
                inflow=6_200,
                outflow=0,
                net_flow=6_200,
                tx_count=9,
            ),
        ],
        native_price_usd=ETH_PRICE,
    )

    notable = [
        "・Coinbase Custody BTCに過去30日最大の単日流入",
        "・ETH側は3日連続で純流入継続",
        "・大口(1,000+ BTC)流入を1件検知 → #etf-flow-alert",
    ]

    fixed_now = datetime(2026, 5, 6, 9, 0, tzinfo=JST)
    return btc, eth, notable, fixed_now


def render_text(embed: dict) -> str:
    lines = [
        "=" * 60,
        f"TITLE: {embed['title']}",
        f"DESC : {embed['description']}",
        f"COLOR: {embed['color']}",
        "-" * 60,
    ]
    for f in embed["fields"]:
        lines.append(f"[{f['name']}]")
        lines.append(f["value"])
        lines.append("")
    if "footer" in embed:
        lines.append(f"FOOTER: {embed['footer']['text']}")
    lines.append("=" * 60)
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="Discord に送信(DRY_RUN尊重)")
    parser.add_argument("--json-out", type=str, help="JSON を書き出すパス")
    args = parser.parse_args()

    thresholds = load_thresholds()
    btc, eth, notable, now = build_mock_summaries()
    embed = build_daily_embed(btc, eth, notable, thresholds.embed, now_jst=now)

    print(render_text(embed))
    print()
    print("--- JSON ---")
    print(json.dumps(embed, ensure_ascii=False, indent=2))

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(embed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nWrote: {args.json_out}")

    if args.send:
        settings = load_settings()
        ok = await send_embed(settings.discord_webhook_daily, embed, dry_run=settings.dry_run)
        print(f"\nSend result: {ok} (dry_run={settings.dry_run})")


if __name__ == "__main__":
    asyncio.run(main())
