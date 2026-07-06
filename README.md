# estat-pipeline — パン市場定点データの自動取得

家計調査・CPI等の定点統計をe-Stat APIから毎月自動取得し、`data/` にCSVで蓄積するリポジトリ。
生活者変化スキャニング（パン市場）の「定点（メガトレンド背景）」レイヤーのデータ基盤。

このリポジトリは**公開**を前提とする（Claudeのチャット環境が raw.githubusercontent.com からデータを直接読むため）。
**分析メモ・解釈はこのリポジトリに置かない**（データと分析の分離）。

## セットアップ（初回のみ）

1. **e-StatのアプリケーションID取得**（無料）
   - https://www.e-stat.go.jp/api/ からユーザ登録 → マイページでアプリケーションID発行（最大3つ）
2. **このリポジトリをGitHubに作成**（Public）し、ファイル一式をpush
3. **Secretsの設定**: リポジトリ Settings → Secrets and variables → Actions → New repository secret
   - Name: `ESTAT_APP_ID` / Value: 発行したID
   - ※IDをコードやコミットに直接書かないこと
4. **統計表IDの初回確認**（ローカルまたはActionsのworkflow_dispatchで）
   ```bash
   export ESTAT_APP_ID=xxxx
   pip install requests pandas
   python scripts/fetch_estat.py discover "品目分類" --stats-code 00200561   # 家計調査の表を検索
   python scripts/fetch_estat.py meta 0003348231                             # 表題・品目分類を確認
   ```
   - `targets.json` の家計調査ID（0003348231）は**推定値**。metaで「二人以上の世帯・品目分類(2020年改定)・月次・金額」で、穀類（米/食パン/他のパン/麺類）と主食的調理食品を含むことを確認してから運用に入る。
   - 確認できたら必要に応じて `targets.json` を修正
5. **手動実行テスト**: Actions → fetch-estat → Run workflow

## 運用

- 毎月10日・25日（JST朝6時ごろ）に自動実行し、差分があればコミット
- Claudeとの分析セッションでは、リポジトリの `data/*.csv` のraw URLを渡すだけでよい
  （例: `https://raw.githubusercontent.com/<user>/<repo>/main/data/kakei_hinmoku_kingaku_2plus_monthly.csv`）

## 対象の追加

`targets.json` に追記する。絞り込みが必要な大きい表は `params` を使う:
```json
{
  "name": "example",
  "statsDataId": "00000000",
  "enabled": true,
  "params": {"cdCat01": "010,011", "cdArea": "00000"},
  "note": "..."
}
```
コードは `python scripts/fetch_estat.py meta <statsDataId>` で確認できる。

## 既知の制約・注意（重要）

- **品目分類の改定**: 家計調査の収支項目分類は原則5年ごとに改定され、**統計表IDと品目コードが変わる**
  （実例: https://www.e-stat.go.jp/api/info-cat/news/kakei-info ）。改定告知が出たら `targets.json` を差し替える。
  旧分類と新分類の系列接続は取得側ではなく**分析側の作業**として扱う。
- **クレジット表示**: e-Stat APIの利用にはクレジット表示が必要（商用利用可）。
  詳細: https://www.e-stat.go.jp/api/api-dev/faq および e-Stat利用規約。
  本リポジトリおよび成果物には「このサービスは、政府統計総合窓口(e-Stat)のAPI機能を使用しています」等の表示を行う。
- **APIの上限**: 1リクエスト最大10万件。超過分はNEXT_KEYで自動ページング（実装済み）。
- **cronの精度**: GitHub ActionsのスケジュールはUTC固定・遅延あり・60日無活動で自動停止
  （https://docs.github.com/en/actions/managing-workflow-runs/disabling-and-enabling-a-workflow ）。
  毎月コミットが発生する限り自動停止は実質回避されるが、Actionsタブでの月次目視確認を推奨。

## 構成

```
scripts/fetch_estat.py   # discover / meta / fetch の3モード
targets.json             # 取得対象（statsDataIdと絞り込み）
.github/workflows/fetch-estat.yml
data/                    # 取得結果（CSV + 取得情報JSON）
data/meta/               # 分類コード一覧（metaモードの出力）
```
