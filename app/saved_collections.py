"""Named Slink collections saved from the UI, so links don't have to be pasted again."""

import json
import threading
from pathlib import Path

from .importer import parse_collection

MAX_NAME_LENGTH = 100


class CollectionStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._items: list[dict] = json.loads(path.read_text()) if path.exists() else []

    def list(self) -> list[dict]:
        with self._lock:
            return sorted((dict(c) for c in self._items), key=lambda c: c["name"].lower())

    def save(self, name: str, link: str) -> dict:
        """Add a collection, or rename it if the same collection ID is already saved."""
        name = " ".join(name.split())
        if not name:
            raise ValueError("name is required")
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError(f"name must be at most {MAX_NAME_LENGTH} characters")
        collection = {"id": parse_collection(link), "name": name}
        with self._lock:
            self._items = [c for c in self._items if c["id"] != collection["id"]]
            self._items.append(collection)
            self._write()
        return collection

    def delete(self, collection_id: str) -> bool:
        with self._lock:
            remaining = [c for c in self._items if c["id"] != collection_id]
            if len(remaining) == len(self._items):
                return False
            self._items = remaining
            self._write()
            return True

    def _write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._items, indent=2))
        tmp.replace(self.path)
