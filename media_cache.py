"""Local copies of the movies uploaded to ShotGrid, for the review player.

Streaming from ShotGrid's S3 bucket stutters, and every loop and every seek
fetches the movie again. So the first play streams while a copy downloads,
and every play after that reads the local file. Entries are keyed by version
and upload, so a re-uploaded movie is a new entry rather than a stale one.

Linux and Windows alike: the directory is config.MEDIA_CACHE_DIR, a download
is written under a temporary name and renamed into place, so a half-written
file is never played, and a file some player still has open is skipped by
the pruning rather than deleted under it.
"""

import hashlib
import os
import shutil
import time
import urllib.parse
import urllib.request
import uuid

import applog
import config

log = applog.get()

CHUNK = 1 << 20
# A .part file this old belongs to a Flow that died mid-download.
STALE_PART_SECONDS = 24 * 3600


def upload_url(version):
    """ShotGrid's copy of the uploaded movie: a presigned link that expires."""
    value = version.get(config.VERSION_MEDIA_FIELD)
    return (value.get("url") if isinstance(value, dict) else value) or ""


def path_for(version_id, url):
    """Where this upload lives in the cache.

    The URL's path identifies the upload; its query is the signature, which
    changes every time ShotGrid hands the link out, so it is left out.
    """
    path = urllib.parse.urlsplit(url).path
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
    ext = os.path.splitext(path)[1].lower() or ".mov"
    return os.path.join(config.MEDIA_CACHE_DIR, f"{version_id}_{digest}{ext}")


def cached(version_id, url):
    """The local copy of this upload, or "". A hit counts as a use."""
    if not url:
        return ""
    path = path_for(version_id, url)
    if not os.path.isfile(path):
        return ""
    try:
        os.utime(path)                 # most recently used, for prune()
    except OSError:
        pass
    return path


def fetch(version_id, url, timeout=30):
    """Download the upload if it is not cached yet; return the local path.

    Blocking: run it on the pool.
    """
    path = cached(version_id, url)
    if path:
        return path
    path = path_for(version_id, url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Unique per download: the player and the inbox prefetch can fetch the
    # same movie at once in one Flow.
    part = f"{path}.{uuid.uuid4().hex[:12]}.part"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, \
                open(part, "wb") as out:
            expected = int(response.headers.get("Content-Length") or 0)
            shutil.copyfileobj(response, out, CHUNK)
            got = out.tell()
        # A connection that closes early is not an error to urllib, and a
        # short file cached once would be a broken version forever.
        if expected and got != expected:
            raise OSError(f"download cut short: {got} of {expected} bytes")
        try:
            os.replace(part, path)
        except PermissionError:
            # Windows will not replace a file that is open: another Flow
            # cached it first and is playing it, which is just as good.
            if not os.path.isfile(path):
                raise
    finally:
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
    log.info("cached %s (%s)", os.path.basename(path), _size(path))
    prune()
    return path


def prefetch(versions, limit):
    """Cache the uploads of up to `limit` of these versions, in order.

    For the review inbox, so the movie somebody is waiting on opens from disk
    the first time. Blocking: run it on the pool. One failure does not stop
    the rest. Returns how many were downloaded.
    """
    fetched = 0
    for version in versions[:limit]:
        url = upload_url(version)
        if not url or cached(version.get("id"), url) or any(
                os.path.isfile(version.get(f) or "")
                for f in ("sg_path_to_movie", "sg_path_to_frames")):
            continue                     # nothing to fetch, or already here
        try:
            fetch(version["id"], url)
            fetched += 1
        except Exception as e:
            log.info("prefetch of %s skipped: %s", version.get("code"), e)
    return fetched


def prune(limit_bytes=None):
    """Delete the least recently used copies until the cache fits the cap."""
    if limit_bytes is None:
        limit_bytes = config.MEDIA_CACHE_GB * 1024 ** 3
    try:
        entries = list(os.scandir(config.MEDIA_CACHE_DIR))
    except OSError:
        return
    now = time.time()
    files = []
    for entry in entries:
        try:
            info = entry.stat()
        except OSError:
            continue
        if entry.name.endswith(".part"):
            if now - info.st_mtime > STALE_PART_SECONDS:
                _remove(entry.path)
            continue
        if entry.is_file():
            files.append((info.st_mtime, info.st_size, entry.path))

    total = sum(size for _when, size, _path in files)
    for _when, size, path in sorted(files):
        if total <= limit_bytes:
            break
        if _remove(path):
            total -= size


def _remove(path):
    try:
        os.remove(path)
        return True
    except OSError:                    # open in a player, on Windows
        return False


def _size(path):
    try:
        return f"{os.path.getsize(path) / 1024 ** 2:.1f} MB"
    except OSError:
        return "?"
