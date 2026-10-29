
"""GHL Content Engine — automatic content ingestion.

Commands:
    python3 ghl_auto_ingest.py --once
    python3 ghl_auto_ingest.py --bot
    python3 ghl_auto_ingest.py --watch
    python3 ghl_auto_ingest.py --import-linkedin URL

Uses the existing ghl_memory.db and .env.
Does not publish anything to Sanity.
"""

import argparse
import hashlib
import os
import re
import runpy
import sqlite3
import subprocess
import threading
import time

from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DB = ROOT / "ghl_memory.db"

BASE = "https://ghartlabs.com"
BLOG = BASE + "/blog"

SKIP_TAGS = {
    "script", "style", "svg", "noscript",
    "nav", "footer", "header", "aside", "form"
}

BLOCK_TAGS = {
    "h1", "h2", "h3", "h4", "p", "li",
    "blockquote", "section", "article",
    "div", "br", "tr"
}

VOID_TAGS = {
    "area", "base", "br", "col", "embed",
    "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr"
}


# ============================================================
# WEBSITE TEXT EXTRACTION
# ============================================================

class WebPage(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.stack = []
        self.links = set()
        self.h1 = []

        self.all = []
        self.main = []
        self.article = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)

        if tag == "a" and attributes.get("href"):
            self.links.add(attributes["href"])

        if tag not in VOID_TAGS:
            self.stack.append(tag)

        if tag in BLOCK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS:
            self._append("\n")

        if tag in self.stack:
            index = (
                len(self.stack)
                - 1
                - self.stack[::-1].index(tag)
            )

            self.stack = self.stack[:index]

    def _append(self, value):
        if any(tag in SKIP_TAGS for tag in self.stack):
            return

        self.all.append(value)

        if "main" in self.stack:
            self.main.append(value)

        if "article" in self.stack:
            self.article.append(value)

    def handle_data(self, data):
        if not data.strip():
            return

        if any(tag in SKIP_TAGS for tag in self.stack):
            return

        self._append(data.strip() + " ")

        if "h1" in self.stack:
            self.h1.append(data.strip())

    def content(self):
        chunks = self.article or self.main or self.all

        content = "".join(chunks)

        content = re.sub(
            r"[ \t]+", " ", content
        )

        content = re.sub(
            r"\n\s*\n+", "\n\n", content
        )

        return content.strip()

    def title(self):
        return " ".join(self.h1).strip()


def get_page(url):
    request = Request(
        url,
        headers={
            "User-Agent": "GHLContentEngine/1.0"
        }
    )

    with urlopen(request, timeout=20) as response:
        final = response.geturl()

        if urlsplit(final).netloc.lower() not in {
            "ghartlabs.com",
            "www.ghartlabs.com"
        }:
            raise ValueError(
                "Unexpected redirect outside GHL website"
            )

        raw = response.read(3_000_000)

    parser = WebPage()
    parser.feed(
        raw.decode("utf-8", errors="replace")
    )

    return parser


def canonical(url):
    parsed = urlsplit(url)

    if parsed.scheme != "https" or not parsed.netloc:
        return ""

    path = parsed.path.rstrip("/") or "/"

    return urlunsplit((
        "https",
        parsed.netloc.lower(),
        path,
        "",
        ""
    ))


def discover_blog_articles(index):
    found = set()

    for link in index.links:
        url = canonical(urljoin(BLOG, link))
        parsed = urlsplit(url)

        if (
            parsed.netloc in {
                "ghartlabs.com",
                "www.ghartlabs.com"
            }
            and parsed.path.startswith("/blog/")
        ):
            found.add(url)

    return sorted(found)


# ============================================================
# SQLITE DATABASE
# ============================================================

def open_database():
    connection = sqlite3.connect(
        DB,
        timeout=20
    )

    connection.execute(
        "PRAGMA busy_timeout=20000"
    )

    connection.execute("""
        CREATE TABLE IF NOT EXISTS docs (
            id TEXT PRIMARY KEY,
            kind TEXT,
            title TEXT,
            content TEXT,
            url TEXT
        )
    """)

    columns = [
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(docs)"
        )
    ]

    expected = [
        "id", "kind", "title", "content", "url"
    ]

    if columns != expected:
        connection.close()
        raise RuntimeError(
            f"Unexpected database schema: {columns}"
        )

    return connection


