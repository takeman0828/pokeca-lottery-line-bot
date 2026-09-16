import os, sqlite3, hashlib, hmac
from datetime import datetime, timezone
from urllib.parse import quote
import requests
import feedparser
from flask import Flask, request, abort

DB = os.getenv("DB_PATH", "pokeca.db")
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
PORT = int(os.getenv("PORT", "8080"))

app = Flask(__name__)

def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS items(
        item_key TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        published TEXT,
        created_at TEXT NOT NULL
    )""")
    con.commit()
    return con

def line_push(user_id, text):
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"to": user_id, "messages": [{"type": "text", "text": text}]},
        timeout=20,
    )
    r.raise_for_status()

def verify_signature(body: bytes, signature: str) -> bool:
    digest = hmac.new(
        LINE_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    import base64
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature or "")

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/check")
def check():
    expected = os.getenv("CHECK_TOKEN", "")
    provided = request.headers.get("Authorization", "")
    if not expected or provided != f"Bearer {expected}":
        abort(401)
    return {"new": notify_new_items()}

@app.get("/test")
def test_notification():
    expected = os.getenv("CHECK_TOKEN", "")
    provided = request.headers.get("Authorization", "")
    if not expected or provided != f"Bearer {expected}":
        abort(401)

    con = db()
    users = [r[0] for r in con.execute("SELECT user_id FROM users").fetchall()]
    con.close()

    sent = 0
    for user in users:
        try:
            line_push(user, "🎴 ポケカ抽選通知Bot\n\nこれはテスト通知です！\nLINE通知が正常に届くことを確認しました。👍")
            sent += 1
        except Exception as e:
            print("LINE test push error:", e)

    return {"sent": sent, "registered_users": len(users)}

@app.post("/callback")
def callback():
    body = request.get_data()
    if not verify_signature(body, request.headers.get("X-Line-Signature")):
        abort(400)

    data = request.get_json(silent=True) or {}
    con = db()

    for event in data.get("events", []):
        user = event.get("source", {}).get("userId")
        if not user:
            continue
        if event.get("type") == "follow":
            con.execute(
                "INSERT OR IGNORE INTO users(user_id, created_at) VALUES(?,?)",
                (user, datetime.now(timezone.utc).isoformat())
            )
            con.commit()
            try:
                line_push(user, "🎴 ポケカ抽選通知Botです！\n友だち追加ありがとう。\n新しい抽選情報を見つけたら、このLINEに通知します。")
            except Exception as e:
                print("LINE push error:", e)
    con.close()
    return "OK"

def google_news_url(query):
    return (
        "https://news.google.com/rss/search?"
        f"q={quote(query)}&hl=ja&gl=JP&ceid=JP:ja"
    )

# ポケカの抽選・応募情報をできるだけ広く拾う検索語。
# 店舗名だけでなく、抽選/応募/予約/当選/入荷などの表現も監視する。
QUERIES = [
    "ポケカ 抽選",
    "ポケモンカード 抽選",
    "ポケカ 応募",
    "ポケモンカード 応募",
    "ポケカ 予約 抽選",
    "ポケモンカード 予約 抽選",
    "ポケカ BOX 抽選",
    "ポケモンカード BOX 抽選",
    "ポケカ 当選",
    "ポケモンカード 当選",
    "ポケカ 入荷 抽選",
    "ポケモンカード 入荷 抽選",
    "ポケカ 抽選受付",
    "ポケモンカード 抽選受付",
    "ポケカ 抽選開始",
    "ポケモンカード 抽選開始",
    "ポケモンセンター ポケカ 抽選",
    "Amazon ポケカ 抽選",
    "楽天 ポケカ 抽選",
    "ヨドバシ ポケカ 抽選",
    "ビックカメラ ポケカ 抽選",
    "Joshin ポケカ 抽選",
    "ヤマダ電機 ポケカ 抽選",
    "TSUTAYA ポケカ 抽選",
    "GEO ポケカ 抽選",
    "セブンネット ポケカ 抽選",
    "イオン ポケカ 抽選",
    "トイザらス ポケカ 抽選",
    "カードショップ ポケカ 抽選",
]

CARD_WORDS = ("ポケカ", "ポケモンカード", "ポケモンカードゲーム")
LOTTERY_WORDS = ("抽選", "応募", "予約", "当選", "受付", "入荷")

def fetch_items():
    seen = []
    for q in QUERIES:
        feed = feedparser.parse(google_news_url(q))
        for e in feed.entries:
            title = e.get("title", "").strip()
            url = e.get("link", "").strip()
            published = e.get("published", "")
            if not title or not url:
                continue

            # ポケカ関連かつ、抽選・応募などの販売情報だけを通知対象にする。
            hay = title.lower()
            if not any(word.lower() in hay for word in CARD_WORDS):
                continue
            if not any(word.lower() in hay for word in LOTTERY_WORDS):
                continue

            key = hashlib.sha256(url.encode()).hexdigest()
            seen.append((key, title, url, published))

    # URL重複を除去
    out, keys = [], set()
    for row in seen:
        if row[0] not in keys:
            keys.add(row[0])
            out.append(row)
    return out

def notify_new_items():
    con = db()
    users = [r[0] for r in con.execute("SELECT user_id FROM users").fetchall()]
    count = 0

    for key, title, url, published in fetch_items():
        exists = con.execute(
            "SELECT 1 FROM items WHERE item_key=?", (key,)
        ).fetchone()
        if exists:
            continue

        con.execute(
            "INSERT INTO items(item_key,title,url,published,created_at) VALUES(?,?,?,?,?)",
            (key, title, url, published, datetime.now(timezone.utc).isoformat())
        )
        con.commit()

        msg = f"🎴 ポケカ抽選情報\n\n{title}\n\n🔗 {url}"
        for user in users:
            try:
                line_push(user, msg)
            except Exception as e:
                print("LINE push error:", e)
        count += 1

    con.close()
    return count

if __name__ == "__main__":
    print("new:", notify_new_items())
