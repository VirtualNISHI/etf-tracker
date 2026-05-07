"""mempool.space REST APIクライアント(BTC)。

ドキュメント: https://mempool.space/docs/api/rest
- 認証不要、無料、レート制限緩い(常識的範囲ならOK)
- Block height・mempool情報も取れるがETF監視では address/txs を使う
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


@dataclass
class BTCTransfer:
    """BTCの単一アドレスから見たフロー記録。"""

    tx_hash: str
    address: str
    amount_btc: float  # 正=受信(流入), 負=送信(流出)
    block_time: datetime
    confirmed: bool


class MempoolClient:
    def __init__(self, base_url: str, min_interval_ms: int = 100):
        self._base = base_url.rstrip("/")
        self._min_interval = min_interval_ms / 1000.0
        self._last_call = 0.0
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "MempoolClient":
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = asyncio.get_event_loop().time()

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=1, max=4),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    async def _get(self, path: str) -> Any:
        assert self._client is not None
        await self._throttle()
        url = f"{self._base}{path}"
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    async def get_address_txs(self, address: str) -> list[dict[str, Any]]:
        """確認済みtxの直近(mempool.spaceは最大50件返す)。"""
        return list(await self._get(f"/address/{address}/txs"))

    async def get_transfers_since(
        self,
        address: str,
        since_unix: int,
    ) -> list[BTCTransfer]:
        """指定UNIX時刻以降のフロー(net amount)を計算。"""
        txs = await self.get_address_txs(address)
        transfers: list[BTCTransfer] = []
        for tx in txs:
            status = tx.get("status", {})
            block_time = status.get("block_time")
            if not block_time or block_time < since_unix:
                continue

            # vin: 自アドレスからの送信、vout: 自アドレスへの受信
            sent = sum(
                int(vin["prevout"]["value"])
                for vin in tx.get("vin", [])
                if vin.get("prevout", {}).get("scriptpubkey_address") == address
            )
            received = sum(
                int(vout["value"])
                for vout in tx.get("vout", [])
                if vout.get("scriptpubkey_address") == address
            )
            net_sat = received - sent
            if net_sat == 0:
                continue
            transfers.append(
                BTCTransfer(
                    tx_hash=tx["txid"],
                    address=address,
                    amount_btc=net_sat / 1e8,
                    block_time=datetime.fromtimestamp(block_time, tz=timezone.utc),
                    confirmed=status.get("confirmed", False),
                )
            )
        return transfers

    async def get_cluster_transfers(
        self,
        addresses: list[str],
        since_unix: int,
        concurrency: int = 8,
    ) -> list[BTCTransfer]:
        """複数アドレスをまとめてクラスタ単位で取得(並列実行)。

        concurrency: 同時並行リクエスト数の上限。mempool.space は
        Cloudflare 経由で大量並列に弱いので 8 程度が無難。

        注: クラスタ内アドレス間のtx(自己内移動)は両側で計上されるため、
        flows.py で重複除外する。
        """
        sem = asyncio.Semaphore(concurrency)

        async def fetch_one(addr: str) -> list[BTCTransfer]:
            async with sem:
                try:
                    return await self.get_transfers_since(addr, since_unix)
                except Exception as e:
                    logger.error(f"mempool {addr[:12]}... fetch failed: {e}")
                    return []

        results = await asyncio.gather(*[fetch_one(a) for a in addresses])
        return [t for sublist in results for t in sublist]
