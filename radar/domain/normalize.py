import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit

from radar.domain.cleaning import collapse

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_DEFAULT_PORTS = {80, 443}


def normalize_url(url: str) -> str:
    """`host/path?정렬된 쿼리` 형태로 바꾼다. 규칙은 docs/SPEC.md의 정제 절을 따른다."""
    raw = url.strip()
    parts = urlsplit(raw if "://" in raw else f"//{raw}")
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host:
        raise ValueError(f"URL에 호스트가 없습니다: {url!r}")
    port = parts.port
    if port and port not in _DEFAULT_PORTS:
        host = f"{host}:{port}"
    path = parts.path.rstrip("/")
    if host == "github.com":
        path = path.lower()
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.startswith("utm_") or k in _TRACKING_PARAMS)
    )
    return f"{host}{path}" + (f"?{urlencode(query)}" if query else "")


def content_hash(title: str, excerpt: str, normalized_url: str) -> str:
    """제목+발췌의 해시. 발췌가 없으면 URL을 섞어 제목만 같은 서로 다른 글이 합쳐지지 않게 한다."""
    base = f"{collapse(title).lower()}\n{collapse(excerpt).lower()}"
    if not excerpt.strip():
        base += f"\n{normalized_url}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
