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
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from src.clients.coingecko_client import CoinGeckoClient
from src.clients.etherscan_client import EtherscanClient
from src.clients.mempool_client import MempoolClient
from src.config import load_clusters, load_labels, load_settings, load_thresholds
from src.counterparty import ResolvedTx, public_cp_display, resolve_btc, resolve_eth
from src.db import (
    init_db,
    is_alert_already_sent,
    record_alert,
    record_snapshot,
)
from src.labels import CounterpartyResolver, build_flow_line
from src.flows import (
    ChainSummary,
    collect_btc_clusters,
    collect_eth_clusters,
    fetch_prices,
    sort_by_display_order,
)
from src.format_embed import build_alert_embed, build_daily_embed
from src.format_x import build_alert_text, build_daily_text
from src.notable import generate_notable_lines
from src.render_image import render_alert_card, render_daily_report
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
        # 集計期間: 12時間 (8:00 / 22:00 配信が独立した非オーバーラップなウィンドウ)
        btc_flows = await collect_btc_clusters(btc_client, clusters.btc_clusters, 12)
        eth_flows = await collect_eth_clusters(eth_client, clusters.eth_clusters, 12)
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

    # まず Gemini で AI 解説を生成、失敗時 (key 無 or API エラー) は notable.py の
    # 決定論版にフォールバック。
    from src.ai_summary import generate_ai_summary

    notable = generate_ai_summary(btc_summary, eth_summary, api_key=settings.gemini_api_key)
    if not notable:
        logger.info("AI summary unavailable, falling back to deterministic Notable")
        notable = generate_notable_lines(btc_summary, eth_summary, thresholds.notable)

    embed = build_daily_embed(btc_summary, eth_summary, notable, thresholds.embed)
    await send_embed(settings.discord_webhook_daily, embed, dry_run=settings.dry_run)

    # X (Twitter) 投稿: 画像生成 → 短いキャプションと一緒に投稿
    if settings.x_enabled:
        try:
            png = render_daily_report(btc_summary, eth_summary, notable)
        except Exception as e:
            logger.error(f"image render failed, fallback to text: {e}")
            png = None

        # キャプション(画像内に詳細あり、ここはタイトル+ハッシュタグだけ)
        caption_lines = [
            "📊 ETF Custody Flow Report",
            (now_jst_str := datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")),
            "",
            f"BTC: {('+' if btc_summary.total_net_flow >= 0 else '')}{btc_summary.total_net_flow:,.0f} BTC",
            f"ETH: {('+' if eth_summary.total_net_flow >= 0 else '')}{eth_summary.total_net_flow:,.0f} ETH",
            "",
            "#BTC #ETH #ETF #SmartMoney",
        ]
        caption = "\n".join(caption_lines)

        if settings.dry_run:
            logger.info(f"[DRY_RUN] X caption ({len(caption)} chars):\n{caption}")
            if png:
                from pathlib import Path as _P

                preview_path = ROOT / "data" / "x_post_preview.png"
                _P(preview_path.parent).mkdir(parents=True, exist_ok=True)
                preview_path.write_bytes(png)
                logger.info(f"[DRY_RUN] image saved: {preview_path}")
        else:
            from src.clients.x_client import XClient

            x = XClient(
                api_key=settings.x_api_key,
                api_key_secret=settings.x_api_key_secret,
                access_token=settings.x_access_token,
                access_token_secret=settings.x_access_token_secret,
            )
            if png:
                x.post_with_image(caption, png)
            else:
                # 画像生成失敗時はテキストのみで fallback
                x.post(build_daily_text(btc_summary, eth_summary, notable))
    else:
        logger.info("X credentials not set, skipping tweet")

    logger.info("=== Daily report done ===")


def _post_alert_to_x(
    settings,
    *,
    resolved: ResolvedTx,
    unit: str,
    price_usd: float,
    flow_label_public: str,
) -> None:
    """大口アラートを画像カード化して X 投稿(または preview 保存)。

    実投稿の条件: x_enabled かつ x_alert_enabled かつ not dry_run の3条件すべて。
    それ以外(dry_run / フラグ無効 / 認証なし)は data/alert_x_preview.png に保存するだけで
    実投稿はしない(安全弁)。内部移動は呼び出し側で除外済みだが二重ガードする。
    """
    if resolved.is_internal:
        return
    try:
        png = render_alert_card(
            issuer=resolved.issuer,
            ticker=resolved.ticker,
            chain=resolved.chain,
            amount=resolved.amount,
            unit=unit,
            price_usd=price_usd,
            direction=resolved.direction,
            tx_hash=resolved.tx_hash,
            block_time=resolved.block_time,
            flow_label=flow_label_public,
        )
    except Exception as e:
        logger.error(f"alert image render failed: {e}")
        return

    caption = build_alert_text(
        issuer=resolved.issuer,
        ticker=resolved.ticker,
        amount=resolved.amount,
        unit=unit,
        price_usd=price_usd,
        block_time=resolved.block_time,
        flow_line=flow_label_public,
    )

    live = settings.x_enabled and settings.x_alert_enabled and not settings.dry_run
    if not live:
        if settings.dry_run:
            reason = "dry_run"
        elif not settings.x_enabled:
            reason = "X credentials not set"
        elif not settings.x_alert_enabled:
            reason = "X_ALERT_ENABLED=false"
        else:
            reason = "unknown"
        preview = ROOT / "data" / "alert_x_preview.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(png)
        logger.info(
            f"[alert-preview] X投稿スキップ ({reason}); image -> {preview}\n"
            f"caption ({len(caption)} chars):\n{caption}"
        )
        return

    from src.clients.x_client import XClient

    x = XClient(
        api_key=settings.x_api_key,
        api_key_secret=settings.x_api_key_secret,
        access_token=settings.x_access_token,
        access_token_secret=settings.x_access_token_secret,
    )
    x.post_with_image(caption, png)


async def _handle_resolved(
    resolved: ResolvedTx | None,
    settings,
    thresholds,
    prices: dict,
    emitted: set[tuple[str, str]],
) -> None:
    """解決済み1件を Discord + X に通知。内部移動の抑制・閾値ゲート・dedup を一元化。"""
    if resolved is None:
        return
    cfg_cp = thresholds.counterparty
    chain = resolved.chain
    unit = "BTC" if chain == "bitcoin" else "ETH"
    thr = thresholds.alert_thresholds.btc if chain == "bitcoin" else thresholds.alert_thresholds.eth
    price = prices.get("bitcoin" if chain == "bitcoin" else "ethereum", 0.0)
    key = (resolved.tx_hash, resolved.cluster_id)

    # ---- 内部移動(同一custody): X には絶対出さない
    if resolved.is_internal:
        if cfg_cp.suppress_internal:
            logger.debug(
                f"[internal-suppressed] {resolved.ticker} {resolved.tx_hash[:12]} "
                f"net={resolved.amount:.2f}{unit}"
            )
            return
        if key in emitted or is_alert_already_sent(*key):
            return
        embed = {
            "title": f"🔄 内部移動検知: {resolved.ticker}",
            "description": f"{resolved.issuer} {resolved.ticker} 内部シャッフル (同一カストディ)",
            "color": 0x95A5A6,
            "fields": [
                {"name": "Chain", "value": chain, "inline": True},
                {"name": "Net", "value": f"{resolved.amount:,.2f} {unit}", "inline": True},
            ],
            "timestamp": datetime.utcnow().isoformat(),
        }
        ok = await send_embed(settings.discord_webhook_alert, embed, dry_run=settings.dry_run)
        if ok and not settings.dry_run:
            record_alert(resolved.cluster_id, resolved.tx_hash, resolved.amount, "int")
        emitted.add(key)
        return

    # ---- 閾値ゲート(cluster-net headline)
    amt = resolved.amount
    passes = (resolved.direction == "inflow" and amt >= thr.inflow) or (
        resolved.direction == "outflow" and amt <= thr.outflow
    )
    if not passes:
        logger.debug(
            f"[sub-threshold] {resolved.ticker} {resolved.tx_hash[:12]} net={amt:.2f}{unit} "
            f"(gross-ext={resolved.external_amount:.2f})"
        )
        return

    if key in emitted or is_alert_already_sent(resolved.tx_hash, resolved.cluster_id):
        return

    cp = resolved.counterparty
    # 外部payment合計が cluster-net と >5% 乖離 → unmapped change の疑い。名指しは控えめに。
    base = abs(resolved.amount)
    if (
        resolved.direction == "outflow"
        and resolved.external_amount > 0
        and abs(resolved.external_amount - base) / max(base, 1e-9) > 0.05
    ):
        cp.low_confidence = True

    show_route = cfg_cp.enabled
    our_label = f"{resolved.issuer} {resolved.ticker}"
    cp_priv = cp.display
    cp_pub = public_cp_display(cp, chain)
    if resolved.direction == "inflow":
        from_display, to_display = cp_priv, our_label
    else:
        from_display, to_display = our_label, cp_priv

    # 想定シグナル(Discordのみ・確定取引所のみ)
    signal_hint = ""
    if show_route and cp.kind == "exchange" and not cp.low_confidence:
        signal_hint = (
            "🔵 新規創出(Creation)＝強気" if resolved.direction == "inflow" else "🔴 償還(Redemption)＝弱気"
        )

    embed = build_alert_embed(
        cluster_label=resolved.ticker,
        chain=chain,
        amount=resolved.amount,
        direction=resolved.direction,
        tx_hash=resolved.tx_hash,
        cfg=thresholds.embed,
        from_display=from_display if show_route else "",
        to_display=to_display if show_route else "",
        low_confidence=cp.low_confidence,
        signal_hint=signal_hint,
    )
    ok = await send_embed(settings.discord_webhook_alert, embed, dry_run=settings.dry_run)

    flow_label_public = (
        build_flow_line(cp_pub, our_label, resolved.direction, resolved.other_count)
        if show_route
        else ""
    )
    _post_alert_to_x(
        settings,
        resolved=resolved,
        unit=unit,
        price_usd=price,
        flow_label_public=flow_label_public,
    )

    if ok and not settings.dry_run:
        record_alert(resolved.cluster_id, resolved.tx_hash, resolved.amount, resolved.direction[:3])
    emitted.add(key)


async def run_alert_check() -> None:
    settings = load_settings()
    clusters = load_clusters()
    labels = load_labels()
    thresholds = load_thresholds()
    cfg_cp = thresholds.counterparty
    api = thresholds.api
    interval_min = thresholds.schedule.alert_check_interval_minutes

    since = int((datetime.now(timezone.utc) - timedelta(minutes=interval_min * 2)).timestamp())

    # X アラート画像の USD 換算用に価格を1回取得(失敗しても 0.0 で続行)
    try:
        async with CoinGeckoClient(api.coingecko_base_url) as price_client:
            prices = await fetch_prices(price_client)
    except Exception as e:
        logger.warning(f"price fetch failed for alert (USD will show as 0): {e}")
        prices = {}

    resolver = CounterpartyResolver(clusters, labels, cfg_cp)
    emitted: set[tuple[str, str]] = set()
    btc_tx_cache: dict[str, dict] = {}

    async with (
        MempoolClient(api.mempool_base_url, api.request_min_interval_ms) as btc_client,
        EtherscanClient(api.etherscan_base_url, settings.etherscan_api_key) as eth_client,
    ):
        # BTC: 候補tx_hashを集め、正本txを1回ずつ再取得 → (tx, cluster)単位で解決
        for cluster in clusters.btc_clusters:
            if not cluster.addresses:
                continue
            transfers = await btc_client.get_cluster_transfers(cluster.addresses, since)
            for txid in {t.tx_hash for t in transfers}:
                raw = btc_tx_cache.get(txid)
                if raw is None:
                    try:
                        raw = await btc_client.get_tx(txid)
                    except Exception as e:
                        logger.error(f"mempool get_tx {txid[:12]} failed: {e}")
                        continue
                    btc_tx_cache[txid] = raw
                resolved = resolve_btc(raw, cluster, resolver, cfg_cp)
                await _handle_resolved(resolved, settings, thresholds, prices, emitted)

        # ETH: tx_hash でグルーピングし、cluster-net を1回判定(二重発火も同時に解消)
        for cluster in clusters.eth_clusters:
            if not cluster.addresses:
                continue
            transfers = await eth_client.get_cluster_transfers(cluster.addresses, since)
            groups: dict[str, list] = {}
            for t in transfers:
                groups.setdefault(t.tx_hash, []).append(t)
            for legs in groups.values():
                resolved = resolve_eth(legs, cluster, resolver, cfg_cp)
                await _handle_resolved(resolved, settings, thresholds, prices, emitted)


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
