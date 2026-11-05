import html
import re

TITLE_LIMIT = 300
EXCERPT_LIMIT = 280

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
# PostgreSQL text는 NUL을 받지 못하고, 나머지 제어 문자는 화면에서 깨지므로 입력 경계에서 지운다
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def collapse(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def strip_control(text: str) -> str:
    return _CONTROL.sub("", text)


def clean_text(text: str | None, limit: int, *, is_html: bool = False) -> str:
    """제어 문자를 지우고 공백을 정리해 limit자에서 자른다.

    is_html일 때만 엔티티를 풀고 태그를 지운다(엔티티를 먼저 풀어야 `&lt;script&gt;`도 함께 지워진다).
    HN 제목이나 GitHub 설명 같은 일반 텍스트에는 쓰지 않는다. `Vec<T>`의 `<T>`가 태그로 오인돼 지워지기 때문이다.
    """
    value = strip_control(text or "")
    if is_html:
        value = _TAG.sub(" ", html.unescape(value))
    return collapse(value)[:limit]
