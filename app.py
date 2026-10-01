import os, sqlite3, hashlib, hmac
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlsplit
from bs4 import BeautifulSoup
import requests
import feedparser
from flask import Flask, request, abort

DB = os.getenv("DB_PATH", "pokeca.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_SECRET = os.environ["LINE_CHANNEL_SECRET"]
PORT = int(os.getenv("PORT", "8080"))

app = Flask(__name__)

class DBAdapter:
    """Small adapter so the app can use either SQLite or persistent PostgreSQL."""
    def __init__(self, connection, postgres=False):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.connection.execute(sql, params)

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def db():
    if DATABASE_URL:
        import psycopg
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        con = DBAdapter(psycopg.connect(url), postgres=True)
    else:
        con = DBAdapter(sqlite3.connect(DB))

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
        timeout=4,
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

@app.get("/db-status")
def db_status():
    expected = os.getenv("CHECK_TOKEN", "")
    provided = request.headers.get("Authorization", "")
    if not expected or provided != f"Bearer {expected}":
        abort(401)
    con = db()
    try:
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        items = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        return {
            "ok": True,
            "backend": "postgresql" if DATABASE_URL else "sqlite",
            "registered_users": users,
            "saved_items": items,
        }
    finally:
        con.close()

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
            "INSERT INTO users(user_id, created_at) VALUES(?,?) ON CONFLICT(user_id) DO NOTHING",
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

DEFAULT_QUERIES = [
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

def load_queries():
    path = os.path.join(os.path.dirname(__file__), "queries.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            queries = [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
        return list(dict.fromkeys(queries + DEFAULT_QUERIES))
    except Exception as e:
        print("queries.txt load error:", e)
        return DEFAULT_QUERIES

QUERIES = load_queries()

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
            r = requests.get(livepocket_search_url(q), headers=headers, timeout=5)
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
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PokecaLotteryBot/1.0)",
        "Accept-Language": "ja-JP,ja;q=0.9",
    }
    for shop, page_url in OFFICIAL_LIST_PAGES:
        try:
            r = requests.get(page_url, headers=headers, timeout=5)
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

                # Match card/action terms in the link plus its nearby text.
                # Official lottery links often have short labels such as "応募はこちら".
                if not any(w.lower() in hay for w in CARD_WORDS):
                    continue
                if not any(w.lower() in hay for w in ACTION_WORDS):
                    continue
                # Exclude news/editorial links by their own title, not the entire parent block.
                if any(w.lower() in title.lower() for w in EXCLUDE_WORDS):
                    continue
                if any(w in text for w in ("受付終了", "募集終了", "販売終了")):
                    continue

                url = requests.compat.urljoin(page_url, href.split("?")[0])

                if is_generic_search_url(url):
                    continue
                parsed_url = urlsplit(url)
                if not parsed_url.path or parsed_url.path == "/":
                    continue

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
                    # Never notify generic Amazon search pages as lottery listings.
                    if url.rstrip("/").lower() in ("https://www.amazon.co.jp/s", "https://amazon.co.jp/s"):
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
                    if published_dt > now + timedelta(days=1):
                        continue

                key = hashlib.sha256(url.encode()).hexdigest()
                seen[key] = (key, title, url, published_dt.isoformat() if published_dt else "")

        except Exception as e:
            print("Official shop monitor error:", shop, e)
    return list(seen.values())

def is_generic_search_url(url):
    """Reject generic search pages that are not a specific lottery/product page."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "/").rstrip("/").lower() or "/"
    if host == "amazon.co.jp" or host.endswith(".amazon.co.jp"):
        return path in ("/s", "/gp/search", "/gp/aw/s")
    return False


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
    # Google News articles are intentionally disabled. Monitor direct LivePocket
    # event pages and official retailer pages instead, to avoid news-article noise.
    return []

def notify_new_items():
    con = db()
    users = [r[0] for r in con.execute("SELECT user_id FROM users").fetchall()]

    # Remove legacy false positives that may have been saved by older versions.
    # Google News articles are no longer a supported source.
    for item_key, item_url in con.execute("SELECT item_key, url FROM items").fetchall():
        host = (urlsplit(item_url).hostname or "").lower()
        if is_generic_search_url(item_url) or host == "news.google.com":
            con.execute("DELETE FROM items WHERE item_key=?", (item_key,))
    con.commit()

    # ユーザー登録の有無に関係なく先に情報源をスキャンする。
    google_items = fetch_items()
    livepocket_items = fetch_livepocket_items()
    official_items = fetch_official_list_items()
    all_items = google_items + livepocket_items + official_items

    print(
        "SCAN COUNTS:",
        f"google={len(google_items)}",
        f"livepocket={len(livepocket_items)}",
        f"official={len(official_items)}",
        f"users={len(users)}"
    )

    if not users:
        print("NO REGISTERED LINE USERS")
        con.close()
        return {
            "new": 0,
            "users": 0,
            "sent": 0,
            "failed": 0,
            "sources": {
                "google": len(google_items),
                "livepocket": len(livepocket_items),
                "official": len(official_items),
                "unique": len(set(row[0] for row in all_items)),
                "candidate_titles": [row[1] for row in all_items[:10]],
                "candidate_urls": [row[2] for row in all_items[:10]],
            },
        }

    dedup = {}
    for row in all_items:
        dedup[row[0]] = row

    new_count = 0
    sent_count = 0
    failed_count = 0

    for key, title, url, published in dedup.values():
        exists = con.execute("SELECT 1 FROM items WHERE item_key=?", (key,)).fetchone()
        if exists:
            continue

        msg = f"🎴 ポケカ抽選情報\n\n{title}\n\n🔗 {url}"
        item_sent = 0
        for user in users:
            try:
                line_push(user, msg)
                item_sent += 1
                sent_count += 1
            except Exception as e:
                failed_count += 1
                print("LINE push error:", e)

        # 1人以上への送信成功後だけ既読登録する。
        # 送信失敗なら次回チェックで再試行できる。
        if item_sent > 0:
            con.execute(
                "INSERT INTO items(item_key,title,url,published,created_at) VALUES(?,?,?,?,?)",
                (key, title, url, published, datetime.now(timezone.utc).isoformat())
            )
            con.commit()
            new_count += 1
        else:
            print("ITEM NOT MARKED AS SENT:", title)

    con.close()
    return {
        "new": new_count,
        "users": len(users),
        "sent": sent_count,
        "failed": failed_count,
        "sources": {
            "google": len(google_items),
            "livepocket": len(livepocket_items),
            "official": len(official_items),
            "unique": len(dedup),
            "candidate_titles": [row[1] for row in list(dedup.values())[:10]],
            "candidate_urls": [row[2] for row in list(dedup.values())[:10]],
        },
    }

if __name__ == "__main__":
    print("new:", notify_new_items())
