"""Bounded audio segment queue and capture error."""

import threading
from collections import deque
from uuid import uuid4


class AudioError(RuntimeError):
    pass


class SegmentQueue:
    """Bounded FIFO with monotonically numbered capture events."""

    def __init__(self, max_bytes, max_segments=4):
        self.max_bytes = max_bytes
        self.max_segments = max_segments
        self._items = deque()
        self._bytes = 0
        self._session_id = uuid4()
        self._last_id = 0
        self._dropped = 0
        self._closed = False
        self._lock = threading.Lock()

    def put(self, segment_id, wav, silence_ms=0):
        with self._lock:
            if self._closed or segment_id <= self._last_id:
                return False
            self._last_id = segment_id
            if (
                len(self._items) >= self.max_segments
                or self._bytes + len(wav) > self.max_bytes
            ):
                self._dropped += 1
                return False
            self._items.append(((self._session_id, segment_id), wav, silence_ms))
            self._bytes += len(wav)
            return True

    def get(self):
        with self._lock:
            if not self._items:
                return None
            segment = self._items.popleft()
            self._bytes -= len(segment[1])
            return segment

    def take_overflow(self):
        with self._lock:
            count, self._dropped = self._dropped, 0
            return count

    def close(self):
        with self._lock:
            self._closed = True
            self._items.clear()
            self._bytes = 0