def upsert(
    kind,
    title,
    content,
    url="",
    key="",
    replace_existing=True
):
    """
    Insert a new document or update an existing one.

    Prefer matching by URL to avoid duplicates.
    Never replace long content with a short extraction.
    """

    title = title.strip()
    content = content.strip()

    url = canonical(url) if url else ""

    if not title or len(content) < 200:
        return "skipped"

    with open_database() as db:
        row = None

        if url:
            row = db.execute(
                """
                SELECT id, kind, content, url
                FROM docs
                WHERE url = ?
                   OR RTRIM(url, '/') = ?
                LIMIT 1
                """,
                (url, url.rstrip("/"))
            ).fetchone()

        if row is None and key:
            row = db.execute(
                """
                SELECT id, kind, content, url
                FROM docs
                WHERE id = ?
                """,
                (key,)
            ).fetchone()

        if row is None and kind == "published":
            row = db.execute(
                """
                SELECT id, kind, content, url
                FROM docs
                WHERE kind = 'published'
                  AND title = ?
                LIMIT 1
                """,
                (title,)
            ).fetchone()

        if row:
            old_id, old_kind, old_content, old_url = row

            if (
                not replace_existing
                or len(old_content or "") > len(content) * 1.3
            ):
                if url and not old_url:
                    db.execute(
                        """
                        UPDATE docs
                        SET url = ?
                        WHERE id = ?
                        """,
                        (url, old_id)
                    )

                    return "linked"

                return "unchanged"

            if (
                (old_content or "") == content
                and (old_url or "") == url
                and old_kind == kind
            ):
                return "unchanged"

            db.execute(
                """
                UPDATE docs
                SET kind = ?,
                    title = ?,
                    content = ?,
                    url = ?
                WHERE id = ?
                """,
                (
                    kind,
                    title,
                    content,
                    url or old_url or "",
                    old_id
                )
            )

            return "updated"

        if not key:
            key = (
                "ingest:"
                + hashlib.sha256(
                    (url or title).encode()
                ).hexdigest()[:24]
            )

        db.execute(
            """
            INSERT INTO docs (
                id, kind, title, content, url
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, kind, title, content, url)
        )

        return "added"


# ============================================================
# GHL WEBSITE SYNC
# ============================================================

def sync_website():
    pages = [
        BASE + "/",
        BASE + "/production",
        BASE + "/gate"
    ]

    try:
        index = get_page(BLOG)

        pages.extend(
            discover_blog_articles(index)
        )

    except Exception as error:
        print(
            "[site] Blog discovery failed:",
            str(error)[:180],
            flush=True
        )

    summary = {
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "linked": 0,
        "skipped": 0,
        "failed": 0
    }

    for url in dict.fromkeys(pages):
        try:
            page = get_page(url)

            title = (
                page.title()
                or urlsplit(url).path.lstrip("/")
                or "GHL website"
            )

            result = upsert(
                kind="published",
                title=title,
                content=page.content(),
                url=url
            )

            summary[result] += 1

        except Exception as error:
            summary["failed"] += 1

            print(
                "[site] Failed:",
                url,
                repr(error),
                flush=True
            )

    return summary


# ============================================================
# SANITY READ-ONLY SYNC
# ============================================================

def portable_text(value):
    """
    Convert Sanity Portable Text to plain text.
    """

    if isinstance(value, str):
        return value

    if isinstance(value, list):
        return "\n\n".join(
            filter(
                None,
                (portable_text(x) for x in value)
            )
        )

    if isinstance(value, dict):
        if value.get("_type") == "span":
            return str(value.get("text", ""))

        if value.get("_type") == "block":
            return "".join(
                portable_text(x)
                for x in value.get("children", [])
            )

        fields = (
            "body",
            "content",
            "text",
            "paragraphs",
            "blocks"
        )

        return "\n\n".join(
            filter(
                None,
                (
                    portable_text(value.get(field))
                    for field in fields
                    if value.get(field) is not None
                )
            )
        )

    return ""


def sync_sanity():
    """
    Read published Sanity documents.

    No Sanity write permission is required
    if the existing API token permits reading.
    """

    try:
        from dotenv import load_dotenv
        from sanity_store import SanityStore

        load_dotenv(
            ROOT / ".env",
            override=False
        )

        query = """
        *[
            _type in ["ghlPost", "post", "contentItem"]
            && !(_id in path("drafts.**"))
            && !(_id in path("versions.**"))
        ] {
            _id,
            _type,
            title,
            slug,
            body,
            content,
            excerpt,
            canonicalUrl,
            url,
            publishedAt
        }
        """

        documents = SanityStore().query(query)

    except Exception as error:
        print(
            "[sanity] Read unavailable:",
            repr(error),
            flush=True
        )

        return {
            "error": str(error)[:180]
        }

    counts = {
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "linked": 0,
        "skipped": 0
    }

    for doc in documents or []:
        try:
            title = str(
                doc.get("title") or ""
            )

            body = portable_text(
                doc.get("body")
                or doc.get("content")
            )

            if len(body) < 200:
                body = (
                    str(doc.get("excerpt") or "")
                    + "\n\n"
                    + body
                ).strip()

            url = (
                doc.get("canonicalUrl")
                or doc.get("url")
                or ""
            )

            result = upsert(
                kind="published",
                title=title,
                content=body,
                url=url,
                key="sanity:" + str(doc["_id"])
            )

            counts[result] += 1

        except Exception as error:
            counts["skipped"] += 1

            print(
                "[sanity] Skipped:",
                repr(error),
                flush=True
            )

    return counts


# ============================================================
# LINKEDIN IMPORT
# ============================================================

def import_linkedin_from_clipboard(url):
    """
    Import author-provided article text.

    LinkedIn does not provide unrestricted article
    discovery and extraction through its public API.

    This function does not scrape LinkedIn.
    """

    parsed = urlsplit(url)

    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() not in {
            "linkedin.com",
            "www.linkedin.com"
        }
    ):
        raise ValueError(
            "Provide the original HTTPS LinkedIn URL."
        )

    text = subprocess.check_output(
        ["pbpaste"],
        text=True
    ).strip()

    if len(text) < 400:
        raise ValueError(
            "Copy the complete article first."
        )

    title = next(
        (
            line.strip().lstrip("# ")
            for line in text.splitlines()
            if line.strip()
        ),
        ""
    )

    if len(title) > 180:
        title = "LinkedIn article"

    return upsert(
        kind="editorial",
        title=title,
        content=text,
        url=url
    )


# ============================================================
# DR. SHILPA'S ARTICLE — USER-PROVIDED TEXT
# ============================================================

SHILPA_ARTICLE_URL = (
    "https://www.linkedin.com/pulse/"
    "clinical-ai-needs-outside-checker-"
    "shilpa-hattangadi-dj9te/"
)

SHILPA_ARTICLE = """Author: Shilpa Hattangadi
Published: 2026-10-22

Hospitals buy AI tools to stretch their already limited staff, then end up counting on those same staff to catch its mistakes. The evidence says that plan doesn't work.

On October 6, the UK government accepted all 44 recommendations (https://healthcare-management.uk/government-commits-continuous-real-world-monitoring-ai-technologies) of its National Commission into the Regulation of AI in Healthcare, including “continuous real-world monitoring” of AI tools after their deployment. In contrast, the US has enacted only voluntary measures, chiefly a Joint Commission Responsible Use of AI in Healthcare (RUAIH) Certification (https://www.jointcommission.org/en-us/certification/responsible-use-of-ai-in-healthcare) launched in June. Arizona State faculty recently published a review (https://nips.cc/virtual/2025/poster/121927) finding that only 9% of FDA-registered AI healthcare tools even include a post-deployment surveillance plan, and that existing monitoring plans are underdeveloped and often “manual, sporadic, and reactive”.

Most hospitals’ AI risk management assumes two flawed beliefs that recent evidence undercuts: that a human reviewer will catch all problems, and that the vendor's premarket assurances will hold.

‘Human in the loop’ review doesn't scale

The bulwark safeguard is a clinician who checks AI output, called “human in the loop”. Hospitals adopt AI mainly to give clinicians time back, but this safeguard takes that away.

Sri Adusumalli, Penn Medicine's chief health information officer, told a Becker's panel in May that people are “terrible” at being able to stay vigilant against algorithms (https://www.pslhub.org/blogs/entry/9903-usa-as-ai-identifies-more-at-risk-patients-health-systems-face-a-capacity-challenge/), and that there are not enough of them to do it anyway. That same month, David Toro-Tobon and colleagues from the Mayo Clinic argued in the BMJ (https://futurehealth.bmj.com/news-1/clinician-loop-flawed-solution-ai-oversight) that "clinician in the loop" shifts responsibility for AI safety from developers to doctors and cannot sustainably serve as the only failsafe. A Lancet viewpoint (https://hifa.org/node/96857635) article described review under time pressure and automation bias as symbolic reassurance more than real protection because of constraints on the clinician.

Clinicians’ input is still paramount at the point of care. Surveillance on whether the tool is working has to come from outside, and the humans need measuring too. Healio reported (https://healio.com/news/gastroenterology/20261005/a-slippery-slope-is-ai-eroding-clinical-skills) this week on proposals to track how often clinicians override or agree with AI. They brought up how there is a slippery slope between augmentation and deskilling, and that as healthcare becomes more reliant on AI, clinicians trained in this era may be less prepared to function when those systems become unavailable in future, not just when it is wrong.

Monitoring belongs in the product

A model that passed validation for FDA approval can still degrade once it meets real clinical data in real-world workflows. A July paper in PLOS Digital Health (https://www.ncbi.nlm.nih.gov/pmc/articles/PMC13405297/) followed four clinical AI systems over extended periods and found post-deployment performance diverging from validation-era results. The authors call this fragility a “structural property of clinical AI [tools] embedded in evolving workflows”, and suggest that effective AI governance should include operational reassessment throughout deployment.

An expert group convened by the UK's Medicines and Healthcare Products Regulatory Agency (MHRA) sorted post-deployment drift into three types (https://futurehealth.bmj.com/news-1/identifying-understanding-significant-change-due-drift-assessing-ai-models-healthcare-narrative-review): shifts in input data, in outcomes, and in the relationship between the two. Each of these three types might require a different response, such as recalibration or retraining. Patient subgroup performance can deteriorate too, which means drift can actually turn into a health equity problem.

If monitoring is something a hospital has to adopt after purchase, most hospitals won't. Drift detection, subgroup tracking, and an alert/accountability path should ship with the software and be priced into the original contract.

The vendor shouldn't grade its own work

Built-in monitoring still needs an independent checker. A vendor's dashboard reports on the vendor's product, and the vendor has every reason to overlook flaws.

Lawyers Palmieri, Robertson, and I. Glenn Cohen wrote in JAMA (https://scholarship.law.bu.edu/faculty_scholarship/4184) that smaller community hospitals tend to buy off-the-shelf tools and trust vendor assurances. Nearly two-thirds of US hospitals fit this definition as having fewer than 100 beds. Among those hospitals using AI, 61% had confirmed its accuracy locally and only 44% had evaluated bias of the tool adopted on their own data. The authors also asked whether thousands of hospitals repeating this work makes sense, noting that no is no entity comparable to a state licensing board for AI.

The verification gap shows up in the newest data. A Clearwater benchmark (https://www.prnewswire.com/news-releases/new-clearwater-report-finds-91-of-healthcare-organizations-cannot-verify-ai-governance-for-their-organization-302900937.html) released today found that healthcare has finished the policy phase of AI governance on paper: committees chartered and contracts updated. Yet only 14% of organizations can show that their deployed AI tools are fully verified.

Independence has to be genuine. The JAMA authors flag the concern that a private, industry-heavy assurance network could favor dominant vendors and act as a defacto regulator. Monitoring funded and governed by the companies it watches would recreate the original problem, so who pays for it and who sits on its board matter as much as its technical skill.

What to ask for

Hospitals buying AI now can write three things into the contract: monitoring built into the product, an independent, third-party that reviews drift and failure modes continuously GoodHart Labs (https://www.linkedin.com/company/goodhart-labs/), and results delivered directly to the hospital without passing through the vendor. Regulators and accreditors can make shared assurance available to hospitals that cannot build it themselves.

AI is being added to clinical workflows faster than anyone is checking it, and the buyers least able to check are the ones most likely to rely on the vendor's biased assurances. Now we as physicians need to negotiate accordingly, and help each other ask for more, before it falls on our shoulders.
"""


def seed_shilpa_article():
    """
    Replace the existing abbreviated article
    without creating a duplicate.
    """

    title = "Clinical AI Needs an Outside Checker"

    with open_database() as db:
        prior = db.execute(
            """
            SELECT id, content
            FROM docs
            WHERE url = ?
            LIMIT 1
            """,
            (SHILPA_ARTICLE_URL,)
        ).fetchone()

        if prior is None:
            prior = db.execute(
                """
                SELECT id, content
                FROM docs
                WHERE title = ?
                  AND (url = '' OR url IS NULL)
                LIMIT 1
                """,
                (
                    "Clinical AI Monitoring and "
                    "Independent Verification",
                )
            ).fetchone()

        if prior:
            key, content = prior

            if len(content or "") >= len(
                SHILPA_ARTICLE
            ) * 0.85:
                return "unchanged"

            db.execute(
                """
                UPDATE docs
                SET kind = 'editorial',
                    title = ?,
                    content = ?,
                    url = ?
                WHERE id = ?
                """,
                (
                    title,
                    SHILPA_ARTICLE,
                    SHILPA_ARTICLE_URL,
                    key
                )
            )

            return "updated"

    return upsert(
        kind="editorial",
        title=title,
        content=SHILPA_ARTICLE,
        url=SHILPA_ARTICLE_URL
    )


# ============================================================
# AUTOMATIC SYNC
# ============================================================

def sync_once():
    print(
        "[ingest]",
        datetime.now(timezone.utc).isoformat(),
        flush=True
    )

    print(
        "[ingest] GHL website:",
        sync_website(),
        flush=True
    )

    print(
        "[ingest] Sanity:",
        sync_sanity(),
        flush=True
    )

    # Report documents missing source URLs.
    with open_database() as db:
        missing = db.execute("""
            SELECT kind, title
            FROM docs
            WHERE url IS NULL OR TRIM(url) = ''
            ORDER BY kind, title
        """).fetchall()

    print(f"[ingest] Documents missing URLs: {len(missing)}")

    for kind, title in missing:
        status = (
            "Unpublished draft — URL not required"
            if kind == "prior_draft"
            else "Needs a verified source URL"
        )

        print(f"  [{kind}] {title} — {status}")

def watch():
    minutes = max(
        15,
        int(
            os.getenv(
                "GHL_SYNC_INTERVAL_MINUTES",
                "2880"
            )
        )
    )

    print(
        f"[ingest] Sync every {minutes} minutes",
        flush=True
    )

    while True:
        try:
            sync_once()

        except Exception as error:
            print(
                "[ingest] Error:",
                repr(error),
                flush=True
            )

        time.sleep(minutes * 60)


# ============================================================
# STARTUP
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    mode = parser.add_mutually_exclusive_group(
        required=True
    )

    mode.add_argument(
        "--once",
        action="store_true",
        help="Run one ingestion cycle"
    )

    mode.add_argument(
        "--watch",
        action="store_true",
        help="Automatically sync every six hours"
    )

    mode.add_argument(
        "--bot",
        action="store_true",
        help="Run Slack bot and automatic ingestion"
    )

    mode.add_argument(
        "--import-linkedin",
        metavar="URL",
        help="Import an article copied to your clipboard"
    )

    args = parser.parse_args()

    if args.once:
        sync_once()

    elif args.watch:
        watch()

    elif args.bot:
        threading.Thread(
            target=watch,
            name="ghl-ingestion",
            daemon=True
        ).start()

        runpy.run_path(
            str(ROOT / "slack_ai_test.py"),
            run_name="__main__"
        )

    elif args.import_linkedin:
        result = import_linkedin_from_clipboard(
            args.import_linkedin
        )

        print(
            "[linkedin]",
            result,
            flush=True
        )


if __name__ == "__main__":
    main()
