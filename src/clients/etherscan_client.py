"""Etherscan APIクライアント(ETH)。

ドキュメント: https://docs.etherscan.io
- API key必須(無料登録)
- レート制限: 5 calls/sec, 100,000 calls/day
- V2 エンドポイント (https://api.etherscan.io/v2/api) を使用、全リクエストに chainid 必須
- chainid=1 が Ethereum mainnet。Base/Arbitrum 等に拡張する場合は init 引数で切替
- Internal Tx, ERC-20も別エンドポイントで取得可能だが、ETHネイティブはtxlistで十分
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
class ETHTransfer:
    tx_hash: str
    address: str
    amount_eth: float  # 正=流入, 負=流出
    block_time: datetime
    confirmed: bool = True


class EtherscanClient:
    """5 calls/sec制限を尊重するためセマフォで直列化。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        rate_limit_per_sec: int = 5,
        chain_id: int = 1,
    ):
        self._base = base_url
        self._api_key = api_key
        self._chain_id = chain_id
        self._semaphore = asyncio.Semaphore(rate_limit_per_sec)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "EtherscanClient":
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=8),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None
        async with self._semaphore:
            params = {**params, "chainid": self._chain_id, "apikey": self._api_key}
            resp = await self._client.get(self._base, params=params)
            resp.raise_for_status()
            await asyncio.sleep(0.21)  # 5 req/sec を確実に下回る
            return dict(resp.json())

    async def get_normal_txs(
        self,
        address: str,
        startblock: int = 0,
        endblock: int = 99_999_999,
    ) -> list[dict[str, Any]]:
        """address宛の通常txを取得(時系列降順)。"""
        result = await self._request(
            {
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": startblock,
                "endblock": endblock,
                "sort": "desc",
                "page": 1,
                "offset": 1000,
            }
        )
        if result.get("status") != "1":
            msg = result.get("message", "")
            if msg == "No transactions found":
                return []
            logger.warning(f"etherscan {address[:10]}... msg={msg}")
            return []
        return list(result.get("result", []))

    async def get_transfers_since(
        self,
        address: str,
        since_unix: int,
    ) -> list[ETHTransfer]:
        txs = await self.get_normal_txs(address)
        transfers: list[ETHTransfer] = []
        addr_lower = address.lower()
        for tx in txs:
            ts = int(tx.get("timeStamp", 0))
            if ts < since_unix:
                break  # 降順ソートなので以降は古い

            value_wei = int(tx.get("value", "0"))
            if value_wei == 0:
                continue

            value_eth = value_wei / 1e18
            from_addr = tx.get("from", "").lower()
            to_addr = tx.get("to", "").lower()

            if to_addr == addr_lower:
                amount = value_eth
            elif from_addr == addr_lower:
                amount = -value_eth
            else:
                continue

            transfers.append(
                ETHTransfer(
                    tx_hash=tx["hash"],
                    address=address,
                    amount_eth=amount,
                    block_time=datetime.fromtimestamp(ts, tz=timezone.utc),
                )
            )
        return transfers

    async def get_cluster_transfers(
        self,
        addresses: list[str],
        since_unix: int,
    ) -> list[ETHTransfer]:
        all_transfers: list[ETHTransfer] = []
        for addr in addresses:
            try:
                transfers = await self.get_transfers_since(addr, since_unix)
                all_transfers.extend(transfers)
            except Exception as e:
                logger.error(f"etherscan {addr[:10]}... fetch failed: {e}")
        return all_transfers
