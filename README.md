# news-digest-data

ヘルスケア／ビジネスニュースの **収集結果（候補記事リスト）だけ** を置くデータ用リポジトリ。

- 毎日 JST 00:00 / 00:20 に Cloudflare Worker（Cron Trigger）が GitHub Actions を `workflow_dispatch` で起動し、Google News RSS を収集して `data/latest.json` を更新する（GitHub の `schedule` は遅延するため使わない）
- JST 06:00 に Claude のスケジュールタスクが `data/latest.json` を読み、記事の選定・日本語要約・HTMLダイジェストの更新を行う
- スコアリング・要約に外部APIキーは不要（Claude本体が実施）。メール配信は廃止

## 置いていないもの

- 検索クエリ・ウォッチ企業リスト等の設定（`config.yaml`）は GitHub Actions の Secret `CONFIG_YAML` に格納しており、このリポジトリには含まれない
- 収集結果に含まれるのは公開ニュースのタイトル・URL・媒体名・公開日時・スニペットのみ

## ファイル

| パス | 内容 |
| --- | --- |
| `fetch_news.py` | Google News RSS 収集スクリプト（収集のみ） |
| `.github/workflows/collect.yml` | 収集ジョブ（`workflow_dispatch` で起動） |
| `data/latest.json` | 直近の収集結果 |
| `data/archive/YYYY-MM-DD.json` | 過去30日分 |

## 設定を変更したいとき

```bash
gh secret set CONFIG_YAML --repo yoisk/news-digest-data < config.yaml
gh workflow run collect.yml --repo yoisk/news-digest-data   # 手動実行
```
