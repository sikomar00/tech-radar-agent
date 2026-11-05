import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> None:
    """`KEY=VALUE` 줄을 환경 변수로 읽는다. 이미 설정된 값은 덮지 않는다."""
    env_file = Path(path)
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL이 없습니다. .env에 DATABASE_URL=... 한 줄을 넣어 주세요.")
    return url
