"""Stable domain error codes; transports may translate their presentation."""


class DomainError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(f"{code}: {self.message}")


class StorageError(RuntimeError):
    """State could not be validated, loaded or safely saved."""


class SyncError(RuntimeError):
    """An external synchronization did not complete successfully."""
