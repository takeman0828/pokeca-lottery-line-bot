# ポケカ抽選 → LINE通知Bot

## できること

- 「ポケカ 抽選」「ポケモンカード 抽選」などの公開検索結果を定期チェック
- 新しく見つかったページだけSQLiteに記録
- LINE公式アカウントを友だち追加したユーザーへ通知
- 応募操作そのものは自動化しない

「世の中の全サイトを完全網羅」は技術的に保証できません。検索エンジンに載る公開情報を広く拾い、必要なショップはQUERIESへ検索語を追加する方式です。

## 1. LINE側

LINE DevelopersでLINE公式アカウントにMessaging APIを有効化し、Channel access tokenとChannel secretを取得します。

Webhook URL:
`https://あなたのサーバー/callback`

Webhookを有効化してください。

## 2. 起動

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# .env等で環境変数を設定
python app.py
```

本番ではgunicorn等で起動してください。

## 3. 5分ごとにチェック

Linuxならcronで:

```cron
*/5 * * * * cd /path/to/pokeca_lottery_line_bot && /path/to/.venv/bin/python run_check.py >> bot.log 2>&1
```

サーバーが常時稼働できる環境なら、cron/スケジューラで `run_check.py` を5〜10分おきに実行します。

## 4. 検索語を増やす

`app.py` の `QUERIES` に追加できます。

例:
- ポケカ 抽選 ヨドバシ
- ポケカ 抽選 ビックカメラ
- ポケカ 抽選 ジョーシン
- ポケカ 抽選 楽天
- ポケカ 抽選 Amazon
- ポケカ 抽選 ポケモンセンター
- ポケモンカード 抽選 店舗

## 注意

各サイトの利用規約・robots.txt等を尊重してください。
このBotは抽選への応募、ログイン、購入、CAPTCHA回避などを自動化しません。
LINEのChannel access tokenは秘密情報なので、GitHub等へ公開しないでください。
