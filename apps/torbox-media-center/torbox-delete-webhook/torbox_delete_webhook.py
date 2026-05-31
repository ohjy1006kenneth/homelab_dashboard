#!/usr/bin/env python3
import json
import os
import re
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

TORBOX_API_KEY = os.environ["TORBOX_API_KEY"]
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://172.17.0.1:8097")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def torbox_request(path, method="GET", data=None):
    url = f"https://api.torbox.app/v1/api{path}"
    body = json.dumps(data).encode() if data else None
    headers = {
        "Authorization": f"Bearer {TORBOX_API_KEY}",
        "User-Agent": "torbox-delete-webhook/1.0",
    }
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def get_torbox_torrents():
    result = torbox_request("/torrents/mylist?bypass_cache=true")
    return (result or {}).get("data") or []


def delete_torbox_torrent(torrent_id, name):
    torbox_request("/torrents/controltorrent", method="POST",
                   data={"torrent_id": torrent_id, "operation": "delete"})
    log(f"  Deleted TorBox torrent {torrent_id}: {name}")


def jellyfin_delete(title):
    if not JELLYFIN_API_KEY:
        return
    try:
        headers = {"X-MediaBrowser-Token": JELLYFIN_API_KEY}
        search_url = (
            f"{JELLYFIN_URL}/Items"
            f"?searchTerm={urllib.parse.quote(title)}&Recursive=true&Limit=10"
        )
        req = urllib.request.Request(search_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            items = json.loads(resp.read()).get("Items", [])

        norm = normalize(title)
        deleted = 0
        for item in items:
            if normalize(item.get("Name", "")) == norm:
                item_id = item["Id"]
                del_req = urllib.request.Request(
                    f"{JELLYFIN_URL}/Items/{item_id}",
                    headers=headers,
                    method="DELETE",
                )
                urllib.request.urlopen(del_req, timeout=10)
                log(f"  Removed from Jellyfin: {item['Name']} ({item['Type']})")
                deleted += 1

        if deleted == 0:
            log(f"  Not found in Jellyfin: '{title}'")
    except Exception as e:
        log(f"  Jellyfin delete failed: {e}")


def normalize(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def title_matches(title, torrent_name):
    words = normalize(title).split()
    norm = normalize(torrent_name)
    return bool(words) and all(w in norm for w in words)


def handle_delete(title):
    log(f"Searching TorBox for torrents matching '{title}'")
    torrents = get_torbox_torrents()
    deleted = 0
    for t in torrents:
        if title_matches(title, t.get("name", "")):
            delete_torbox_torrent(t["id"], t["name"])
            deleted += 1
    if deleted == 0:
        log(f"  No matching TorBox torrents found for '{title}'")
    else:
        log(f"  Removed {deleted} torrent(s) from TorBox")
    jellyfin_delete(title)


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return

        event = payload.get("eventType", "")

        if event == "Test":
            log("Test event received — webhook is working")
            return

        if event == "SeriesDelete" and payload.get("deletedFiles"):
            title = (payload.get("series") or {}).get("title")
            if title:
                log(f"SeriesDelete: {title}")
                handle_delete(title)

        elif event == "MovieDelete" and payload.get("deletedFiles"):
            title = (payload.get("movie") or {}).get("title")
            if title:
                log(f"MovieDelete: {title}")
                handle_delete(title)

    def log_message(self, *args):
        pass


def main():
    server = HTTPServer(("0.0.0.0", 8008), WebhookHandler)
    log("TorBox delete webhook listening on :8008")
    server.serve_forever()


if __name__ == "__main__":
    main()
