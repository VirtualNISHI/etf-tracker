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

| 種別 | 時刻(JST) | チャンネル | 閾値 | X投稿 |
|------|-----------|-----------|------|-------|
| 定期 | 09:00 / 22:00 | `#etf-flow-daily` | - | 画像付き(X認証情報があれば) |
| アラート | 都度 | `#etf-flow-alert` | BTC ±1,000 / ETH ±10,000 | 画像カード(`X_ALERT_ENABLED=true` 時のみ) |

## リアルタイム大口アラートの X 画像投稿

`run_alert_check()`(5分毎)が閾値超えの単発txを検知すると、Discord通知に加えて
**1枚の画像カード**(発行体・ティッカー・流入/流出・数量・USD換算・tx・検知時刻)を生成し、
X へ投稿できる。

安全弁として **`X_ALERT_ENABLED`(既定 false)** でゲートしている:

- `false`(既定): 認証情報があっても **`data/alert_x_preview.png` に画像を保存するだけ**で実投稿しない。
  ログにキャプション全文も出る。まずこれで画像・文言を確認する。
- `true`: X認証情報がすべて揃い、かつ `DRY_RUN=false` のときだけ実投稿する。

カードのデザインは定時レポート(`render_daily_report`)と同一パレット。
データソースは Discord アラートと同じ mempool.space / Etherscan(Nansenラベル不使用=ToSクリーン)。

## カウンターパーティ表示(誰から誰へ)

アラートは「**どこから → どこへ**」を1行で明示する。相手側は4分類:

| 分類 | 表示例 | 判定元 |
|------|--------|--------|
| 取引所 | `取引所 Coinbase` | `config/labels.yaml`(自前編集ラベル) |
| ETF | `BlackRock IBIT` | `config/clusters.yaml`(別クラスタかつ別custody) |
| 内部移動 | `内部移動` | 同一custody内シャッフル → 既定で**抑制** |
| 不明 | `不明ウォレット (bc1q…)` | 未ラベル/曖昧(混在入力・分散・stale)。安全側の既定 |

意味付け: **取引所→ETF=新規創出(強気)** / **ETF→取引所=償還(弱気)**。
この「想定」ヒントは Discord(確定取引所のみ)に出し、公開Xでは出さない。

### 仕組み(誤ラベル防止)

- **per-tx・cluster-net判定**: `GET /tx/{txid}` で正本を再取得し、txごとに1回だけ判定。
  これにより同一custody内振替による**二重アラート(流出+流入)も解消**。
- **所有権分類**: 混在入力(CoinJoin/co-spend)・change戻り・cross-ETFを区別。曖昧なら不明に倒す。
- **公開X信頼ゲート**: low_confidence/stale なラベルは公開Xでは**強制的に不明**に落とす(誤った取引所名を投稿しない)。Discordは私的なので `※推定` 付きで表示可。

### ToSクリーン

ラベル解決器は**実行時に一切ネットワークI/Oしない**。入力は `clusters.yaml` と
operatorが**offlineで構築**した `config/labels.yaml` のみ。Nansen/Arkham は labels.yaml を
作る offline 工程でだけ使う(第三者フィードを実行時に転載しない)。

### labels.yaml の育て方

```bash
# 既存の Arkham TSV エクスポートから取引所アドレス候補を抽出(stdout に YAML 断片)
uv run python scripts/build_labels.py scripts/blackrock_arkham.tsv
```

出力をレビューして `config/labels.yaml` の `exchanges:` に追記する。各 `as_of` を更新日にし、
`thresholds.yaml` の `counterparty.label_max_age_days`(既定180日)より古いものは公開Xで不明扱いになる。

### 設定(`thresholds.yaml` → `counterparty:`)

| キー | 既定 | 意味 |
|------|------|------|
| `enabled` | true | false で from→to 表示をオフ(per-tx判定・内部抑制は継続) |
| `suppress_internal` | true | 同一custody内移動: true=抑制 / false=Discordのみmuted(Xは常に出さない) |
| `dominance_ratio` | 0.6 | 相手を1社に名寄せする最低価値シェア |
| `batch_fanout_max` | 5 | 外部受取先がこれ超で「複数アドレスへ分散」 |
| `label_max_age_days` | 180 | labels.yaml の鮮度しきい値 |

## 制約事項

- ETF発行体別の細かい分離は不可。クラスタ単位の集計のみ。
- BTCはUTXOが分散しているため、監視アドレス外のフローは捕捉できない近似集計。
- 配信文言では「推定値」「主要wallet基準」と明示する。
