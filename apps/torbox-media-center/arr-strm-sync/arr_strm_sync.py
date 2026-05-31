#!/usr/bin/env python3
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


TORBOX_API_KEY = os.environ["TORBOX_API_KEY"]
RADARR_API_KEY = os.environ["RADARR_API_KEY"]
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")
RADARR_URL = os.environ.get("RADARR_URL", "http://172.17.0.1:7878").rstrip("/")
SONARR_URL = os.environ.get("SONARR_URL", "http://172.17.0.1:8989").rstrip("/")
MOVIES_DIR = Path(os.environ.get("MOVIES_DIR", "/media/movies"))
TV_DIR = Path(os.environ.get("TV_DIR", "/media/tv"))
SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "120"))
RADARR_ROOT = os.environ.get("RADARR_ROOT", "/movies")
SONARR_ROOT = os.environ.get("SONARR_ROOT", "/tv")

EXTRA_TERMS = (
    "behind the scenes",
    "deleted scene",
    "deleted scenes",
    "extra",
    "extras",
    "featurette",
    "gag reel",
    "interview",
    "sample",
    "trailer",
)


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def api_json(url, method="GET", data=None, headers=None):
    headers = headers or {}
    if data is not None:
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read()
        if not raw:
            return None
        return json.loads(raw.decode())


def radarr(path, method="GET", data=None):
    sep = "&" if "?" in path else "?"
    return api_json(f"{RADARR_URL}{path}{sep}apikey={RADARR_API_KEY}", method, data)


def sonarr(path, method="GET", data=None):
    sep = "&" if "?" in path else "?"
    return api_json(f"{SONARR_URL}{path}{sep}apikey={SONARR_API_KEY}", method, data)


