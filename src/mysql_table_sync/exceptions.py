"""Application-level exceptions."""


class SyncError(Exception):
    """Base exception for synchronization failures."""


class SyncStrategyNotConfiguredError(SyncError):
    """Raised until table mapping and duplicate handling are explicitly selected."""
