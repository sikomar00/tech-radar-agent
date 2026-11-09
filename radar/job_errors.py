from datetime import datetime


class JobFailed(Exception):
    """잡을 실패로 처리해야 할 때 올린다. retryable이 False면 다시 시도해도 소용없는 잡이다."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class JobDeferred(Exception):
    """지금은 할 수 없지만 실패도 아닌 잡(예: 시간당 한도). until 뒤로 미루고 시도 횟수는 쓰지 않는다."""

    def __init__(self, message: str, until: datetime) -> None:
        super().__init__(message)
        self.until = until
