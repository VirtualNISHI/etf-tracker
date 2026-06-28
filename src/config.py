"""設定ファイルとenvのロード。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


class Cluster(BaseModel):
    """旧スキーマ互換 + 発行体別 (Phase 2) 拡張。

    Phase 2: id を ETF ticker (ibit, fbtc, bitb, gbtc, etha, feth, ethe) で運用。
    issuer は表示時に使う発行体名(BlackRock, Fidelity, Bitwise, Grayscale)。
    """
    id: str
    label: str
    chain: str  # 'bitcoin' or 'ethereum'
    issuer: str = ""  # Phase 2 で必須化、空なら label をそのまま使う
    custodian: str = ""  # "coinbase" | "fidelity" | "" (unknown)。同一custody内移動の判定に使う。
    note: str = ""
    addresses: list[str] = Field(default_factory=list)


class ClusterBook(BaseModel):
    btc_clusters: list[Cluster]
    eth_clusters: list[Cluster]
    display_order: dict[str, list[str]]


class ExchangeLabel(BaseModel):
    """config/labels.yaml の1エントリ。operator が OFFLINE で構築する編集ラベル。

    実行時には絶対にネットワーク取得しない(Nansen等は offline での seed のみ)。ToSクリーン。
    """
    name: str
    type: str = "exchange"  # exchange | custodian | bridge | otc | other
    chain: str  # "bitcoin" | "ethereum"
    as_of: str = ""  # 任意 YYYY-MM-DD。古い(label_max_age_days超)と公開Xでは不明扱い。
    addresses: list[str] = Field(default_factory=list)

    @field_validator("as_of", mode="before")
    @classmethod
    def _as_of_to_str(cls, v: object) -> str:
        # YAMLが 2026-06-28 を date 化しても文字列に正規化(引用符忘れ対策)。
        return "" if v is None else str(v)


class LabelBook(BaseModel):
    version: int = 1
    exchanges: list[ExchangeLabel] = Field(default_factory=list)


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


class CounterpartyConfig(BaseModel):
    """カウンターパーティ解決(誰から誰へ)の挙動設定。thresholds.yaml の counterparty: に対応。"""
    enabled: bool = True
    suppress_internal: bool = True  # 同一custody内移動: True=完全に抑制, False=Discordのみmuted通知
    dominance_ratio: float = 0.6  # 名前を出すのに必要な、解決エンティティの最低価値シェア
    batch_fanout_max: int = 5  # 外部受取先がこれ超なら1つに名寄せしない(複数アドレスへ分散)
    label_max_age_days: int = 180  # labels.yaml as_of がこれより古いと公開Xでは不明扱い


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
    # 既存 thresholds.yaml に counterparty: が無くてもデフォルト構築でロード可能。
    counterparty: CounterpartyConfig = CounterpartyConfig()


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
    # リアルタイム大口アラートの X 自動投稿ゲート。
    # 既定 False = 認証情報があっても alert は preview PNG 生成のみ(実投稿しない)。
    # 明示的に X_ALERT_ENABLED=true にした時だけ実投稿する(安全弁)。
    x_alert_enabled: bool = False
    # 定時(daily)レポートの X 投稿ゲート。既定 False = 認証情報があっても daily は
    # X に出さない(Discord のみ)。alert の X 投稿と独立して制御するための安全弁。
    x_daily_enabled: bool = False
    # LLM (jp_translator) — Gemini → OpenAI → Grok → DeepL のフォールバックチェーン。
    # 全部空なら AI 解説スキップ (notable.py の決定論版にフォールバック)。
    # 最低 1 つあれば動く (失敗時のみ次のプロバイダへ)。
    gemini_api_key: str = ""
    openai_api_key: str = ""
    xai_api_key: str = ""
    deepl_api_key: str = ""

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


def load_labels() -> LabelBook:
    """config/labels.yaml をロード。無ければ空(=全カウンターパーティが不明ウォレットに degrade)。"""
    p = CONFIG_DIR / "labels.yaml"
    if not p.exists():
        return LabelBook()
    return LabelBook.model_validate(_load_yaml(p))


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
        x_alert_enabled=os.getenv("X_ALERT_ENABLED", "false").lower() == "true",
        x_daily_enabled=os.getenv("X_DAILY_ENABLED", "false").lower() == "true",
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        xai_api_key=os.getenv("XAI_API_KEY", "") or os.getenv("GROK_API_KEY", ""),
        deepl_api_key=os.getenv("DEEPL_API_KEY", ""),
    )
