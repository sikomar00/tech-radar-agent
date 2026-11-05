"""수집 스파이크: GitHub·Hacker News·RSS에서 원본 샘플을 읽기 전용으로 모은다.

저장하는 것은 제목, URL, 짧은 발췌, 메타데이터뿐이다(PRD S1-06).
shortcut: xml.etree로 외부 XML을 파싱한다. 스파이크라서 피드를 직접 고르지만, 어댑터를 만들 때는 defusedxml 도입 여부를 Plan에서 정한다.
"""
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OUT = Path(__file__).parent / "samples"
USER_AGENT = "tech-radar-agent-spike (personal research, read-only)"
EXCERPT_LEN = 280
PAUSE_SECONDS = 1.0

SEED_REPOS = [
    "anthropics/claude-code",
    "langchain-ai/langchain",
    "vercel/next.js",
    "ollama/ollama",
    "modelcontextprotocol/python-sdk",
]
FEEDS = {
    "github-blog": "https://github.blog/feed/",
    "cloudflare": "https://blog.cloudflare.com/rss/",
    "huggingface": "https://huggingface.co/blog/feed.xml",
    "simon-willison": "https://simonwillison.net/atom/everything/",
    "hacker-news-best": "https://hnrss.org/best",
    "ieee-spectrum": "https://spectrum.ieee.org/feeds/feed.rss",
    "nasa": "https://www.nasa.gov/feed/",
}

run_log: list[dict] = []


def excerpt(text: str | None) -> str:
    plain = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", plain).strip()[:EXCERPT_LEN]


def fetch(source: str, url: str) -> tuple[bytes, dict]:
    """한 번 호출하고 성공·실패를 run_log에 남긴다. 실패하면 예외를 그대로 올린다."""
    entry = {"source": source, "url": url}
    run_log.append(entry)
    time.sleep(PAUSE_SECONDS)
    try:
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as res:
            entry.update(status=res.status, ratelimit_remaining=res.headers.get("X-RateLimit-Remaining"))
            return res.read(), dict(res.headers)
    except (URLError, TimeoutError) as err:
        entry.update(status="error", error=repr(err))
        raise


def collect_github() -> list[dict]:
    since = (date.today() - timedelta(days=7)).isoformat()
    query = urlencode({"q": f"created:>{since} stars:>20", "sort": "stars", "order": "desc", "per_page": 30})
    body, _ = fetch("github-search", f"https://api.github.com/search/repositories?{query}")
    items = [
        {"kind": "new_repo", "external_id": str(r["id"]), "title": r["full_name"], "url": r["html_url"],
         "excerpt": excerpt(r["description"]), "stars": r["stargazers_count"], "language": r["language"],
         "topics": r.get("topics", []), "created_at": r["created_at"], "pushed_at": r["pushed_at"],
         "archived": r["archived"], "fork": r["fork"]}
        for r in json.loads(body)["items"]
    ]
    for repo in SEED_REPOS:
        body, _ = fetch("github-releases", f"https://api.github.com/repos/{repo}/releases?per_page=3")
        items += [
            {"kind": "release", "external_id": str(r["id"]), "title": f"{repo} {r['tag_name']}", "url": r["html_url"],
             "excerpt": excerpt(r["body"]), "published_at": r["published_at"], "prerelease": r["prerelease"]}
            for r in json.loads(body)
        ]
    return items


def collect_hn() -> list[dict]:
    """공식 Firebase API만 쓴다. 속도 제한이 없다고 명시돼 있다(Algolia는 약관을 확인하지 못해 제외)."""
    base = "https://hacker-news.firebaseio.com/v0"
    items = []
    for kind in ("topstories", "showstories"):
        body, _ = fetch(f"hn-{kind}", f"{base}/{kind}.json")
        for story_id in json.loads(body)[:15]:
            body, _ = fetch(f"hn-{kind}-item", f"{base}/item/{story_id}.json")
            story = json.loads(body)
            if not story or story.get("dead") or story.get("deleted"):
                continue
            items.append({"kind": kind, "external_id": str(story["id"]), "title": story.get("title"),
                          "url": story.get("url"), "points": story.get("score"),
                          "num_comments": story.get("descendants"), "created_at": story.get("time")})
    return items


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(node: ET.Element, *names: str) -> str | None:
    for child in node:
        if local_name(child.tag) in names:
            return child.get("href") or (child.text or "").strip()
    return None


def collect_rss() -> list[dict]:
    items = []
    for name, url in FEEDS.items():
        try:
            body, _ = fetch(f"rss-{name}", url)
            root = ET.fromstring(body)
        except (URLError, TimeoutError, ET.ParseError) as err:
            run_log[-1].setdefault("error", repr(err))
            continue
        entries = [n for n in root.iter() if local_name(n.tag) in ("item", "entry")][:30]
        items += [
            {"kind": "rss", "feed": name, "external_id": child_text(e, "guid", "id"), "title": child_text(e, "title"),
             "url": child_text(e, "link"), "published_at": child_text(e, "pubDate", "published", "updated"),
             "excerpt": excerpt(child_text(e, "description", "summary"))}
            for e in entries
        ]
    return items


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, collector in (("github", collect_github), ("hn", collect_hn), ("rss", collect_rss)):
        try:
            items = collector()
        except (URLError, TimeoutError, ValueError, KeyError) as err:
            items = []
            run_log.append({"source": name, "error": f"collector failed: {err!r}"})
        (OUT / f"{name}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{name}: {len(items)}건")
    (OUT / "run_log.json").write_text(json.dumps(run_log, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
