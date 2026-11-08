"""URL 수동 등록(S3-06, UJ-6). 페이지 제목을 읽어 manual 소스의 수집 항목으로 저장한다. 분석은 워커가 한다."""
from datetime import datetime
from typing import Callable, Iterable
from urllib.parse import urlsplit

import psycopg

from radar.adapters.page import Page, PageError, read_page
from radar.domain.cleaning import EXCERPT_LIMIT, TITLE_LIMIT, clean_text
from radar.domain.models import RawItem
from radar.domain.normalize import normalize_url
from radar.store.items import ItemStore


def add_urls(conn: psycopg.Connection, urls: Iterable[str], clock: Callable[[], datetime],
             read: Callable[[str], Page] = read_page) -> list[str]:
    """URL마다 결과 한 줄을 돌려준다. 빈 줄과 #으로 시작하는 줄은 건너뛴다."""
    source_id = conn.execute("select id from sources where name = 'manual'").fetchone()["id"]
    store = ItemStore(conn, clock)
    return [_add_one(conn, store, source_id, url.strip(), read) for url in urls if url.strip() and not url.startswith("#")]


def _add_one(conn: psycopg.Connection, store: ItemStore, source_id: int, url: str, read: Callable[[str], Page]) -> str:
    if urlsplit(url).scheme not in ("http", "https"):
        return f"건너뜀(http·https 주소가 아님): {url}"
    try:
        existing = conn.execute("select id from source_items where normalized_url = %s", (normalize_url(url),)).fetchone()
    except ValueError:
        return f"건너뜀(주소를 해석하지 못함): {url}"
    if existing:
        return f"이미 있음(항목 #{existing['id']}): {url}"
    try:
        page = read(url)
        title, excerpt, note = page.title or url, page.description or page.text, ""
    except PageError as err:  # 읽지 못해도 URL로 등록한다. 모델은 URL과 제목만 보고 이름을 정한다
        title, excerpt, note = url, "", f" (페이지를 읽지 못함: {err.kind})"
    item = RawItem(None, url, clean_text(title, TITLE_LIMIT), clean_text(excerpt, EXCERPT_LIMIT), None, {"kind": "manual"})
    outcome = store.save(source_id, [item])
    return f"{'등록함' if outcome.inserted else '등록하지 못함'}: {url}{note}"
