"""외부 API 접근은 여기서만 한다. domain에만 의존한다."""
from radar.adapters import github, hackernews, rss

ADAPTERS = {"github": github.fetch, "hackernews": hackernews.fetch, "rss": rss.fetch}
