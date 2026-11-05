from datetime import timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

# 스파이크에서 동작을 확인한 소스. 비소프트웨어 피드와 HN 미러는 넣지 않는다(ADR-0001, 스파이크 관찰 1·8)
DEFAULT_SOURCES: list[dict[str, Any]] = [
    {"name": "github", "kind": "github", "interval": timedelta(hours=6), "config": {
        "window_days": 7, "min_stars": 20, "per_page": 30, "releases_per_repo": 3,
        "seed_repos": ["anthropics/claude-code", "langchain-ai/langchain", "vercel/next.js", "ollama/ollama",
                       "modelcontextprotocol/python-sdk"]}},
    {"name": "hackernews", "kind": "hackernews", "interval": timedelta(hours=1), "config": {
        "lists": ["topstories", "showstories"], "limit": 15}},
    *[
        {"name": f"rss-{name}", "kind": "rss", "interval": timedelta(hours=1), "config": {"feed_url": url, "limit": 30}}
        for name, url in {
            "github-blog": "https://github.blog/feed/",
            "cloudflare": "https://blog.cloudflare.com/rss/",
            "huggingface": "https://huggingface.co/blog/feed.xml",
            "simon-willison": "https://simonwillison.net/atom/everything/",
        }.items()
    ],
]


def seed_sources(conn: psycopg.Connection) -> int:
    """기본 소스를 넣는다. 이미 있는 이름은 건드리지 않는다. 새로 들어간 개수를 돌려준다."""
    added = 0
    for source in DEFAULT_SOURCES:
        cur = conn.execute(
            "insert into sources (name, kind, config, collect_interval) values (%s, %s, %s, %s) "
            "on conflict (name) do nothing",
            (source["name"], source["kind"], Jsonb(source["config"]), source["interval"]),
        )
        added += cur.rowcount
    return added
