#!/usr/bin/env python3
"""Download images from direct links (e.g. i.postimg.cc) and upload them to Slink.

Uses Slink's external upload API: POST /api/external/upload with an API key
(create one in Profile -> API Keys).
"""

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def read_links(path: Path) -> list[str]:
    links = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            links.append(line)
    # Drop duplicates while keeping order
    return list(dict.fromkeys(links))


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def filename_from(url: str, content_type: str | None) -> str:
    name = unquote(os.path.basename(urlparse(url).path)) or "image"
    if not os.path.splitext(name)[1] and content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            name += ext
    return name


def download(session: requests.Session, url: str) -> tuple[bytes, str, str]:
    resp = session.get(url, timeout=60, headers={"Referer": "https://postimg.cc/"})
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        raise ValueError(
            f"not an image (Content-Type: {content_type or 'unknown'}); "
            "is this a direct i.postimg.cc link?"
        )
    return resp.content, filename_from(url, content_type), content_type


def upload(
    session: requests.Session,
    base_url: str,
    api_key: str,
    data: bytes,
    filename: str,
    content_type: str,
    public: bool,
    description: str,
) -> dict:
    resp = session.post(
        f"{base_url}/api/external/upload",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"image": (filename, data, content_type)},
        data={"isPublic": "true" if public else "false", "description": description},
        timeout=120,
    )
    if resp.status_code != 201:
        raise RuntimeError(f"upload failed ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("links", type=Path, help="text file with one image URL per line")
    parser.add_argument(
        "--url",
        default=os.environ.get("SLINK_URL", "https://i.hellish.dev"),
        help="Slink base URL (env: SLINK_URL, default: %(default)s)",
    )
    parser.add_argument(
        "--api-key-file",
        type=Path,
        default=Path("api.secret"),
        help="file containing the Slink API key (default: %(default)s)",
    )
    parser.add_argument("--public", action="store_true", help="mark uploaded images as public")
    parser.add_argument(
        "--description",
        default="",
        help="description to set on every image ('{url}' is replaced with the source link)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("slink_import_state.json"),
        help="file tracking already imported links, so reruns skip them (default: %(default)s)",
    )
    parser.add_argument("--delay", type=float, default=0.5, help="seconds to wait between images")
    parser.add_argument("--dry-run", action="store_true", help="only download, don't upload")
    args = parser.parse_args()

    api_key = ""
    if not args.dry_run:
        if not args.api_key_file.is_file():
            parser.error(f"API key file not found: {args.api_key_file}")
        api_key = args.api_key_file.read_text().strip()
        if not api_key:
            parser.error(f"API key file is empty: {args.api_key_file}")

    base_url = args.url.rstrip("/")
    links = read_links(args.links)
    state = load_state(args.state)

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    done = failed = skipped = 0
    for i, link in enumerate(links, 1):
        prefix = f"[{i}/{len(links)}]"
        if link in state:
            skipped += 1
            continue
        try:
            data, filename, content_type = download(session, link)
            if args.dry_run:
                print(f"{prefix} OK (dry run) {link} -> {filename} ({len(data)} bytes)")
                done += 1
                continue
            result = upload(
                session,
                base_url,
                api_key,
                data,
                filename,
                content_type,
                args.public,
                args.description.replace("{url}", link)[:255],
            )
            state[link] = {"id": result.get("id"), "url": f"{base_url}/{result.get('url', '')}"}
            save_state(args.state, state)
            print(f"{prefix} OK {link} -> {state[link]['url']}")
            done += 1
        except Exception as e:  # keep going on individual failures
            print(f"{prefix} FAIL {link}: {e}", file=sys.stderr)
            failed += 1
        time.sleep(args.delay)

    print(f"\nImported: {done}, skipped (already imported): {skipped}, failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
