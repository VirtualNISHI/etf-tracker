# NISHI ETF Tracker (B案)

BTC/ETH現物ETFのカストディウォレットフローをDiscordに定期配信するBot。
**Nansen有料APIなし、無料データソース(mempool.space + Etherscan + CoinGecko)で構築。**

## クイックスタート

```bash
# 1. 依存インストール
uv sync

# 2. 環境変数設定
cp .env.example .env
# Etherscan API key、Discord Webhook URLを記入

# 3. クラスタアドレス確認
# config/clusters.yaml を実アドレスで埋める(Arkham等から手動収集)

# 4. ドライラン(Discord送信せずログのみ)
DRY_RUN=true uv run python -m src.main --once

# 5. 本番1回実行
uv run python -m src.main --once

# 6. 常駐起動
uv run python -m src.main
```

## アドレス収集の手順

1. https://intel.arkm.com (無料登録) で "BlackRock", "Fidelity Digital Assets", "Coinbase Custody" 等を検索
2. Bitcoin Addresses タブから残高上位を10〜20個コピー
3. `config/clusters.yaml` の対応するクラスタの `addresses` に追加
4. ETH側はEtherscanで対応するEntity関連アドレスを検索

## 配信スケジュール

| 種別 | 時刻(JST) | チャンネル | 閾値 |
|------|-----------|-----------|------|
| 定期 | 09:00 / 22:00 | `#etf-flow-daily` | - |
| アラート | 都度 | `#etf-flow-alert` | BTC ±1,000 / ETH ±10,000 |

## 制約事項

- ETF発行体別の細かい分離は不可。クラスタ単位の集計のみ。
- BTCはUTXOが分散しているため、監視アドレス外のフローは捕捉できない近似集計。
- 配信文言では「推定値」「主要wallet基準」と明示する。
