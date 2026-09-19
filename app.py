import os, sqlite3, hashlib, hmac
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from bs4 import BeautifulSoup
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
        LINE_SECRET.encode("utf-8"), body, hashlib.sha256
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

@app.get("/test-lottery")
def test_lottery_notification():
    expected = os.getenv("CHECK_TOKEN", "")
    provided = request.headers.get("Authorization", "")
    if not expected or provided != f"Bearer {expected}":
        abort(401)
    con = db()
    users = [r[0] for r in con.execute("SELECT user_id FROM users").fetchall()]
    con.close()
    sent = 0
    message = (
        "🎴 ポケカ抽選情報【テスト】\n\n"
        "ポケモンカードゲーム 新商品 抽選販売のお知らせ\n\n"
        "🗓 応募期間：テスト期間\n"
        "🏪 販売店：テスト店舗\n\n"
        "🔗 https://example.com/\n\n"
        "※これは通知動作確認用のテストです。"
    )
    for user in users:
        try:
            line_push(user, message)
            sent += 1
        except Exception as e:
            print("LINE lottery test push error:", e)
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
        con.execute(
            "INSERT OR IGNORE INTO users(user_id, created_at) VALUES(?,?)",
            (user, datetime.now(timezone.utc).isoformat())
        )
        con.commit()
        if event.get("type") == "follow":
            try:
                line_push(user, "🎴 ポケカ抽選通知Botです！\n友だち追加ありがとう。\n新しい抽選情報を見つけたら、このLINEに通知します。")
            except Exception as e:
                print("LINE push error:", e)
        elif event.get("type") == "message":
            try:
                line_push(user, "🎴 テストを受信しました！\nLINE通知Botは正常に接続されています。\nこのまま友だち登録しておけば、抽選情報を通知します👍")
            except Exception as e:
                print("LINE message reply error:", e)
    con.close()
    return "OK"

def google_news_url(query):
    return (
        "https://news.google.com/rss/search?"
        f"q={quote(query)}&hl=ja&gl=JP&ceid=JP:ja"
    )

QUERIES = [
    "ポケカ 抽選", "ポケモンカード 抽選", "ポケカ 応募", "ポケモンカード 応募",
    "ポケカ 予約 抽選", "ポケモンカード 予約 抽選", "ポケカ BOX 抽選",
    "ポケモンカード BOX 抽選", "ポケカ 抽選受付", "ポケモンカード 抽選受付",
    "ポケカ 抽選開始", "ポケモンカード 抽選開始",
    "ポケモンセンター ポケカ 抽選", "Amazon ポケカ 抽選", "楽天 ポケカ 抽選",
    "ヨドバシ ポケカ 抽選", "ビックカメラ ポケカ 抽選", "Joshin ポケカ 抽選",
    "ヤマダ電機 ポケカ 抽選", "TSUTAYA ポケカ 抽選", "GEO ポケカ 抽選",
    "セブンネット ポケカ 抽選", "イオン ポケカ 抽選", "トイザらス ポケカ 抽選",
    "カードショップ ポケカ 抽選",
    "GEO ポケカ 抽選販売", "ゲオ ポケカ 抽選販売", "GEO ポケモンカード 抽選",
    "Amazon ポケカ 抽選販売", "Amazon ポケモンカード 抽選",
    "ビックカメラ ポケカ 抽選販売", "ビックカメラ ポケモンカード 抽選",
    "ヨドバシ ポケカ 抽選販売", "ヨドバシ ポケモンカード 抽選",
    "バトロコ ポケカ 抽選", "バトロコ ポケモンカード 抽選",
    "PAO ポケカ 抽選", "PAO ポケモンカード 抽選", "流星のPAO ポケカ 抽選",
    "エディオン ポケカ 抽選", "EDION ポケカ 抽選", "エディオン ポケモンカード 抽選",
    "site:livepocket.jp/e ポケカ 抽選",
    "site:livepocket.jp/e ポケモンカード 抽選",
    "site:livepocket.jp/e ポケカ 応募",
    "site:livepocket.jp/e ポケモンカード 予約 抽選",
]

CARD_WORDS = ("ポケカ", "ポケモンカード", "ポケモンカードゲーム")
ACTION_WORDS = ("抽選", "応募", "予約", "受付")
EXCLUDE_WORDS = (
    "調査", "アンケート", "ランキング", "実態", "意識調査", "市場調査",
    "レビュー", "開封", "買取", "価格", "高騰", "相場", "当選報告",
    "当選者", "当選結果", "当選発表", "結果発表", "集計", "当選率",
    "まとめ", "攻略", "コラム", "ニュース", "最新ニュース",
    "ニュース記事", "報道", "メディア"
)

DIRECT_LIVEPOCKET = os.getenv("DIRECT_LIVEPOCKET", "true").lower() in ("1", "true", "yes", "on")
LIVEPOCKET_SEARCH_QUERIES = ("ポケカ", "ポケモンカード")

