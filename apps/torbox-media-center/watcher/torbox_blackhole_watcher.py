#!/usr/bin/env python3
import json
import mimetypes
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


API_URL = "https://api.torbox.app/v1/api/torrents/createtorrent"
WATCH_DIR = Path(os.environ.get("WATCH_DIR", "/blackhole"))
PROCESSED_DIR = Path(os.environ.get("PROCESSED_DIR", "/blackhole/.processed"))
FAILED_DIR = Path(os.environ.get("FAILED_DIR", "/blackhole/.failed"))
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "15"))
DELETE_AFTER_UPLOAD = os.environ.get("DELETE_AFTER_UPLOAD", "true").lower() == "true"
API_KEY = os.environ.get("TORBOX_API_KEY", "")


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def multipart(fields, files):
    boundary = "----torboxblackhole%08x" % int(time.time())
    body = bytearray()

    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(str(value).encode())
        body.extend(b"\r\n")

    for name, path in files.items():
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/x-bittorrent"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode()
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode())
        body.extend(data)
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def upload(path):
    suffix = path.suffix.lower()
    fields = {"seed": "1"}
    files = {}

    if suffix == ".torrent":
        files["file"] = path
    elif suffix == ".magnet":
        fields["magnet"] = path.read_text(errors="replace").strip()
    else:
        return False, "unsupported file type"

    data, content_type = multipart(fields, files)
    request = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": content_type,
            "User-Agent": "torbox-blackhole-watcher/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read().decode("utf-8", errors="replace")
            if response.status != 200:
                return False, f"HTTP {response.status}: {payload}"
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                parsed = {}
            if parsed.get("success") is False:
                return False, payload
            return True, payload
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        if err.code == 429:
            retry_after = int(err.headers.get("retry-after", 160))
            log(f"Rate limited — waiting {retry_after}s before retrying")
            time.sleep(retry_after)
            return None, "rate_limited"
        return False, f"HTTP {err.code}: {body}"
    except Exception as err:
        return False, str(err)


def settle(path):
    try:
        first = path.stat().st_size
        time.sleep(0.3)
        return path.exists() and path.stat().st_size == first
    except FileNotFoundError:
        return False


def move_or_delete(path, ok):
    if DELETE_AFTER_UPLOAD and ok:
        path.unlink(missing_ok=True)
        return

    target_dir = PROCESSED_DIR if ok else FAILED_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}-{int(time.time())}{path.suffix}"
    shutil.move(str(path), str(target))


def main():
    if not API_KEY or API_KEY == "PASTE_TORBOX_API_KEY_HERE":
        log("TORBOX_API_KEY is missing.")
        sys.exit(1)

    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    log(f"watching {WATCH_DIR}")

    while True:
        for path in sorted(WATCH_DIR.rglob("*")):
            if not path.is_file():
                continue
            if any(part.startswith(".") for part in path.relative_to(WATCH_DIR).parts):
                continue
            if path.suffix.lower() not in (".torrent", ".magnet"):
                continue
            if not settle(path):
                continue

            ok, detail = upload(path)
            if ok is None:  # rate limited — upload() already slept retry-after; retry now
                continue
            elif ok:
                log(f"uploaded {path.name}")
            else:
                log(f"failed {path.name}: {detail}")
            move_or_delete(path, ok)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
