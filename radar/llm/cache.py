"""평가용 응답 파일 캐시. 임시 스키마는 지워지므로 같은 입력을 다시 부르지 않으려면 파일에 남겨야 한다(무료 한도를 아끼려고)."""
import hashlib
import json
from pathlib import Path
from typing import Any

from radar.llm import LlmClient, LlmResult


class FileCachedLlm:
    """성공한 응답만 모델·지시문·스키마·데이터가 같을 때 재사용한다. 오류는 저장하지 않는다."""

    def __init__(self, inner: LlmClient, path: Path) -> None:
        self.model = inner.model
        self._inner = inner
        self._path = path
        self._store: dict[str, dict[str, Any]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.calls = 0
        self.hits = 0

    def complete_json(self, instruction: str, data: str, schema: dict[str, Any]) -> LlmResult:
        key = hashlib.sha256(json.dumps([self.model, instruction, schema, data], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key in self._store:
            self.hits += 1
            return LlmResult(**self._store[key])
        result = self._inner.complete_json(instruction, data, schema)
        self.calls += 1
        self._store[key] = {"data": result.data, "tokens_in": result.tokens_in, "tokens_out": result.tokens_out}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._store, ensure_ascii=False), encoding="utf-8")
        return result