def torbox_torrents():
    req = urllib.request.Request(
        "https://api.torbox.app/v1/api/torrents/mylist?bypass_cache=true",
        headers={
            "Authorization": f"Bearer {TORBOX_API_KEY}",
            "User-Agent": "torbox-arr-strm-sync/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode())
    return payload.get("data") or []


def torbox_delete_torrent(torrent_id):
    try:
        data = json.dumps({"torrent_id": int(torrent_id), "operation": "delete"}).encode()
    except (TypeError, ValueError):
        return False
    req = urllib.request.Request(
        "https://api.torbox.app/v1/api/torrents/controltorrent",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {TORBOX_API_KEY}",
            "User-Agent": "torbox-arr-strm-sync/1.0",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        log(f"deleted superseded TorBox torrent {torrent_id}")
        return True
    except (urllib.error.URLError, OSError) as error:
        log(f"failed deleting TorBox torrent {torrent_id}: {error}")
        return False


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "movie"


def quality_rank(name):
    """Rank a quality name so 2160p > 1080p > 720p, breaking ties by source.
    For 2160p: WEBDL > WEBRip > Bluray > Remux (WEBDL preferred for compatibility).
    For 1080p and below: Remux > Bluray > WEBDL > WEBRip (normal quality order).
    """
    name = (name or "").lower()
    is_4k = "2160" in name or "4k" in name or "uhd" in name
    if is_4k:
        res = 2160
    elif "1080" in name:
        res = 1080
    elif "720" in name:
        res = 720
    elif "480" in name or "dvd" in name or "sdtv" in name:
        res = 480
    else:
        res = 0
    if is_4k:
        # At 4K, prefer WEBDL (EAC3 audio, lower bitrate, device-compatible)
        # over Remux (TrueHD/DTS audio forces transcode, huge bitrate)
        if "web-dl" in name or "webdl" in name or "web dl" in name:
            src = 5
        elif "webrip" in name or "web" in name:
            src = 4
        elif "bluray" in name or "blu-ray" in name:
            src = 3
        elif "remux" in name:
            src = 2
        elif "hdtv" in name:
            src = 1
        else:
            src = 0
    else:
        # Below 4K: standard quality order (Remux = best)
        if "remux" in name:
            src = 5
        elif "bluray" in name or "blu-ray" in name:
            src = 4
        elif "web-dl" in name or "webdl" in name or "web dl" in name:
            src = 3
        elif "webrip" in name or "web" in name:
            src = 2
        elif "hdtv" in name:
            src = 1
        else:
            src = 0
    return res * 10 + src


def quality_from_filename(name):
    stem = name[:-5] if name.lower().endswith(".strm") else name
    parts = stem.rsplit(" - ", 1)
    return parts[1] if len(parts) == 2 else ""


def parse_torrent_id(text):
    try:
        query = urllib.parse.urlparse((text or "").strip()).query
        return urllib.parse.parse_qs(query).get("torrent_id", [None])[0]
    except ValueError:
        return None


def strm_torrent_id(path):
    try:
        return parse_torrent_id(path.read_text(errors="replace"))
    except OSError:
        return None


def torrent_referenced_elsewhere(torrent_id, exclude_paths):
    torrent_id = str(torrent_id)
    for root in (MOVIES_DIR, TV_DIR):
        if not root.exists():
            continue
        for strm in root.rglob("*.strm"):
            if strm in exclude_paths:
                continue
            if strm_torrent_id(strm) == torrent_id:
                return True
    return False


def host_movie_path(radarr_path):
    if not radarr_path.startswith(RADARR_ROOT):
        return None
    rel = radarr_path[len(RADARR_ROOT):].lstrip("/")
    return MOVIES_DIR / rel


def host_series_path(sonarr_path):
    if not sonarr_path.startswith(SONARR_ROOT):
        return None
    rel = sonarr_path[len(SONARR_ROOT):].lstrip("/")
    return TV_DIR / rel


def latest_movie_grab(movie_id):
    history = radarr(f"/api/v3/history/movie?movieId={movie_id}") or []
    return latest_grab_from_history(history)


def latest_series_grab(series_id):
    history = sonarr(f"/api/v3/history/series?seriesId={series_id}") or []
    return latest_grab_from_history(history)


def latest_grab_from_history(history):
    grabs = [item for item in history if item.get("eventType") == "grabbed"]
    if not grabs:
        return None
    return sorted(grabs, key=lambda item: item.get("date", ""), reverse=True)[0]


def video_files(torrent):
    files = []
    for item in torrent.get("files") or []:
        mimetype = item.get("mimetype") or ""
        suffix = Path(item.get("short_name") or item.get("name") or "").suffix.lower()
        if mimetype.startswith("video/") or suffix in (".mkv", ".mp4", ".avi", ".mov", ".m4v"):
            files.append(item)
    return files


def choose_main_file(torrent):
    files = video_files(torrent)
    if not files:
        return None
    regular = []
    for item in files:
        haystack = f"{item.get('name', '')} {item.get('short_name', '')}".lower()
        if not any(term in haystack for term in EXTRA_TERMS):
            regular.append(item)
    candidates = regular or files
    return max(candidates, key=lambda item: int(item.get("size") or 0))


def matching_torrent(torrents, grab):
    info_hash = ((grab.get("data") or {}).get("torrentInfoHash") or "").lower()
    if info_hash:
        for torrent in torrents:
            hashes = [torrent.get("hash") or ""]
            hashes.extend(torrent.get("alternative_hashes") or [])
            if info_hash in [value.lower() for value in hashes]:
                return torrent
    source_title = (grab.get("sourceTitle") or "").lower()
    for torrent in torrents:
        if source_title and source_title in (torrent.get("name") or "").lower():
            return torrent
    return None


def strm_url(torrent, file_item):
    query = urllib.parse.urlencode(
        {
            "token": TORBOX_API_KEY,
            "torrent_id": torrent["id"],
            "file_id": file_item["id"],
            "redirect": "true",
        }
    )
    return f"https://api.torbox.app/v1/api/torrents/requestdl?{query}"


def current_movie_quality(movie):
    movie_file = movie.get("movieFile") or {}
    return (((movie_file.get("quality") or {}).get("quality") or {}).get("name")) or ""


def sync_movie(movie, torrents):
    grab = latest_movie_grab(movie["id"])
    if not grab:
        return False
    torrent = matching_torrent(torrents, grab)
    if not torrent or not torrent.get("cached") or not torrent.get("download_finished"):
        return False
    main_file = choose_main_file(torrent)
    movie_dir = host_movie_path(movie.get("path") or "")
    if not main_file or movie_dir is None:
        return False

    new_quality = (((grab.get("quality") or {}).get("quality") or {}).get("name")) or "Unknown"
    new_rank = quality_rank(new_quality)
    new_tid = str(torrent["id"])

    existing = list(movie_dir.glob("*.strm")) if movie_dir.exists() else []

    # Already streaming this exact torrent — nothing to do.
    if any(strm_torrent_id(path) == new_tid for path in existing):
        return False

    # If Radarr already imported a file, only continue when this is a real upgrade.
    if movie.get("hasFile"):
        if new_rank <= quality_rank(current_movie_quality(movie)):
            return False

    movie_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(f"{movie['title']} ({movie.get('year')}) - {new_quality}.strm")
    target = movie_dir / filename
    target.write_text(strm_url(torrent, main_file))

    old_tids = set()
    for path in existing:
        if path == target:
            continue
        tid = strm_torrent_id(path)
        if tid and tid != new_tid:
            old_tids.add(tid)
        path.unlink(missing_ok=True)

    for tid in old_tids:
        if not torrent_referenced_elsewhere(tid, {target}):
            torbox_delete_torrent(tid)

    radarr("/api/v3/command", "POST", {"name": "RescanMovie", "movieId": movie["id"]})
    log(f"{'upgraded' if old_tids else 'wrote'} {target.name} (quality {new_quality})")
    return True


def parse_episode(filename):
    match = re.search(r"[Ss](\d{1,2})[ ._-]*[Ee](\d{1,3})", filename)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def sync_series(series, torrents):
    grab = latest_series_grab(series["id"])
    if not grab:
        return False
    torrent = matching_torrent(torrents, grab)
    if not torrent or not torrent.get("cached") or not torrent.get("download_finished"):
        return False

    series_dir = host_series_path(series.get("path") or "")
    if series_dir is None:
        return False

    new_quality = (((grab.get("quality") or {}).get("quality") or {}).get("name")) or "Unknown"
    new_rank = quality_rank(new_quality)
    new_tid = str(torrent["id"])

    written = 0
    old_tids = set()
    removed = set()
    for file_item in video_files(torrent):
        source_name = file_item.get("short_name") or file_item.get("name") or ""
        parsed = parse_episode(source_name)
        if not parsed:
            continue
        season, episode = parsed
        season_dir = series_dir / f"Season {season:02d}"

        existing = []
        if season_dir.exists():
            pattern = re.compile(rf"S{season:02d}E{episode:02d}\b", re.IGNORECASE)
            existing = [p for p in season_dir.glob("*.strm") if pattern.search(p.name)]

        # Already streaming this exact torrent for the episode — skip.
        if any(strm_torrent_id(p) == new_tid for p in existing):
            continue

        # Existing episode that is equal/higher quality — skip (no downgrade/churn).
        if existing:
            cur_rank = max(quality_rank(quality_from_filename(p.name)) for p in existing)
            if new_rank <= cur_rank:
                continue

        season_dir.mkdir(parents=True, exist_ok=True)
        filename = safe_filename(
            f"{series['title']} - S{season:02d}E{episode:02d} - {new_quality}.strm"
        )
        target = season_dir / filename
        target.write_text(strm_url(torrent, file_item))
        written += 1

        for p in existing:
            if p == target:
                continue
            tid = strm_torrent_id(p)
            if tid and tid != new_tid:
                old_tids.add(tid)
            p.unlink(missing_ok=True)
            removed.add(p)

    if not written:
        return False

    # Only delete an old torrent if no remaining episode strm still points at it
    # (season packs are shared across many episodes).
    for tid in old_tids:
        if not torrent_referenced_elsewhere(tid, removed):
            torbox_delete_torrent(tid)

    sonarr("/api/v3/command", "POST", {"name": "RescanSeries", "seriesId": series["id"]})
    log(f"wrote/updated {written} episode strm file(s) for {series.get('title')}")
    return True


def sync_once():
    movies = radarr("/api/v3/movie") or []
    torrents = torbox_torrents()
    count = 0
    for movie in movies:
        try:
            if sync_movie(movie, torrents):
                count += 1
        except (urllib.error.URLError, KeyError, OSError) as error:
            log(f"failed syncing {movie.get('title', movie.get('id'))}: {error}")
    if SONARR_API_KEY:
        for series in sonarr("/api/v3/series") or []:
            try:
                if sync_series(series, torrents):
                    count += 1
            except (urllib.error.URLError, KeyError, OSError) as error:
                log(f"failed syncing {series.get('title', series.get('id'))}: {error}")
    if count:
        log(f"synced {count} item(s)")


def main():
    log("starting Radarr STRM sync")
    while True:
        try:
            sync_once()
        except Exception as error:
            log(f"sync loop failed: {error}")
        time.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    main()
