"""분류 보류 큐 운영 명령 `radar holds list|set|link|new`. 사람이 판정한다(S2-03). 화면은 3단계에서 만든다."""
from datetime import datetime
from typing import Callable

import psycopg

from radar.analyze import attach
from radar.domain.analysis import Classification, parse_classification
from radar.store import holds as queries
from radar.store.analysis import AnalysisStore
from radar.store.items import get_item
from radar.store.technologies import TechnologyStore

HOLD_TYPES = ("PRODUCT", "FRAMEWORK_LIBRARY")


def list_holds(conn: psycopg.Connection) -> list[str]:
    lines = ["== 유형을 정해야 하는 기술 =="]
    techs = queries.held_technologies(conn)
    lines += [f"기술 #{t['id']} {t['canonical_name']} | 별칭 {', '.join(t['aliases']) or '없음'} | 근거 항목 {t['sources']}건" for t in techs]
    lines += ["(없음)"] if not techs else []
    lines.append("== 기존 기술과 같은지 모르는 항목 ==")
    items = queries.held_items(conn)
    for item in items:
        found = item["classified"]
        lines.append(f"항목 #{item['id']} [{found['class']}] {found['canonical_name']} (유형 {found['type']}) | {item['title']} | {item['url']}")
    lines += ["(없음)"] if not items else []
    return lines


def set_type(conn: psycopg.Connection, technology_id: int, type_: str) -> tuple[bool, str]:
    if type_ not in HOLD_TYPES:
        return False, f"유형은 {' 또는 '.join(HOLD_TYPES)}만 쓸 수 있습니다"
    if queries.set_type(conn, technology_id, type_):
        return True, f"기술 #{technology_id}의 유형을 {type_}로 정했습니다"
    return False, f"기술 #{technology_id}가 없거나 이미 유형이 정해져 있습니다"


def _classification(conn: psycopg.Connection, clock: Callable[[], datetime], item_id: int) -> Classification | None:
    raw = AnalysisStore(conn, clock).current_result("classify", item_id)
    try:
        found = parse_classification(raw) if raw else None
    except ValueError:
        return None
    return found if found and found.is_candidate else None


def link_item(conn: psycopg.Connection, clock: Callable[[], datetime], item_id: int, technology_id: int) -> tuple[bool, str]:
    """보류 항목을 기존 기술에 잇는다."""
    techs, found = TechnologyStore(conn), _classification(conn, clock, item_id)
    if found is None:
        return False, f"항목 #{item_id}는 후보로 분류된 결과가 없습니다"
    if not techs.describe([technology_id]):
        return False, f"기술 #{technology_id}가 없습니다"
    with conn.transaction():
        attach(techs, get_item(conn, item_id), found, technology_id)
    return True, f"항목 #{item_id}를 기술 #{technology_id}에 연결했습니다"


def new_from_item(conn: psycopg.Connection, clock: Callable[[], datetime], item_id: int) -> tuple[bool, str]:
    """보류 항목으로 새 기술을 만든다. 같은 이름의 기술이 이미 있으면 그 기술에 잇는다."""
    techs, found = TechnologyStore(conn), _classification(conn, clock, item_id)
    if found is None:
        return False, f"항목 #{item_id}는 후보로 분류된 결과가 없습니다"
    with conn.transaction():
        technology_id, created = techs.create(found.name, found.type)
        attach(techs, get_item(conn, item_id), found, technology_id)
    return True, f"항목 #{item_id}로 기술 #{technology_id}를 {'만들었습니다' if created else '찾아 연결했습니다(같은 이름이 이미 있음)'}"
