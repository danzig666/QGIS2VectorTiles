"""
Progress and cancellation for long publishing steps.

Accepts a QgsProcessingFeedback / QgsFeedback-like object (``setProgress``,
``isCanceled``, ``pushInfo``), a plain callable ``(percent, message)`` or
nothing. Pure Python: never touches widgets (callers marshal to the GUI).
"""

import time
from typing import Callable, Optional

from .errors import Cancelled


class Progress:
    """Reports ``[start, end]`` percent sub-ranges of a parent feedback."""

    def __init__(self, feedback=None, start: float = 0.0, end: float = 100.0,
                 cancel: Optional[Callable[[], bool]] = None, min_interval: float = 0.2):
        self.feedback = feedback
        self.start = start
        self.end = end
        self._cancel = cancel
        self._min_interval = min_interval
        self._last = 0.0

    def sub(self, start_fraction: float, end_fraction: float) -> "Progress":
        span = self.end - self.start
        return Progress(self.feedback, self.start + span * start_fraction,
                        self.start + span * end_fraction, self._cancel, self._min_interval)

    def canceled(self) -> bool:
        if self._cancel is not None and self._cancel():
            return True
        checker = getattr(self.feedback, "isCanceled", None)
        return bool(checker()) if callable(checker) else False

    def check(self) -> None:
        if self.canceled():
            raise Cancelled()

    def update(self, fraction: float, message: str = "", force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last < self._min_interval:
            return
        self._last = now
        percent = self.start + (self.end - self.start) * max(0.0, min(1.0, fraction))
        if self.feedback is None:
            return
        if callable(self.feedback) and not hasattr(self.feedback, "setProgress"):
            self.feedback(percent, message)
            return
        setter = getattr(self.feedback, "setProgress", None)
        if callable(setter):
            setter(percent)
        if message:
            self.info(message)

    def info(self, message: str) -> None:
        push = getattr(self.feedback, "pushInfo", None)
        if callable(push):
            push(message)
        elif callable(self.feedback) and not hasattr(self.feedback, "setProgress"):
            self.feedback(None, message)
