"""X (Twitter) API クライアント。OAuth 1.0a User Context で投稿。

- テキストのみ投稿: tweepy.Client (v2) の create_tweet
- 画像付き投稿: tweepy.API (v1.1) の media_upload + create_tweet(media_ids=[...])
- 4つのキーが必要 (Consumer Key/Secret + Access Token/Secret)
- 月間投稿数制限あり(Free tier: 500 tweets/month)
- 画像はローカル一時ファイル経由でアップロード(tweepy が要求)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import tweepy
from loguru import logger


class XClient:
    """X 投稿クライアント。"""

    def __init__(
        self,
        api_key: str,
        api_key_secret: str,
        access_token: str,
        access_token_secret: str,
    ):
        # v2 endpoint(create_tweet)用
        self._client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_key_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )
        # v1.1 endpoint(media_upload)用
        auth = tweepy.OAuth1UserHandler(
            api_key, api_key_secret, access_token, access_token_secret
        )
        self._api_v1 = tweepy.API(auth)

    def post(self, text: str) -> bool:
        """テキストのみツイート。"""
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

    def post_with_image(self, text: str, image_bytes: bytes) -> bool:
        """画像付きツイート。

        text: 280文字以内のキャプション(ハッシュタグなど)。空でもOK。
        image_bytes: PNG/JPEG の bytes。
        """
        if len(text) > 280:
            logger.warning(f"caption too long ({len(text)} chars), truncating to 277")
            text = text[:277] + "..."
        # 一時ファイルに書く(media_upload は filename を要求)
        tmp = Path(tempfile.gettempdir()) / "etf_tracker_post.png"
        tmp.write_bytes(image_bytes)
        try:
            media = self._api_v1.media_upload(filename=str(tmp))
            media_id = media.media_id_string
            r = self._client.create_tweet(text=text, media_ids=[media_id])
            tweet_id = r.data.get("id") if r.data else "unknown"
            logger.info(
                f"X posted with image: id={tweet_id} media_id={media_id} "
                f"caption={len(text)} chars image={len(image_bytes):,} bytes"
            )
            return True
        except tweepy.TooManyRequests as e:
            logger.error(f"X rate limit: {e}")
            return False
        except tweepy.Forbidden as e:
            logger.error(f"X forbidden: {e}")
            return False
        except Exception as e:
            logger.error(f"X image post failed: {e}")
            return False
        finally:
            tmp.unlink(missing_ok=True)
