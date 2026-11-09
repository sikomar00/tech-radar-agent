"""공식 페이지 한 장 읽기(ADR-0007). 수집 항목의 URL은 외부 글이 정한 값이라 내부망을 찌르지 못하게 막는다.

urllib는 리다이렉트를 알아서 따라가고 연결할 IP를 검사할 틈이 없어서 http.client로 직접 부른다.
이름을 풀 때 한 번, 실제로 연결된 상대 주소로 한 번 더 검사한다(이름 풀이 뒤에 주소를 바꾸는 DNS 리바인딩 방어).
"""
import codecs
import http.client
import ipaddress
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from radar.adapters.http import USER_AGENT
from radar.domain.cleaning import TITLE_LIMIT, collapse, strip_control

MAX_PAGE_BYTES = 1_000_000
TEXT_LIMIT = 8000
TIMEOUT = 20.0  # 연결·recv 한 번의 제한
TOTAL_SECONDS = 45.0  # robots·리다이렉트·본문을 합친 페이지 한 장의 제한
MAX_REDIRECTS = 3
PAGE_TYPES = ("text/html", "text/plain", "text/markdown")
DEFAULT_PORTS = {"http": 80, "https": 443}
_REDIRECTS = {301, 302, 303, 307, 308}
_PATH_SAFE = "/%?=&;:@!$'()*+,-._~"  # 이미 인코딩된 %XX와 경로·쿼리 구분자는 그대로 둔다
_REPO = re.compile(r"[A-Za-z0-9-]+/(?!\.\.?$)[A-Za-z0-9_.-]+")  # GitHub 사용자명에는 점이 없고, 저장소명 . ..은 경로 이동이다


class PageError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


def public_ip(ip: str) -> bool:
    """사설·루프백·링크 로컬·예약 주소가 아니면 True. IPv6 영역 표시(%eth0)는 떼고 본다.

    IPv6 안에 IPv4를 담는 형식(매핑, 6to4, Teredo, NAT64, IPv4 호환)은 담긴 IPv4도 공개 주소여야 한다.
    파이썬 3.11.10 전의 is_global은 이 형식 일부를 공개로 본다(CVE-2024-4032).
    """
    addr = ipaddress.ip_address(ip.split("%")[0])
    if not addr.is_global:
        return False
    if addr.version == 4:
        return True
    embedded = [addr.ipv4_mapped, addr.sixtofour, addr.teredo[1] if addr.teredo else None]
    if addr in _NAT64 or addr in _IPV4_COMPATIBLE:
        embedded.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    return all(inner is None or inner.is_global for inner in embedded)


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
    try:
        parts = urlsplit(url)
        port = parts.port or DEFAULT_PORTS.get(parts.scheme, 0)
    except ValueError as err:  # 잘못된 IPv6 괄호, 숫자가 아닌 포트 등
        raise PageError("blocked", "주소를 해석하지 못했다") from err
    if parts.scheme not in DEFAULT_PORTS or not parts.hostname:
        raise PageError("blocked", "http·https 주소만 읽는다")
    if port != DEFAULT_PORTS[parts.scheme] and not guard.any_port:
        raise PageError("blocked", "기본 포트만 허용한다")
    try:
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as err:  # UnicodeError: IDNA로 바꿀 수 없는 호스트 이름
        raise PageError("network", "이름을 풀지 못했다") from err
    if not all(guard.allow_ip(info[4][0]) for info in infos):
        raise PageError("blocked", "공개 주소가 아니다")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return parts.scheme, parts.hostname, port, quote(path, safe=_PATH_SAFE)  # 한글 같은 글자는 퍼센트 인코딩해야 보낼 수 있다


def _connection(scheme: str, host: str, port: int, guard: Guard) -> http.client.HTTPConnection:
    """TCP 연결 직후, TLS 인사나 요청을 보내기 전에 실제 상대 주소를 검사하는 연결."""

    def checked_connect(address: tuple[str, int], timeout: float, source_address: tuple | None = None) -> socket.socket:
        sock = socket.create_connection(address, timeout, source_address)
        if not guard.allow_ip(sock.getpeername()[0]):
            sock.close()
            raise PageError("blocked", "연결된 주소가 공개 주소가 아니다")
        return sock

    base = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    conn = base(host, port, timeout=TIMEOUT)
    conn._create_connection = checked_connect  # http.client가 소켓을 만들 때 부르는 자리(HTTP·HTTPS 공통)
    return conn


