"""Bounded, process-local cache for immutable, permission-checked thumbnails."""

from collections import OrderedDict
from collections.abc import Callable
from threading import Lock


class ThumbnailCache:
    def __init__(self, max_bytes: int = 16 * 1024 * 1024, max_items: int = 64):
        self.max_bytes = max_bytes
        self.max_items = max_items
        self._items: OrderedDict[tuple[str, str], tuple[bytes, str]] = OrderedDict()
        self._bytes = 0
        self._lock = Lock()

    def get(self, key: tuple[str, str], render: Callable[[], tuple[bytes, str]]):
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
            value = render()
            size = len(value[0])
            if size <= self.max_bytes:
                while self._items and (
                    self._bytes + size > self.max_bytes or len(self._items) >= self.max_items
                ):
                    _, removed = self._items.popitem(last=False)
                    self._bytes -= len(removed[0])
                self._items[key] = value
                self._bytes += size
            return value
