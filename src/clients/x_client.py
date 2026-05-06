"""X (Twitter) API v2 クライアント。OAuth 1.0a User Context で投稿。

ドキュメント: https://docs.tweepy.org/en/stable/client.html#tweepy.Client.create_tweet
- 4つのキーが必要 (Consumer Key/Secret + Access Token/Secret)
- 1ツイート最大280文字(プレミアム会員でも API 経由は基本280)
- 月間投稿数制限あり(Free tier: 500 tweets/month)
"""
from __future__ import annotations

import tweepy
from loguru import logger


class XClient:
    """X 投稿クライアント。同期APIを薄くラップしただけ。"""

    def __init__(
        self,
        api_key: str,
        api_key_secret: str,
        access_token: str,
        access_token_secret: str,
    ):
        self._client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_key_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )

    def post(self, text: str) -> bool:
        """ツイートを投稿。成功時 True、失敗時 False。"""
        if len(text) > 280:
            logger.warning(f"tweet too long ({len(text)} chars), truncating to 277")
            text = text[:277] + "..."
        try:
            r = self._client.create_tweet(text=text)
            tweet_id = r.data.get("id") if r.data else "unknown"
            logger.info(f"X posted: id={tweet_id} ({len(text)} chars)")
            return True
        except tweepy.TooManyRequests as e:
            logger.error(f"X rate limit: {e}")
            return False
        except tweepy.Forbidden as e:
            logger.error(f"X forbidden (auth/permission issue): {e}")
            return False
        except Exception as e:
            logger.error(f"X post failed: {e}")
            return False
