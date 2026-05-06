"""エントリポイント。

使い方:
    uv run python -m src.main             # 常駐
    uv run python -m src.main --once      # 定期配信を1回実行
    uv run python -m src.main --alert-once
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from src.clients.coingecko_client import CoinGeckoClient
from src.clients.etherscan_client import EtherscanClient
from src.clients.mempool_client import MempoolClient
from src.config import load_clusters, load_settings, load_thresholds
from src.db import (
    init_db,
    is_alert_already_sent,
    record_alert,
    record_snapshot,
)
from src.flows import (
    ChainSummary,
    collect_btc_clusters,
    collect_eth_clusters,
    fetch_prices,
    sort_by_display_order,
)
from src.format_embed import build_alert_embed, build_daily_embed
from src.notable import generate_notable_lines
from src.send_discord import send_embed


def setup_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level)
    logger.add("logs/app.log", level=level, rotation="10 MB", retention="14 days")


async def run_daily() -> None:
    settings = load_settings()
    clusters = load_clusters()
    thresholds = load_thresholds()
    api = thresholds.api

    logger.info("=== Daily report start ===")

    async with (
        MempoolClient(api.mempool_base_url, api.request_min_interval_ms) as btc_client,
        EtherscanClient(api.etherscan_base_url, settings.etherscan_api_key) as eth_client,
        CoinGeckoClient(api.coingecko_base_url) as price_client,
    ):
        btc_flows = await collect_btc_clusters(btc_client, clusters.btc_clusters, 24)
        eth_flows = await collect_eth_clusters(eth_client, clusters.eth_clusters, 24)
        prices = await fetch_prices(price_client)

    btc_flows = sort_by_display_order(btc_flows, clusters.display_order["btc"])
    eth_flows = sort_by_display_order(eth_flows, clusters.display_order["eth"])

    btc_summary = ChainSummary(
        chain="bitcoin",
        clusters=btc_flows,
        native_price_usd=prices.get("bitcoin", 0.0),
    )
    eth_summary = ChainSummary(
        chain="ethereum",
        clusters=eth_flows,
        native_price_usd=prices.get("ethereum", 0.0),
    )

    captured = datetime.utcnow()
    for summary in (btc_summary, eth_summary):
        for c in summary.clusters:
            record_snapshot(
                captured_at=captured,
                cluster_id=c.cluster_id,
                chain=c.chain,
                inflow=c.inflow,
                outflow=c.outflow,
                net_flow=c.net_flow,
                tx_count=c.tx_count,
            )

    notable = generate_notable_lines(btc_summary, eth_summary, thresholds.notable)
    embed = build_daily_embed(btc_summary, eth_summary, notable, thresholds.embed)
    await send_embed(settings.discord_webhook_daily, embed, dry_run=settings.dry_run)
    logger.info("=== Daily report done ===")


async def run_alert_check() -> None:
    settings = load_settings()
    clusters = load_clusters()
    thresholds = load_thresholds()
    api = thresholds.api
    interval_min = thresholds.schedule.alert_check_interval_minutes

    since = int((datetime.now(timezone.utc) - timedelta(minutes=interval_min * 2)).timestamp())

    async with (
        MempoolClient(api.mempool_base_url, api.request_min_interval_ms) as btc_client,
        EtherscanClient(api.etherscan_base_url, settings.etherscan_api_key) as eth_client,
    ):
        # BTC
        for cluster in clusters.btc_clusters:
            if not cluster.addresses:
                continue
            transfers = await btc_client.get_cluster_transfers(cluster.addresses, since)
            for t in transfers:
                threshold = thresholds.alert_thresholds.btc
                if not (
                    (t.amount_btc > 0 and t.amount_btc >= threshold.inflow)
                    or (t.amount_btc < 0 and t.amount_btc <= threshold.outflow)
                ):
                    continue
                if is_alert_already_sent(t.tx_hash):
                    continue
                direction = "inflow" if t.amount_btc > 0 else "outflow"
                embed = build_alert_embed(
                    cluster_label=cluster.label,
                    chain="bitcoin",
                    amount=t.amount_btc,
                    direction=direction,
                    tx_hash=t.tx_hash,
                    cfg=thresholds.embed,
                )
                ok = await send_embed(
                    settings.discord_webhook_alert, embed, dry_run=settings.dry_run
                )
                if ok and not settings.dry_run:
                    record_alert(cluster.id, t.tx_hash, t.amount_btc, direction[:3])

        # ETH
        for cluster in clusters.eth_clusters:
            if not cluster.addresses:
                continue
            transfers = await eth_client.get_cluster_transfers(cluster.addresses, since)
            for t in transfers:
                threshold = thresholds.alert_thresholds.eth
                if not (
                    (t.amount_eth > 0 and t.amount_eth >= threshold.inflow)
                    or (t.amount_eth < 0 and t.amount_eth <= threshold.outflow)
                ):
                    continue
                if is_alert_already_sent(t.tx_hash):
                    continue
                direction = "inflow" if t.amount_eth > 0 else "outflow"
                embed = build_alert_embed(
                    cluster_label=cluster.label,
                    chain="ethereum",
                    amount=t.amount_eth,
                    direction=direction,
                    tx_hash=t.tx_hash,
                    cfg=thresholds.embed,
                )
                ok = await send_embed(
                    settings.discord_webhook_alert, embed, dry_run=settings.dry_run
                )
                if ok and not settings.dry_run:
                    record_alert(cluster.id, t.tx_hash, t.amount_eth, direction[:3])


async def run_scheduler() -> None:
    settings = load_settings()
    thresholds = load_thresholds()
    tz = ZoneInfo(thresholds.schedule.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)

    for t in thresholds.schedule.daily_times:
        h, m = map(int, t.split(":"))
        scheduler.add_job(
            run_daily,
            CronTrigger(hour=h, minute=m, timezone=tz),
            id=f"daily_{t}",
            name=f"Daily {t}",
        )
        logger.info(f"Scheduled daily at {t} {tz}")

    scheduler.add_job(
        run_alert_check,
        IntervalTrigger(minutes=thresholds.schedule.alert_check_interval_minutes),
        id="alert",
        name="Alert check",
    )
    scheduler.start()
    logger.info(f"Scheduler started (mode={'DRY_RUN' if settings.dry_run else 'LIVE'})")

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--alert-once", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.log_level)
    init_db()

    if args.once:
        asyncio.run(run_daily())
    elif args.alert_once:
        asyncio.run(run_alert_check())
    else:
        asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
