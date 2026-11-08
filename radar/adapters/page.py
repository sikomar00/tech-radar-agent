"""공식 페이지 한 장 읽기(ADR-0007). 수집 항목의 URL은 외부 글이 정한 값이라 내부망을 찌르지 못하게 막는다.

urllib는 리다이렉트를 알아서 따라가고 연결할 IP를 검사할 틈이 없어서 http.client로 직접 부른다.
이름을 풀 때 한 번, 실제로 연결된 상대 주소로 한 번 더 검사한다(이름 풀이 뒤에 주소를 바꾸는 DNS 리바인딩 방어).
"""
import codecs
import http.client
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from radar.adapters.http import USER_AGENT
from radar.domain.cleaning import TITLE_LIMIT, collapse, strip_control

MAX_PAGE_BYTES = 1_000_000
TEXT_LIMIT = 8000
TIMEOUT = 20.0
MAX_REDIRECTS = 3
PAGE_TYPES = ("text/html", "text/plain", "text/markdown")
DEFAULT_PORTS = {"http": 80, "https": 443}
_REDIRECTS = {301, 302, 303, 307, 308}
_REPO = re.compile(r"[A-Za-z0-9-]+/(?!\.\.?$)[A-Za-z0-9_.-]+")  # GitHub 사용자명에는 점이 없고, 저장소명 . ..은 경로 이동이다


class PageError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


def public_ip(ip: str) -> bool:
    """사설·루프백·링크 로컬·예약 주소가 아니면 True. IPv6 영역 표시(%eth0)는 떼고 본다."""
    return ipaddress.ip_address(ip.split("%")[0]).is_global


@dataclass(frozen=True)
class Guard:
    allow_ip: Callable[[str], bool] = public_ip
    any_port: bool = False  # 시험 서버(임의 포트)용. 운영에서는 기본 포트만


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    description: str
    text: str


@dataclass
class _Response:
    url: str
    status: int
    content_type: str
    charset: str
    body: bytes = field(repr=False)


def _target(url: str, guard: Guard) -> tuple[str, str, int, str]:
    parts = urlsplit(url)
    if parts.scheme not in DEFAULT_PORTS or not parts.hostname:
        raise PageError("blocked", "http·https 주소만 읽는다")
    try:
        port = parts.port or DEFAULT_PORTS[parts.scheme]
    except ValueError as err:
        raise PageError("blocked", "포트가 잘못됐다") from err
    if port != DEFAULT_PORTS[parts.scheme] and not guard.any_port:
        raise PageError("blocked", "기본 포트만 허용한다")
    try:
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except OSError as err:
        raise PageError("network", "이름을 풀지 못했다") from err
    if not all(guard.allow_ip(info[4][0]) for info in infos):
        raise PageError("blocked", "공개 주소가 아니다")
    return parts.scheme, parts.hostname, port, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _connection(scheme: str, host: str, port: int, guard: Guard) -> http.client.HTTPConnection:
    base = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection

    class Checked(base):  # type: ignore[valid-type, misc]
        def connect(self) -> None:
            super().connect()
            if not guard.allow_ip(self.sock.getpeername()[0]):
                self.close()
                raise PageError("blocked", "연결된 주소가 공개 주소가 아니다")

    return Checked(host, port, timeout=TIMEOUT)


def _charset(declared: str | None) -> str:
    """서버가 밝힌 인코딩 이름이 파이썬이 모르는 값이면 utf-8로 읽는다(decode가 LookupError로 죽지 않게)."""
    try:
        return codecs.lookup(declared or "utf-8").name
    except LookupError:
        return "utf-8"


def _fetch(url: str, guard: Guard, headers: dict[str, str]) -> _Response:
    """리다이렉트는 MAX_REDIRECTS번까지 따르고, 매번 주소를 다시 검사한다."""
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = _target(url, guard)
        conn = _connection(scheme, host, port, guard)
        try:
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, **headers})
            res = conn.getresponse()
            location = res.getheader("Location")
            if res.status in _REDIRECTS and location:
                url = urljoin(url, location)
                continue
            body = res.read(MAX_PAGE_BYTES + 1)
            if len(body) > MAX_PAGE_BYTES:
                raise PageError("too_large", f"{MAX_PAGE_BYTES}바이트를 넘는다")
            content_type = (res.getheader("Content-Type") or "").split(";")[0].strip().lower()
            return _Response(url, res.status, content_type, _charset(res.headers.get_content_charset()), body)
        except TimeoutError as err:
            raise PageError("timeout", "시간 초과") from err
        except (OSError, http.client.HTTPException) as err:
            raise PageError("network", type(err).__name__) from err
        finally:
            conn.close()
    raise PageError("redirects", f"리다이렉트가 {MAX_REDIRECTS}번을 넘는다")


def _robots_allow(url: str, guard: Guard) -> bool:
    """robots.txt가 막으면 False. 없거나(4xx) 못 읽으면 허용하고, 401·403이면 막는다(RobotFileParser와 같은 해석)."""
    parts = urlsplit(url)
    try:
        res = _fetch(f"{parts.scheme}://{parts.netloc}/robots.txt", guard, {})
    except PageError:
        return True
    if res.status in (401, 403):
        return False
    if res.status != 200:
        return True
    robots = RobotFileParser()
    robots.parse(res.body.decode(res.charset, errors="replace").splitlines())
    return robots.can_fetch(USER_AGENT, url)


class _Text(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.description = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and not self.description:
            if (values.get("name") or values.get("property") or "").lower() in ("description", "og:description"):
                self.description = values.get("content") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            (self.title if self._in_title else self.parts).append(data)


def _clean(text: str, limit: int) -> str:
    return collapse(strip_control(text))[:limit]


def _parse(raw: str) -> _Text:
    parser = _Text()
    parser.feed(raw)
    parser.close()
    return parser


def read_page(url: str, guard: Guard = Guard()) -> Page:
    """페이지 한 장의 제목·설명·본문 앞 TEXT_LIMIT자. 본문은 돌려줄 뿐 어디에도 저장하지 않는다."""
    if not _robots_allow(url, guard):
        raise PageError("robots", "robots.txt가 막는다")
    res = _fetch(url, guard, {"Accept": "text/html, text/plain, text/markdown"})
    if res.status != 200:
        raise PageError("http_status", f"HTTP {res.status}")
    if res.content_type not in PAGE_TYPES:
        raise PageError("content_type", res.content_type or "없음")
    raw = res.body.decode(res.charset, errors="replace")
    if res.content_type != "text/html":
        return Page(res.url, "", "", _clean(raw, TEXT_LIMIT))
    parser = _parse(raw)
    return Page(res.url, _clean(" ".join(parser.title), TITLE_LIMIT), _clean(parser.description, TEXT_LIMIT),
                _clean(" ".join(parser.parts), TEXT_LIMIT))


def read_readme(repo: str, guard: Guard = Guard()) -> Page:
    """GitHub 공식 API로 README 원문을 읽는다(ADR-0003의 공식 API). repo는 owner/name 형식만 받는다."""
    if not _REPO.fullmatch(repo):
        raise PageError("blocked", "저장소 이름 형식이 아니다")
    res = _fetch(f"https://api.github.com/repos/{repo}/readme", guard, {"Accept": "application/vnd.github.raw"})
    if res.status != 200:
        raise PageError("http_status", f"HTTP {res.status}")
    text = " ".join(_parse(res.body.decode(res.charset, errors="replace")).parts)  # README 마크다운에 섞인 HTML 태그를 걷는다
    return Page(f"https://github.com/{repo}", repo, "", _clean(text, TEXT_LIMIT))
