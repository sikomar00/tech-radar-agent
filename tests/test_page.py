"""공식 페이지 읽기(ADR-0007)의 안전 규칙. 127.0.0.1에 실제 HTTP 서버를 띄워 시험한다(외부 네트워크 없음)."""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from radar.adapters import page
from radar.adapters.page import Guard, PageError, public_ip, read_page, read_readme

LOCAL = Guard(allow_ip=lambda ip: ip == "127.0.0.1", any_port=True)
HTML = ("<html><head><title> Tool  X </title><meta name='description' content='A tool.'>"
        "<script>steal()</script><style>p{}</style></head><body><p>Hello &amp; welcome</p></body></html>")
ROUTES = {
    "/page": (200, "text/html; charset=utf-8", HTML.encode()),
    "/plain": (200, "text/plain", b"just  text"),
    "/odd-charset": (200, "text/plain; charset=no-such-codec", b"still readable"),
    "/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /secret\n"),
    "/secret": (200, "text/html", b"<p>hidden</p>"),
    "/big": (200, "text/html", b"a" * (page.MAX_PAGE_BYTES + 10)),
    "/bin": (200, "application/octet-stream", b"\x00\x01"),
}
REDIRECTS = {"/loop": "/loop", "/to-private": "http://10.0.0.1/", "/hop": "/page", "/broken": "http://[::1"}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server 규약)
        if self.path == "/drip":
            return self._drip()
        if self.path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[self.path])
            self.end_headers()
            return
        status, content_type, body = ROUTES.get(self.path, (404, "text/plain", b"no"))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drip(self) -> None:
        """본문을 0.2초마다 한 바이트씩 흘려보내는 서버(소켓 timeout만으로는 끊기지 않는다)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        try:
            for _ in range(100):
                self.wfile.write(b"a")
                self.wfile.flush()
                time.sleep(0.2)
        except OSError:
            pass

    def log_message(self, *_args) -> None:
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_a_page_gives_title_description_and_text_without_scripts(server) -> None:
    found = read_page(f"{server}/page", LOCAL)
    assert (found.title, found.description, found.text) == ("Tool X", "A tool.", "Hello & welcome")


def test_plain_text_is_returned_as_text_and_redirects_are_followed(server) -> None:
    assert read_page(f"{server}/plain", LOCAL).text == "just text"
    assert read_page(f"{server}/odd-charset", LOCAL).text == "still readable"
    assert read_page(f"{server}/hop", LOCAL).url.endswith("/page")


@pytest.mark.parametrize(("path", "kind"), [("/secret", "robots"), ("/big", "too_large"), ("/bin", "content_type"),
                                            ("/loop", "redirects"), ("/missing", "http_status")])
def test_pages_that_must_not_be_read_fail_with_a_reason(server, path: str, kind: str) -> None:
    with pytest.raises(PageError) as caught:
        read_page(f"{server}{path}", LOCAL)
    assert caught.value.kind == kind


def test_a_redirect_into_the_private_network_is_blocked(server) -> None:
    with pytest.raises(PageError) as caught:
        read_page(f"{server}/to-private", LOCAL)
    assert caught.value.kind == "blocked"


def test_the_default_guard_refuses_loopback_even_on_an_allowed_port(server) -> None:
    with pytest.raises(PageError) as caught:
        read_page(f"{server}/page", Guard(any_port=True))
    assert caught.value.kind == "blocked"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "http://example.com:8080/x", "http:///nohost",
                                 "http://example.com:99999/"])
def test_other_schemes_ports_and_broken_urls_are_blocked_before_any_request(url: str) -> None:
    with pytest.raises(PageError) as caught:
        read_page(url)
    assert caught.value.kind == "blocked"


def test_a_name_that_changes_to_a_private_address_after_the_check_is_caught_at_connect(server, monkeypatch) -> None:
    real = socket.getaddrinfo
    answers = iter(["93.184.216.34", "127.0.0.1"])  # 검사할 때는 공개 주소, 연결할 때는 루프백(DNS 리바인딩)

    def rebinding(host, port, *args, **kwargs):
        if host != "rebind.test":
            return real(host, port, *args, **kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    monkeypatch.setattr(socket, "getaddrinfo", rebinding)
    port = int(server.rsplit(":", 1)[1])
    with pytest.raises(PageError) as caught:
        page._fetch(f"http://rebind.test:{port}/page", Guard(any_port=True), {}, time.monotonic() + 10)
    assert caught.value.kind == "blocked"


@pytest.mark.parametrize(("ip", "expected"), [("8.8.8.8", True), ("127.0.0.1", False), ("10.1.2.3", False),
                                              ("169.254.169.254", False), ("::1", False), ("fe80::1%eth0", False),
                                              ("192.168.0.1", False), ("100.64.0.1", False),
                                              ("64:ff9b::a00:1", False), ("2002:a00:1::", False), ("::7f00:1", False),
                                              ("::ffff:10.0.0.1", False), ("64:ff9b::808:808", True), ("2001:4860:4860::8888", True)])
def test_only_global_addresses_are_public(ip: str, expected: bool) -> None:
    assert public_ip(ip) is expected


@pytest.mark.parametrize("repo", ["../etc", "owner/..", "owner/.", "owner/repo/extra", "owner", "own er/repo"])
def test_a_readme_request_takes_only_an_owner_and_name(repo: str) -> None:
    with pytest.raises(PageError) as caught:
        read_readme(repo)
    assert caught.value.kind == "blocked"


def test_a_server_that_drips_bytes_is_cut_at_the_total_deadline(server, monkeypatch) -> None:
    monkeypatch.setattr(page, "TOTAL_SECONDS", 1.5)
    started = time.monotonic()
    with pytest.raises(PageError) as caught:
        read_page(f"{server}/drip", LOCAL)
    assert caught.value.kind == "timeout" and time.monotonic() - started < 6


def test_non_ascii_paths_are_sent_encoded_and_broken_redirects_are_page_errors(server) -> None:
    with pytest.raises(PageError) as caught:
        read_page(f"{server}/한글 경로?q=값", LOCAL)
    assert caught.value.kind == "http_status"  # 요청은 나갔고 서버가 404로 답했다(UnicodeEncodeError로 죽지 않는다)
    with pytest.raises(PageError):  # 깨진 Location도 PageError로 끝난다(ValueError로 잡을 죽이지 않는다)
        read_page(f"{server}/broken", LOCAL)
    with pytest.raises(PageError):
        read_page("http://[::1")