def _cut(conn: http.client.HTTPConnection) -> None:
    """마감이 지나면 다른 스레드에서 소켓을 끊어, 한 바이트씩 흘려보내는 서버에 막힌 recv를 깨운다."""
    try:
        if conn.sock is not None:
            conn.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _read_body(res: http.client.HTTPResponse, deadline: float) -> bytes:
    body = bytearray()
    while chunk := res.read1(65536):
        body += chunk
        if len(body) > MAX_PAGE_BYTES:
            raise PageError("too_large", f"{MAX_PAGE_BYTES}바이트를 넘는다")
        if time.monotonic() > deadline:
            raise PageError("timeout", "전체 시간 제한을 넘었다")
    if time.monotonic() > deadline:  # 감시 타이머가 소켓을 끊어 읽기가 일찍 끝난 경우. 잘린 본문을 성공으로 넘기지 않는다
        raise PageError("timeout", "전체 시간 제한을 넘었다")
    return bytes(body)


def _charset(declared: str | None) -> str:
    """서버가 밝힌 인코딩 이름이 파이썬이 모르는 값이면 utf-8로 읽는다(decode가 LookupError로 죽지 않게)."""
    try:
        return codecs.lookup(declared or "utf-8").name
    except LookupError:
        return "utf-8"


def _fetch(url: str, guard: Guard, headers: dict[str, str], deadline: float) -> _Response:
    """리다이렉트는 MAX_REDIRECTS번까지 따르고 매번 주소를 다시 검사한다. 모든 단계가 deadline(monotonic) 안에 끝나야 한다.

    소켓의 timeout은 recv 한 번마다 다시 시작되므로 그것만으로는 조금씩 흘려보내는 서버를 끊지 못한다.
    """
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = _target(url, guard)
        conn = _connection(scheme, host, port, guard)
        watchdog = threading.Timer(max(deadline - time.monotonic(), 0), _cut, (conn,))
        watchdog.daemon = True
        try:
            conn.connect()
            watchdog.start()
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, **headers})
            res = conn.getresponse()
            location = res.getheader("Location")
            if res.status in _REDIRECTS and location:
                url = urljoin(url, location)
                continue
            body = _read_body(res, deadline)
            content_type = (res.getheader("Content-Type") or "").split(";")[0].strip().lower()
            return _Response(url, res.status, content_type, _charset(res.headers.get_content_charset()), body)
        except TimeoutError as err:
            raise PageError("timeout", "시간 초과") from err
        except (OSError, http.client.HTTPException, ValueError) as err:  # ValueError: 보낼 수 없는 글자가 든 요청 등
            raise PageError("timeout" if time.monotonic() > deadline else "network", type(err).__name__) from err
        finally:
            watchdog.cancel()
            conn.close()
    raise PageError("redirects", f"리다이렉트가 {MAX_REDIRECTS}번을 넘는다")


def _robots_allow(url: str, guard: Guard, deadline: float) -> bool:
    """robots.txt가 막으면 False. 없거나(4xx) 못 읽으면 허용하고, 401·403이면 막는다(RobotFileParser와 같은 해석)."""
    scheme, host, port, _ = _target(url, guard)  # 잘못된 주소는 robots를 읽기 전에 PageError로 끝낸다
    netloc = f"[{host}]" if ":" in host else host
    netloc += "" if port == DEFAULT_PORTS[scheme] else f":{port}"
    try:
        res = _fetch(f"{scheme}://{netloc}/robots.txt", guard, {}, deadline)
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
    deadline = time.monotonic() + TOTAL_SECONDS
    if not _robots_allow(url, guard, deadline):
        raise PageError("robots", "robots.txt가 막는다")
    res = _fetch(url, guard, {"Accept": "text/html, text/plain, text/markdown"}, deadline)
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
    res = _fetch(f"https://api.github.com/repos/{repo}/readme", guard, {"Accept": "application/vnd.github.raw"},
                 time.monotonic() + TOTAL_SECONDS)
    if res.status != 200:
        raise PageError("http_status", f"HTTP {res.status}")
    text = " ".join(_parse(res.body.decode(res.charset, errors="replace")).parts)  # README 마크다운에 섞인 HTML 태그를 걷는다
    return Page(f"https://github.com/{repo}", repo, "", _clean(text, TEXT_LIMIT))
