"""The review player's local copies of ShotGrid uploads.

file:// URLs stand in for ShotGrid's links: urllib serves them with a
Content-Length like S3 does, and nothing reaches the network.
"""

import os
import pathlib
import tempfile
import time

import config
import media_cache

TMP = tempfile.mkdtemp(prefix="flow-cache-")


def _cache_dir(name):
    config.MEDIA_CACHE_DIR = os.path.join(TMP, name)
    return config.MEDIA_CACHE_DIR


def _upload(name, size=4096):
    path = os.path.join(TMP, "s3", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(os.urandom(size))
    return path, pathlib.Path(path).as_uri()


def test_a_fetched_upload_is_played_from_disk_next_time():
    _cache_dir("hit")
    source, url = _upload("AD1008_Lighting_v004.mov")
    local = media_cache.fetch(1001, url)
    assert open(local, "rb").read() == open(source, "rb").read()
    os.remove(source)                       # gone from "ShotGrid"
    assert media_cache.cached(1001, url) == local
    assert media_cache.fetch(1001, url) == local, "a hit never downloads"
    assert not [n for n in os.listdir(config.MEDIA_CACHE_DIR)
                if n.endswith(".part")]


def test_the_expiring_signature_does_not_split_the_cache():
    _cache_dir("signature")
    _source, url = _upload("v002.mov")
    local = media_cache.fetch(7, url)
    assert media_cache.cached(7, url + "?X-Amz-Signature=new") == local


def test_a_reupload_is_a_new_entry():
    _cache_dir("reupload")
    _a, first = _upload("one/v003.mov")
    _b, second = _upload("two/v003.mov")
    assert media_cache.fetch(9, first) != media_cache.fetch(9, second)


def test_a_failed_download_leaves_nothing_behind():
    cache = _cache_dir("failed")
    missing = pathlib.Path(TMP, "s3", "missing.mov").as_uri()
    try:
        media_cache.fetch(5, missing)
    except OSError:
        pass
    else:
        raise AssertionError("a missing upload must fail")
    assert media_cache.cached(5, missing) == ""
    assert os.listdir(cache) == []


def test_pruning_drops_the_least_recently_played_first():
    cache = _cache_dir("prune")
    urls = [_upload(f"p{i}.mov", size=1000)[1] for i in range(3)]
    paths = [media_cache.fetch(i, u) for i, u in enumerate(urls)]
    now = time.time()
    for age, path in zip((300, 200, 100), paths):
        os.utime(path, (now - age, now - age))
    media_cache.cached(0, urls[0])          # played again: newest now
    media_cache.prune(limit_bytes=2000)
    left = sorted(os.listdir(cache))
    assert os.path.basename(paths[1]) not in left, "oldest use goes first"
    assert os.path.basename(paths[0]) in left
    assert os.path.basename(paths[2]) in left


def test_a_stale_part_file_is_cleared():
    cache = _cache_dir("stale")
    os.makedirs(cache, exist_ok=True)
    part = os.path.join(cache, "1_abc.mov.999.part")
    open(part, "wb").close()
    old = time.time() - media_cache.STALE_PART_SECONDS - 60
    os.utime(part, (old, old))
    media_cache.prune()
    assert not os.path.exists(part)


def test_the_inbox_prefetch_caches_newest_first_up_to_the_limit():
    _cache_dir("prefetch")
    rows = []
    for i in range(4):
        _source, url = _upload(f"inbox_v00{i}.mov")
        rows.append({"id": 300 + i, "code": f"v00{i}",
                     "sg_uploaded_movie": {"url": url}})
    local, _url = _upload("on_disk.mov")
    rows.insert(0, {"id": 399, "code": "local", "sg_path_to_movie": local,
                    "sg_uploaded_movie": {"url": _url}})
    rows.insert(1, {"id": 398, "code": "no upload"})

    assert media_cache.prefetch(rows, limit=4) == 2, \
        "the local file and the version with no upload are skipped"
    assert media_cache.cached(300, rows[2]["sg_uploaded_movie"]["url"])
    assert media_cache.cached(301, rows[3]["sg_uploaded_movie"]["url"])
    assert not media_cache.cached(302, rows[4]["sg_uploaded_movie"]["url"])
    assert media_cache.prefetch(rows, limit=4) == 0, "cached ones are not refetched"


def test_a_failed_prefetch_does_not_stop_the_rest():
    _cache_dir("prefetch_fail")
    missing = pathlib.Path(TMP, "s3", "gone.mov").as_uri()
    _source, url = _upload("still_here.mov")
    rows = [{"id": 1, "code": "gone", "sg_uploaded_movie": {"url": missing}},
            {"id": 2, "code": "here", "sg_uploaded_movie": {"url": url}}]
    assert media_cache.prefetch(rows, limit=5) == 1


def test_the_default_location_suits_each_platform():
    path = config._default_media_cache()
    assert path.endswith(os.path.join("Flow", "media_cache")) or \
        path.endswith(os.path.join("flow", "media"))
