"""Download images from direct links and upload them to Slink."""

import json
import logging
import mimetypes
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

log = logging.getLogger("slink_import")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# Upload errors worth retrying: the request most likely never reached Slink
RETRYABLE_UPLOAD_STATUS = {502, 503}
RETRY_DELAY = 2.0


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_links(text: str) -> list[str]:
    """One link per line; blank lines and '#' comments are ignored, duplicates dropped."""
    links = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            links.append(line)
    return list(dict.fromkeys(links))


def parse_collection(value: str) -> str:
    """Accept a bare collection ID or a full .../collection/<id> URL."""
    candidate = value.strip().rstrip("/").rsplit("/", 1)[-1]
    try:
        return str(uuid.UUID(candidate))
    except ValueError:
        raise ValueError(f"not a valid collection ID or URL: {value}") from None


def format_failed_links(failures: dict[str, str]) -> str:
    lines = []
    for link, error in failures.items():
        lines.append(f"# {' '.join(error.split())}")
        lines.append(link)
    return "\n".join(lines) + "\n" if lines else ""


class StateStore:
    """Thread-safe JSON record of every link that was imported or failed."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict = json.loads(path.read_text()) if path.exists() else {}

    def is_imported(self, link: str) -> bool:
        with self._lock:
            entry = self._data.get(link)
        # Entries written before "status" existed were always successful imports
        return entry is not None and entry.get("status", "ok") == "ok"

    def get(self, link: str) -> dict | None:
        with self._lock:
            entry = self._data.get(link)
            return dict(entry) if entry else None

    def set(self, link: str, entry: dict) -> None:
        with self._lock:
            self._data[link] = entry
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=2))
            tmp.replace(self.path)


@dataclass(frozen=True)
class ImportOptions:
    public: bool = False
    description: str = ""
    collection_ids: tuple[str, ...] = ()


class TransientError(Exception):
    pass


class SlinkClient:
    def __init__(self, base_url: str, api_key: str, max_bytes: int):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_bytes = max_bytes
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        # requests.Session isn't guaranteed thread-safe, so use one per worker thread
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
            self._local.session = session
        return session

    def download(self, url: str) -> tuple[bytes, str, str]:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("not an http(s) URL")
        headers = {}
        if parsed.netloc.endswith("postimg.cc"):
            headers["Referer"] = "https://postimg.cc/"
        try:
            resp = self.session.get(url, timeout=60, stream=True, headers=headers)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise TransientError(f"download failed: {e}") from e
        with resp:
            if resp.status_code >= 500:
                raise TransientError(f"download failed ({resp.status_code} {resp.reason})")
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
            if not content_type.startswith("image/"):
                raise ValueError(
                    f"not an image (Content-Type: {content_type or 'unknown'}); "
                    "is this a direct image link?"
                )
            chunks, size = [], 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise ValueError(f"image larger than {self.max_bytes // (1024 * 1024)} MB")
                chunks.append(chunk)
        return b"".join(chunks), filename_from(url, content_type), content_type

    def upload(
        self, data: bytes, filename: str, content_type: str, options: ImportOptions, description: str
    ) -> str:
        fields = [
            ("isPublic", "true" if options.public else "false"),
            ("description", description),
        ]
        fields += [("collectionIds[]", cid) for cid in options.collection_ids]
        try:
            resp = self.session.post(
                f"{self.base_url}/api/external/upload",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    # Slink's SvelteKit frontend rejects form POSTs without a same-site Origin
                    "Origin": self.base_url,
                },
                files={"image": (filename, data, content_type)},
                data=fields,
                timeout=120,
            )
        except requests.ConnectionError as e:
            raise TransientError(f"upload failed: {e}") from e
        if resp.status_code in RETRYABLE_UPLOAD_STATUS:
            raise TransientError(f"upload failed ({resp.status_code}): {resp.text[:300]}")
        if resp.status_code != 201:
            raise RuntimeError(f"upload failed ({resp.status_code}): {resp.text[:300]}")
        return f"{self.base_url}/{resp.json().get('url', '')}"

    def import_link(self, link: str, options: ImportOptions) -> str:
        """Import one link (retrying once on transient errors) and return its Slink URL."""
        description = options.description.replace("{url}", link)[:255]
        for attempt in (1, 2):
            try:
                data, filename, content_type = self.download(link)
                return self.upload(data, filename, content_type, options, description)
            except TransientError:
                if attempt == 2:
                    raise
                time.sleep(RETRY_DELAY)
        raise AssertionError("unreachable")


def filename_from(url: str, content_type: str | None) -> str:
    name = unquote(os.path.basename(urlparse(url).path)) or "image"
    if not os.path.splitext(name)[1] and content_type:
        ext = mimetypes.guess_extension(content_type)
        if ext:
            name += ext
    return name


def run_import(
    client: SlinkClient,
    state: StateStore,
    links: list[str],
    options: ImportOptions,
    workers: int,
    on_update: Callable[[str, dict], None],
) -> None:
    """Import links in parallel; on_update(link, entry) is called when each one finishes."""

    def work(link: str) -> None:
        on_update(link, {"status": "running"})
        try:
            url = client.import_link(link, options)
            entry = {"status": "ok", "url": url, "at": now()}
            log.info("OK %s -> %s", link, url)
        except Exception as e:  # keep going on individual failures
            entry = {"status": "failed", "error": str(e), "at": now()}
            log.warning("FAIL %s: %s", link, e)
        state.set(link, entry)
        on_update(link, entry)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, links))
