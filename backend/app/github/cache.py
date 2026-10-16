"""In-memory ETag and raw response cache for GitHub API requests.

Stores raw response payloads alongside their HTTP ETag headers.
Enables HTTP 304 (Not Modified) responses to avoid consuming rate limit quota.
"""

from dataclasses import dataclass
import time
from typing import Any, Dict, Optional, Tuple


@dataclass
class CacheEntry:
    """Represents a cached API response entry."""
    etag: Optional[str]
    data: Any
    cached_at: float


class ETagCache:
    """Thread-safe in-memory cache for raw responses and ETags."""

    def __init__(self, max_entries: int = 500) -> None:
        self._entries: Dict[str, CacheEntry] = {}
        self._max_entries = max_entries

    def get(self, key: str) -> Optional[Tuple[Optional[str], Any]]:
        """Retrieve the cached ETag and payload for a given cache key (URL/params).

        Returns:
            Tuple of (etag, cached_data) if present, else None.
        """
        entry = self._entries.get(key)
        if entry is not None:
            return entry.etag, entry.data
        return None

    def set(self, key: str, etag: Optional[str], data: Any) -> None:
        """Store an ETag and raw payload in the cache.

        Evicts oldest entries if capacity is reached.
        """
        if len(self._entries) >= self._max_entries:
            # Evict the oldest key
            oldest_key = min(self._entries, key=lambda k: self._entries[k].cached_at)
            self._entries.pop(oldest_key, None)

        self._entries[key] = CacheEntry(
            etag=etag,
            data=data,
            cached_at=time.time(),
        )

    def has(self, key: str) -> bool:
        """Check if a cache key exists."""
        return key in self._entries

    def clear(self) -> None:
        """Clear all cached entries."""
        self._entries.clear()

    @property
    def size(self) -> int:
        """Return current number of cached entries."""
        return len(self._entries)
