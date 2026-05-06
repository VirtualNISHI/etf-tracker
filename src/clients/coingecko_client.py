"""CoinGecko Public APIクライアント(価格取得)。

無料、認証不要、30 calls/min制限。
ETF配信ではBTC/ETHのspot priceしか使わないので余裕。
"""
from __future__ import annotations

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential


class CoinGeckoClient:
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "CoinGeckoClient":
        self._client = httpx.AsyncClient(timeout=15.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def get_prices_usd(self, coin_ids: list[str]) -> dict[str, float]:
        """{'bitcoin': 101234.5, 'ethereum': 3700.1} の形で返す。"""
        assert self._client is not None
        ids = ",".join(coin_ids)
        resp = await self._client.get(
            f"{self._base}/simple/price",
            params={"ids": ids, "vs_currencies": "usd"},
        )
        resp.raise_for_status()
        data = resp.json()
        result: dict[str, float] = {}
        for cid in coin_ids:
            try:
                result[cid] = float(data[cid]["usd"])
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"coingecko price missing {cid}: {e}")
                result[cid] = 0.0
        return result
