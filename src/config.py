"""設定ファイルとenvのロード。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


class Cluster(BaseModel):
    id: str
    label: str
    chain: str  # 'bitcoin' or 'ethereum'
    note: str = ""
    addresses: list[str] = Field(default_factory=list)


class ClusterBook(BaseModel):
    btc_clusters: list[Cluster]
    eth_clusters: list[Cluster]
    display_order: dict[str, list[str]]


class AlertThreshold(BaseModel):
    inflow: float
    outflow: float


class AlertThresholds(BaseModel):
    btc: AlertThreshold
    eth: AlertThreshold


class ScheduleConfig(BaseModel):
    timezone: str
    daily_times: list[str]
    alert_check_interval_minutes: int


class NotableConfig(BaseModel):
    lookback_days: int
    consecutive_flow_min_days: int
    significant_net_flow_btc: float
    significant_net_flow_eth: float


class EmbedConfig(BaseModel):
    color_btc: int
    color_eth: int
    color_alert: int


class APIConfig(BaseModel):
    etherscan_base_url: str
    mempool_base_url: str
    coingecko_base_url: str
    request_min_interval_ms: int


class ThresholdsConfig(BaseModel):
    schedule: ScheduleConfig
    alert_thresholds: AlertThresholds
    notable: NotableConfig
    embed: EmbedConfig
    api: APIConfig


class Settings(BaseModel):
    etherscan_api_key: str
    discord_webhook_daily: str
    discord_webhook_alert: str
    log_level: str = "INFO"
    dry_run: bool = False
    # X (Twitter) — 4つすべて埋まっていれば投稿、ひとつでも空ならスキップ
    x_api_key: str = ""
    x_api_key_secret: str = ""
    x_access_token: str = ""
    x_access_token_secret: str = ""

    @property
    def x_enabled(self) -> bool:
        return all(
            [
                self.x_api_key,
                self.x_api_key_secret,
                self.x_access_token,
                self.x_access_token_secret,
            ]
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_clusters() -> ClusterBook:
    return ClusterBook.model_validate(_load_yaml(CONFIG_DIR / "clusters.yaml"))


def load_thresholds() -> ThresholdsConfig:
    return ThresholdsConfig.model_validate(_load_yaml(CONFIG_DIR / "thresholds.yaml"))


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    return Settings(
        etherscan_api_key=os.environ["ETHERSCAN_API_KEY"],
        discord_webhook_daily=os.environ["DISCORD_WEBHOOK_DAILY"],
        discord_webhook_alert=os.environ["DISCORD_WEBHOOK_ALERT"],
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
        x_api_key=os.getenv("X_API_KEY", ""),
        x_api_key_secret=os.getenv("X_API_KEY_SECRET", ""),
        x_access_token=os.getenv("X_ACCESS_TOKEN", ""),
        x_access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET", ""),
    )
