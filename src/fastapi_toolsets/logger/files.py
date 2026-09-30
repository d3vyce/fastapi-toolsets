"""Rotating log file shared by several processes."""

import logging
import os
import weakref
from logging.handlers import RotatingFileHandler
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

__all__ = ["SharedRotatingFileHandler"]


def _forget_inherited_files(handler: "SharedRotatingFileHandler | None") -> None:
    # A forked child shares its parent's descriptors, and so its lock.
    if handler is None:
        return
    handler._close_lock()
    if (stream := handler.stream) is not None:
        handler.stream = None
        # Whatever the parent had buffered at fork time is its own to write.
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, stream.fileno())
        finally:
            os.close(devnull)
        stream.close()


class SharedRotatingFileHandler(RotatingFileHandler):
    """A ``RotatingFileHandler`` that several processes can write to at once.

    Each record is written under a lock on a ``.lock`` file next to the log,
    so uvicorn workers share one file and one of them rotates it. A process
    whose file was renamed, by a sibling or by ``logrotate``, reopens it
    before writing. With ``maxBytes=0`` the handler never rotates and only
    follows external rotation. A process forked after the handler was built
    reopens the lock and the file, so it is excluded from its parent too.

    On platforms without ``fcntl`` the processes do not coordinate.

    Args:
        filename: Path of the log file.
        maxBytes: Size after which the file is rotated. ``0`` disables rotation.
        backupCount: Rotated files kept. ``0`` disables rotation.
        **kwargs: Passed to ``RotatingFileHandler``.
    """

    def __init__(
        self,
        filename: str | os.PathLike[str],
        *,
        maxBytes: int = 0,
        backupCount: int = 0,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("encoding", "utf-8")
        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount, **kwargs)
        self._lock_fd: int | None = None
        if hasattr(os, "register_at_fork"):
            ref = weakref.ref(self)
            os.register_at_fork(after_in_child=lambda: _forget_inherited_files(ref()))

    def _close_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _acquire(self) -> None:
        if fcntl is None:  # pragma: no cover
            return
        if self._lock_fd is None:
            self._lock_fd = os.open(
                f"{self.baseFilename}.lock", os.O_RDWR | os.O_CREAT, 0o644
            )
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX)

    def _release(self) -> None:
        if fcntl is None or self._lock_fd is None:  # pragma: no cover
            return
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

    def _reopen_if_moved(self) -> None:
        if self.stream is None:
            return
        try:
            on_disk = os.stat(self.baseFilename)
        except FileNotFoundError:
            moved = True
        else:
            opened = os.fstat(self.stream.fileno())
            moved = (on_disk.st_ino, on_disk.st_dev) != (opened.st_ino, opened.st_dev)
        if moved:
            self.stream.close()
            self.stream = self._open()

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self.maxBytes <= 0 or self.backupCount <= 0:
            return False
        if self.stream is None:
            self.stream = self._open()
        # Other processes append too, so the stream position is not the size.
        size = os.fstat(self.stream.fileno()).st_size
        return size + len(self.format(record)) + len(self.terminator) >= self.maxBytes

    def emit(self, record: logging.LogRecord) -> None:
        # Threads are already serialised by the handler lock ``handle`` holds.
        try:
            self._acquire()
        except OSError:
            self.handleError(record)
            return
        try:
            self._reopen_if_moved()
        except OSError:
            self._release()
            self.handleError(record)
            return
        try:
            super().emit(record)
        finally:
            self._release()

    def close(self) -> None:
        self.acquire()
        try:
            self._close_lock()
        finally:
            self.release()
        super().close()
