class JobFailed(Exception):
    """잡을 실패로 처리해야 할 때 올린다. retryable이 False면 다시 시도해도 소용없는 잡이다."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable
