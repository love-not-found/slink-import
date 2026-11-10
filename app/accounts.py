"""Slink accounts (API keys) the importer can upload as. Keys are never returned to the browser."""

import json
import os
import threading
import uuid
from pathlib import Path

# The account configured through SLINK_API_KEY; it also owns data from before accounts existed
DEFAULT_ACCOUNT = "default"
MAX_NAME_LENGTH = 100


def key_hint(key: str) -> str:
    return f"{key[:3]}…{key[-4:]}" if len(key) > 10 else "…"


class AccountStore:
    def __init__(self, path: Path, env_key: str):
        self.path = path
        self.env_key = env_key
        self._lock = threading.Lock()
        self._items: list[dict] = json.loads(path.read_text()) if path.exists() else []

    def _all(self) -> list[dict]:
        builtin = [{"id": DEFAULT_ACCOUNT, "name": "Default", "key": self.env_key, "builtin": True}]
        return (builtin if self.env_key else []) + self._items

    def list(self) -> list[dict]:
        with self._lock:
            return [
                {"id": a["id"], "name": a["name"], "keyHint": key_hint(a["key"]), "builtin": a.get("builtin", False)}
                for a in self._all()
            ]

    def get(self, account_id: str) -> dict | None:
        with self._lock:
            return next((dict(a) for a in self._all() if a["id"] == account_id), None)

    def add(self, name: str, key: str) -> dict:
        name = " ".join(name.split())
        key = key.strip()
        if not name:
            raise ValueError("name is required")
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError(f"name must be at most {MAX_NAME_LENGTH} characters")
        if not key or any(c.isspace() for c in key):
            raise ValueError("API key is required and can't contain spaces")
        with self._lock:
            for existing in self._all():
                if existing["key"] == key:
                    raise ValueError(f'this API key is already saved as "{existing["name"]}"')
            account = {"id": uuid.uuid4().hex[:8], "name": name, "key": key}
            self._items.append(account)
            self._write()
        return {"id": account["id"], "name": name, "keyHint": key_hint(key), "builtin": False}

    def delete(self, account_id: str) -> bool:
        if account_id == DEFAULT_ACCOUNT:
            raise ValueError("the Default account comes from SLINK_API_KEY and can't be removed here")
        with self._lock:
            remaining = [a for a in self._items if a["id"] != account_id]
            if len(remaining) == len(self._items):
                return False
            self._items = remaining
            self._write()
            return True

    def _write(self) -> None:
        # Holds API keys, so keep it readable by the app user only
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._items, f, indent=2)
        tmp.replace(self.path)
