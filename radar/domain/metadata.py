from datetime import datetime
from typing import Any

MAX_SNAPSHOTS = 10


def merge_metadata(old: dict[str, Any], new: dict[str, Any], at: datetime) -> dict[str, Any]:
    """새 metadata로 덮되, 스타 수는 시점별 스냅샷으로 이어 붙인다(2단계의 스타 증가율 계산용)."""
    merged = {**old, **new}
    if "stars" in new:
        history = [*old.get("stars_snapshots", []), {"at": at.isoformat(), "stars": new["stars"]}]
        merged["stars_snapshots"] = history[-MAX_SNAPSHOTS:]
    return merged
