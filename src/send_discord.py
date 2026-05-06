"""Discord Webhook送信。"""
from __future__ import annotations

import asyncio
from typing import Any

from discord_webhook import DiscordEmbed, DiscordWebhook
from loguru import logger


def _to_discord_embed(d: dict[str, Any]) -> DiscordEmbed:
    embed = DiscordEmbed(
        title=d.get("title"),
        description=d.get("description"),
        color=d.get("color"),
    )
    for f in d.get("fields", []):
        embed.add_embed_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
    if footer := d.get("footer"):
        embed.set_footer(text=footer["text"])
    if ts := d.get("timestamp"):
        embed.set_timestamp(ts)
    return embed


async def send_embed(
    webhook_url: str,
    embed_dict: dict[str, Any],
    *,
    dry_run: bool = False,
    max_retries: int = 3,
) -> bool:
    if dry_run:
        logger.info(f"[DRY_RUN] Embed: {embed_dict.get('title')}")
        return True

    for attempt in range(max_retries):
        try:
            wh = DiscordWebhook(url=webhook_url, rate_limit_retry=True)
            wh.add_embed(_to_discord_embed(embed_dict))
            r = wh.execute()
            if r.status_code in (200, 204):
                logger.info(f"Sent: {embed_dict.get('title')}")
                return True
            logger.warning(f"Webhook {r.status_code}: {r.text}")
        except Exception as e:
            logger.error(f"Webhook error attempt {attempt + 1}: {e}")
        if attempt < max_retries - 1:
            await asyncio.sleep(2**attempt)
    return False
