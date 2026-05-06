# NISHI ETF Tracker (B案: 無料API構成)

## プロジェクト概要

BTC/ETH現物ETFのカストディウォレットのオンチェーン入出金を監視し、
Discordへ定期配信するBot。仮想NISHI(@Nishi8maru)ブランドの分析配信用。

**Nansen有料APIは使用しない。** 公開・無料データソースで構築する。

## 配信仕様

- **定期配信**: 1日2回(JST 9:00 / 22:00)
- **臨時アラート**: BTC ±1,000枚超 / ETH ±10,000枚超 単発検知時
- **粒度**: クラスタ別(Coinbase Custody / Fidelity)、発行体別の細かい分離はしない
- **footer**: "Data: mempool.space + Etherscan · by 仮想NISHI · @Nishi8maru"

## データソース

| 用途 | サービス | 認証 | レート |
|------|----------|------|--------|
| BTC tx/balance | mempool.space REST | 不要 | 緩い |
| BTC リアルタイム | mempool.space WebSocket | 不要 | 緩い |
| ETH tx | Etherscan API | API key必須(無料) | 5/sec, 100k/day |
| 価格 (BTC/ETH/USD) | CoinGecko Public API | 不要 | 30/min |

## 監視対象クラスタ

`config/clusters.yaml` で定義。

### BTC側
- `coinbase_custody_btc` : Coinbase Custodyの主要hot/warm wallet群(主にIBIT/ARKB/BITB/GBTC関連を内包)
- `fidelity_btc` : Fidelity Digital Assets主要wallet群(主にFBTC関連)

### ETH側
- `coinbase_custody_eth` : Coinbase CustodyのETH保有wallet群(主にETHA関連)
- `fidelity_eth` : Fidelity ETH保有wallet群(主にFETH関連)

**注意**: アドレス収集は手動。Arkham(無料閲覧)・Lookonchainブログ・GitHub有志リポを参照。
未確定アドレスは含めず、確実な代表アドレス10〜20個から開始する。
追加・除外は運用しながらconfigを更新する想定。

## 技術スタック

- Python 3.11+ / uv
- 主要ライブラリ:
  - `discord-webhook`
  - `apscheduler`
  - `httpx` (各無料API呼び出し)
  - `pyyaml`
  - `loguru`
  - `sqlalchemy` + SQLite
  - `python-dotenv`
  - `pydantic`

## ファイル構成

```
src/
├── main.py                 # エントリポイント・スケジューラ
├── config.py               # 設定ロード
├── clients/
│   ├── mempool_client.py   # mempool.space (BTC)
│   ├── etherscan_client.py # Etherscan (ETH)
│   └── coingecko_client.py # CoinGecko (価格)
├── flows.py                # クラスタ別フロー集計
├── notable.py              # Notable文言生成
├── format_embed.py         # Discord Embed
├── send_discord.py         # Webhook送信
└── db.py                   # SQLite履歴

config/
├── clusters.yaml           # 監視対象クラスタ・アドレス定義
└── thresholds.yaml         # 閾値・配信時刻
```

## コード規約

- 型ヒント必須(`from __future__ import annotations`)
- ログは loguru で構造化、INFO以上を logs/app.log に出力
- API keyは `.env` から読む、絶対にハードコード禁止
- Etherscanのレート制限を尊重(5 calls/sec): クライアント内でセマフォ制御
- mempool.spaceは緩いが連続バーストは避ける、リクエスト間に最低100ms
- SQLiteは `data/history.sqlite`
- エラー時はDiscord送信を指数バックオフで3回リトライ
- 集計タイムウィンドウは「過去24時間」、UTCで計算しJSTで表示

## 運用上の注意

- BTCのカストディは多数のUTXOアドレスを持つ。
  全てを追えないので、代表的な10〜20アドレスを監視する近似集計と理解する。
- `flow.estimated = true` フラグを内部で持ち、配信文言で「推定値」を明示する設計を検討。
- ETF発行体別の分離は不可能(B案の前提)。
  「BlackRock IBIT単独で+1,800 BTC」のような断定は配信しない。
  代わりに「Coinbase Custody全体で+2,250 BTC」と表現する。

## 仮想NISHIブランド原則

- 配信文言は事実ベース、推定は推定と明示
- 「Nansen依存ではなく自前のオンチェーン分析」というポジショニング
- Notable section は独自分析の見せ場、品質を保つ