OFFICIAL_LIST_PAGES = (
    # Pokémon公式
    ("PokemonCenterOnline", "https://www.pokemoncenter-online.com/news/"),
    ("PokemonCenter", "https://shop.pokemon.co.jp/ja/"),
    # 大手量販店・家電
    ("GEO", "https://geo-online.co.jp/news/"),
    ("BicCamera", "https://www.biccamera.com/bc/c/info/order/lottery.jsp"),
    ("EDION", "https://www.edion.com/special.html"),
    ("Yodobashi", "https://www.yodobashi.com/?word=%E3%83%9D%E3%82%B1%E3%83%A2%E3%83%B3%E3%82%AB%E3%83%BC%E3%83%89+%E6%8A%BD%E9%81%B8"),
    ("Joshin", "https://store.joshin.co.jp/"),
    ("Yamada", "https://www.yamada-denki.jp/"),
    ("Kojima", "https://www.kojima.net/"),
    ("Nojima", "https://www.nojima.co.jp/"),
    ("Sofmap", "https://www.sofmap.com/"),
    # 総合小売・EC
    ("Amazon", "https://www.amazon.co.jp/s?k=%E3%83%9D%E3%82%B1%E3%83%A2%E3%83%B3%E3%82%AB%E3%83%BC%E3%83%89+%E6%8A%BD%E9%81%B8"),
    ("7net", "https://7net.omni7.jp/"),
    ("AEON", "https://www.aeonretail.jp/"),
    ("Donki", "https://www.donki.com/"),
    ("ItoYokado", "https://www.itoyokado.co.jp/"),
    ("ToysRUs", "https://www.toysrus.co.jp/"),
    # トレカ・カードショップ
    ("PAO", "https://pao-onlineshop.com/view/news/list"),
    ("Bato-Loco", "https://bato-loco.com/"),
    ("DragonStar", "https://www.dragonstar.co.jp/"),
    ("CardBox", "https://cardbox.sc/"),
    ("BeeHonpo", "https://www.beehonpo.com/"),
    ("Hareruya2", "https://www.hareruya2.com/"),
    ("Surugaya", "https://www.suruga-ya.jp/"),
    ("BookOff", "https://www.bookoff.co.jp/"),
)

# 公式ページから直接拾う監視先。Google Newsは補助として残すが、
# 抽選・応募・予約・受付に関係する公式ページを優先する。


def livepocket_search_url(query):
    return "https://livepocket.jp/event/search?" + quote("keyword") + "=" + quote(query)


