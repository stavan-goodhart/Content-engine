"""Sanity Content Lake as durable editorial state and published article storage."""
from __future__ import annotations

import json
import os
import requests

API_VERSION = '2025-02-19'


class SanityStore:
    def __init__(self):
        self.project = os.environ['SANITY_PROJECT_ID']
        self.dataset = os.getenv('SANITY_DATASET', 'production')
        self.token = os.environ['SANITY_API_TOKEN']
        self.base = f'https://{self.project}.api.sanity.io/v{API_VERSION}/data'
        self.s = requests.Session()
        self.s.headers.update({'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})

    def query(self, groq: str, params: dict | None = None):
        fields = {'query': groq, 'perspective': 'raw'}
        fields.update({f'${k}': json.dumps(v) for k, v in (params or {}).items()})
        r = self.s.get(f'{self.base}/query/{self.dataset}', params=fields, timeout=25)
        r.raise_for_status()
        return r.json()['result']

    def get(self, identifier: str):
        return self.query('*[_id == $id][0]', {'id': identifier})

    def find_thread(self, ts: str):
        return self.query('*[_type == "contentItem" && slackThreadTs == $ts][0]', {'ts': ts})

    def mutate(self, mutations: list[dict]):
        r = self.s.post(f'{self.base}/mutate/{self.dataset}', json={'mutations': mutations}, timeout=25)
        r.raise_for_status()
        return r.json()

    def insert_if_absent(self, item: dict):
        return self.mutate([{'createIfNotExists': item}])

    def patch(self, identifier: str, **fields):
        return self.mutate([{'patch': {'id': identifier, 'set': fields}}])

    def publish_article(self, item: dict, *, dry_run: bool):
        slug = item['slug']
        article_id = 'ghl-post-' + slug
        source_lines = [f"{s['title']} ({s['url']})" for s in item.get('sources', [])]
        # Body is Portable Text for a small, custom `ghlPost` schema.
        blocks = []
        for idx, line in enumerate((item.get('body') or '').splitlines()):
            line = line.strip()
            if not line:
                continue
            style = 'normal'
            if line.startswith('## '):
                style, line = 'h2', line[3:]
            elif line.startswith('# '):
                style, line = 'h1', line[2:]
            if not line:
                continue
            key = f'line{idx:05d}'
            blocks.append({'_key': key, '_type': 'block', 'style': style, 'markDefs': [],
                           'children': [{'_key': f'{key}span', '_type': 'span', 'text': line, 'marks': []}]})
        published = {
            '_id': article_id, '_type': 'ghlPost', 'title': item['articleTitle'],
            'slug': {'_type': 'slug', 'current': slug}, 'excerpt': item.get('excerpt', ''),
            'body': blocks, 'sources': [{'title': s['title'], 'url': s['url']} for s in item.get('sources', [])],
            'sourceNotes': '\n'.join(source_lines), 'publishedAt': item.get('approvedAt', ''),
            'category': 'Research note',
        }
        if not dry_run:
            self.mutate([{'createIfNotExists': published}])
        return article_id
