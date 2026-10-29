
"""Local GHL editorial memory. Standard library only."""
import json
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DB = ROOT / "ghl_memory.db"
BLOG = "https://ghartlabs.com/blog"


def db():
    c = sqlite3.connect(DB, timeout=15)
    c.execute("CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, kind TEXT, title TEXT, content TEXT, url TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, data TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY)")
    return c


def document(id, kind, title, content, url=""):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?)", (id, kind, title, content[:20000], url))


def thread(id, data=None):
    with db() as c:
        if data is not None:
            c.execute("INSERT OR REPLACE INTO threads VALUES (?,?)", (id, json.dumps(data)))
            return data
        row = c.execute("SELECT data FROM threads WHERE id=?", (id,)).fetchone()
        return json.loads(row[0]) if row else None


def first(id):
    with db() as c:
        return c.execute("INSERT OR IGNORE INTO events VALUES (?)", (id,)).rowcount == 1


class HTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.text, self.title, self.skip, self.heading = set(), [], "", False, False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "svg"):
            self.skip = True
        if tag == "a" and attrs.get("href"):
            self.links.add(urljoin(BLOG, attrs["href"]))
        if tag == "h1":
            self.heading = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "svg"):
            self.skip = False
        if tag == "h1":
            self.heading = False

    def handle_data(self, text):
        if text.strip() and not self.skip:
            self.text.append(text.strip())
            if self.heading:
                self.title += text.strip() + " "


def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=10) as response:
        html = response.read(2_000_000).decode("utf-8", "replace")
    parser = HTML()
    parser.feed(html)
    return parser


def sync():
    folder = ROOT / "knowledge"
    folder.mkdir(exist_ok=True)

    for f in folder.rglob("*"):
        if f.is_file() and f.suffix.lower() in (".md", ".txt"):
            document("file:" + str(f.relative_to(folder)), "reference", f.stem, f.read_text(errors="replace"))

    old = ROOT / "ghl_drafts.json"
    if old.exists():
        try:
            for key, item in json.loads(old.read_text()).items():
                title = item.get("title") or item.get("articleTitle")
                body = item.get("article") or item.get("body")
                if title and body:
                    document("legacy:" + key, "prior_draft", title, body)
        except (ValueError, AttributeError):
            print("Old draft import skipped")

    try:
        urls = sorted(
            u for u in fetch(BLOG).links
            if urlsplit(u).netloc == urlsplit(BLOG).netloc
            and urlsplit(u).path.startswith("/blog/")
        )[:25]

        for url in urls:
            try:
                page = fetch(url)
                document(url, "published", page.title.strip(), "\n".join(page.text), url)
            except Exception as e:
                print("Skipped article:", str(e)[:80])
    except Exception as e:
        print("Website sync unavailable:", str(e)[:100])


def context(query):
    with db() as c:
        rows = c.execute(
            "SELECT id, kind, title, content, url FROM docs"
        ).fetchall()

    terms = set(query.lower().split())

    rows.sort(
        key=lambda r: len(
            terms & set((r[2] + " " + r[3]).lower().split())
        ),
        reverse=True,
    )

    catalog = "\n".join(
        f"{r[0]} | {r[1]} | {r[2]}"
        for r in rows
    )

    excerpts = "\n\n".join(
        f"[{r[1]}] {r[2]}\n{r[3][:2000]}\n{r[4]}"
        for r in rows[:15]
    )

    return f"AVAILABLE CONTENT:\n{catalog}\n\nDETAILS:\n{excerpts}"