def fetch_livepocket_items():
    if not DIRECT_LIVEPOCKET:
        return []
    seen = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PokecaLotteryBot/1.0)",
        "Accept-Language": "ja-JP,ja;q=0.9",
    }
    for q in LIVEPOCKET_SEARCH_QUERIES:
        try:
            r = requests.get(livepocket_search_url(q), headers=headers, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select('a[href*="/e/"]'):
                href = a.get("href", "").strip()
                if not href.startswith("/e/"):
                    continue
                url = "https://livepocket.jp" + href.split("?")[0]
                card = a.find_parent(["article", "li", "div"])
                text = (card.get_text(" ", strip=True) if card else a.get_text(" ", strip=True)).strip()
                title = a.get_text(" ", strip=True) or text[:200]
                hay = (title + " " + text).lower()
                if not any(w.lower() in hay for w in CARD_WORDS):
                    continue
                if not any(w.lower() in hay for w in ACTION_WORDS):
                    continue
                if any(w.lower() in hay for w in EXCLUDE_WORDS):
                    continue
                if any(w in text for w in ("受付終了", "販売終了", "募集終了")):
                    continue
                if not any(w in text for w in ("抽選", "応募", "受付", "販売前", "販売中")):
                    continue
                key = hashlib.sha256(url.encode()).hexdigest()
                seen[key] = (key, title, url, "")
        except Exception as e:
            print("LivePocket direct monitor error:", q, e)
    return list(seen.values())


def fetch_official_list_items():
    seen = {}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=3)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PokecaLotteryBot/1.0)",
        "Accept-Language": "ja-JP,ja;q=0.9",
    }
    for shop, page_url in OFFICIAL_LIST_PAGES:
        try:
            r = requests.get(page_url, headers=headers, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            # 各社公式ページ内の「抽選・応募・予約・受付」に関係するリンクを直接監視。
            for a in soup.select('a[href]'):
                href = a.get("href", "").strip()
                title = a.get_text(" ", strip=True)
                if not href or not title:
                    continue

                parent = a.find_parent(["article", "li", "div", "tr", "section"]) or a.parent
                text = parent.get_text(" ", strip=True) if parent else title
                hay = (title + " " + text).lower()

                if not any(w.lower() in hay for w in CARD_WORDS):
                    continue
                if not any(w.lower() in hay for w in ACTION_WORDS):
                    continue
                if any(w.lower() in title.lower() for w in EXCLUDE_WORDS):
                    continue
                if any(w in text for w in ("受付終了", "募集終了", "販売終了")):
                    continue

                url = requests.compat.urljoin(page_url, href.split("?")[0])

                if shop == "PokemonCenterOnline":
                    if "pokemoncenter-online.com" not in url:
                        continue
                elif shop == "PokemonCenter":
                    if "pokemon.co.jp" not in url and "shop.pokemon.co.jp" not in url:
                        continue
                elif shop == "GEO":
                    if "geo-online.co.jp" not in url:
                        continue
                elif shop == "BicCamera":
                    if "biccamera.com" not in url:
                        continue
                elif shop == "EDION":
                    if "edion.com" not in url:
                        continue
                elif shop == "PAO":
                    if "pao-onlineshop.com" not in url:
                        continue
                elif shop == "Bato-Loco":
                    if "bato-loco.com" not in url:
                        continue
                elif shop == "Amazon":
                    if "amazon.co.jp" not in url:
                        continue
                elif shop == "Yodobashi":
                    if "yodobashi.com" not in url:
                        continue
                elif shop == "Joshin":
                    if "joshin.co.jp" not in url:
                        continue
                elif shop == "Yamada":
                    if "yamada-denki.jp" not in url:
                        continue
                elif shop == "Kojima":
                    if "kojima.net" not in url:
                        continue
                elif shop == "Nojima":
                    if "nojima.co.jp" not in url:
                        continue
                elif shop == "Sofmap":
                    if "sofmap.com" not in url:
                        continue
                elif shop == "7net":
                    if "7net.omni7.jp" not in url:
                        continue
                elif shop == "AEON":
                    if "aeonretail.jp" not in url:
                        continue
                elif shop == "Donki":
                    if "donki.com" not in url:
                        continue
                elif shop == "ItoYokado":
                    if "itoyokado.co.jp" not in url:
                        continue
                elif shop == "ToysRUs":
                    if "toysrus.co.jp" not in url:
                        continue
                elif shop == "DragonStar":
                    if "dragonstar.co.jp" not in url:
                        continue
                elif shop == "CardBox":
                    if "cardbox.sc" not in url:
                        continue
                elif shop == "BeeHonpo":
                    if "beehonpo.com" not in url:
                        continue
                elif shop == "Hareruya2":
                    if "hareruya2.com" not in url:
                        continue
                elif shop == "Surugaya":
                    if "suruga-ya.jp" not in url:
                        continue
                elif shop == "BookOff":
                    if "bookoff.co.jp" not in url:
                        continue

                import re
                m = re.search(r"(20\d{2})[./年](\d{1,2})[./月](\d{1,2})", text)
                published_dt = None
                if m:
                    published_dt = datetime(
                        int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        tzinfo=timezone.utc
                    )
                    if published_dt < cutoff or published_dt > now + timedelta(days=1):
                        continue

                key = hashlib.sha256(url.encode()).hexdigest()
                seen[key] = (key, title, url, published_dt.isoformat() if published_dt else "")

        except Exception as e:
            print("Official shop monitor error:", shop, e)
    return list(seen.values())

def parse_published(entry):
    value = entry.get("published_parsed") or entry.get("updated_parsed")
    if not value:
        return None
    try:
        from calendar import timegm
        return datetime.fromtimestamp(timegm(value), tz=timezone.utc)
    except Exception:
        return None

ENABLE_GOOGLE_NEWS = os.getenv("ENABLE_GOOGLE_NEWS", "false").lower() in ("1", "true", "yes", "on")

def fetch_items():
    if not ENABLE_GOOGLE_NEWS:
        return []
    seen = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=3)
    for q in QUERIES:
        feed = feedparser.parse(google_news_url(q))
        for e in feed.entries:
            title = e.get("title", "").strip()
            url = e.get("link", "").strip()
            published = e.get("published", "")
            if not title or not url:
                continue
            summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True)
            hay = (title + " " + summary).lower()
            if not any(word.lower() in hay for word in CARD_WORDS):
                continue
            if not any(word.lower() in hay for word in ACTION_WORDS):
                continue
            if any(word.lower() in hay for word in EXCLUDE_WORDS):
                continue

            # LivePocket専用検索は、実際のLivePocketページだけを通す。
            # 通常検索から拾った記事についても、ニュース記事などは既存フィルターで除外する。
            is_livepocket = "livepocket.jp/e/" in url.lower()
            is_livepocket_query = q.startswith("site:livepocket.jp/e")
            if is_livepocket_query and not is_livepocket:
                continue

            published_dt = parse_published(e)
            if published_dt is None or published_dt < cutoff or published_dt > now + timedelta(hours=1):
                continue
            key = hashlib.sha256(url.encode()).hexdigest()
            seen.append((key, title, url, published))

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
    all_items = fetch_items() + fetch_livepocket_items() + fetch_official_list_items()
    dedup = {}
    for row in all_items:
        dedup[row[0]] = row
    for key, title, url, published in dedup.values():
        exists = con.execute("SELECT 1 FROM items WHERE item_key=?", (key,)).fetchone()
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
