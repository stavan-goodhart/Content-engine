"""Lean GHL-specific news feed. No dependency on the complete Hawk database."""
from __future__ import annotations

import hashlib
import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

QUERIES = [
    '"clinical AI" validation hospital',
    '"clinical AI" patient safety failure',
    '"medical AI" independent evaluation',
    '"healthcare AI" governance oversight',
    '"AI scribe" clinical errors omissions',
    '"medical AI" FDA postmarket monitoring',
    '"clinical AI" benchmark real world deployment',
    '"healthcare AI" hallucination adverse event',
]

STRONG = (
    'clinical ai', 'medical ai', 'healthcare ai', 'ai scribe', 'ambient ai',
    'clinical decision support', 'model validation', 'patient safety',
    'ai governance', 'medical llm', 'clinical documentation',
    'ai diagnostic', 'ai evaluation', 'ai benchmark', 'algorithmic bias',
)
BONUS = ('hospital', 'health system', 'fda', 'regulation', 'model drift',
         'hallucinat', 'validation', 'verification', 'deployment', 'safety',
         'liability', 'evaluation', 'clinical', 'risk', 'audit')


def relevance(text: str) -> int:
    t = text.lower()
    return 4 * sum(term in t for term in STRONG) + sum(term in t for term in BONUS)


def item_from_xml(el: ET.Element, query: str) -> dict | None:
    raw = (el.findtext('title') or '').strip()
    if not raw:
        return None
    title, _, publisher = raw.rpartition(' - ')
    if not title or len(publisher) > 60:
        title = raw
        publisher = (el.findtext('source') or '').strip()
    pub = (el.findtext('pubDate') or '').strip()
    try:
        date = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        date = ''
    snippet = html.unescape(re.sub(r'<[^>]*>', ' ', el.findtext('description') or ''))
    snippet = ' '.join(snippet.split())[:400]
    url = (el.findtext('link') or '').strip()
    result = {
        'title': html.unescape(title), 'publisher': publisher, 'url': url,
        'published': date, 'snippet': snippet, 'query': query,
    }
    result['score'] = relevance(result['title'] + ' ' + result['snippet'])
    return result


def fetch_query(query: str, days: int = 3, max_items: int = 10) -> list[dict]:
    params = urllib.parse.urlencode({'q': f'{query} when:{days}d', 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})
    req = urllib.request.Request('https://news.google.com/rss/search?' + params,
                                headers={'User-Agent': 'Mozilla/5.0 (GHL Content Research/1.0)'})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            feed = ET.fromstring(resp.read())
        return [x for item in feed.iter('item') if (x := item_from_xml(item, query))][:max_items]
    except Exception as exc:
        print(f'[NEWS WARNING] {query}: {exc}')
        return []


def fetch_shortlist(days: int = 3, limit: int = 6) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    unique: dict[str, dict] = {}
    for q in QUERIES:
        for item in fetch_query(q, days):
            if item['published']:
                try:
                    if datetime.fromisoformat(item['published']) < cutoff:
                        continue
                except ValueError:
                    continue
            key = re.sub(r'[^a-z0-9]', '', item['title'].lower())[:160]
            if key not in unique or item['score'] > unique[key]['score']:
                unique[key] = item
        time.sleep(0.35)
    ranked = sorted(unique.values(), key=lambda i: (i['score'], i['published']), reverse=True)
    return [i for i in ranked if i['score'] >= 4][:limit]


def stable_id(title: str, url: str = '') -> str:
    return 'drafts.content-' + hashlib.sha256((url or title).encode()).hexdigest()[:20]
