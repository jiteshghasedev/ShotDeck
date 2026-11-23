"""Run one publish job: `nuke -t version_worker.py <job.json>`, or
`python3 version_worker.py <job.json>` for a job whose movie_tool is ffmpeg
(Silhouette has no Nuke to make the movie).

Started by version_publish.start() in a process of its own, so closing (or
losing) the artist's DCC does not stop it. See version_publish.py.
"""

import os
import subprocess
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow_dcc import shotgrid, version_publish  # noqa: E402

# Nuke's mov writer. h264 keeps the upload small; a Nuke without it falls
# back to the writer's default codec rather than failing the publish.
CODECS = ("h264", "avc1")


FFMPEG = os.environ.get("FLOW_FFMPEG", "/software/pipeline/bin/ffmpeg")


def ffmpeg_movie(job):
    """H.264 movie of the frames, as ingest makes its plate previews."""
    if os.path.isfile(job["movie"]):
        os.remove(job["movie"])         # a crash's half-written movie
    subprocess.run([FFMPEG, "-y", "-v", "error",
                    "-start_number", str(job["first"]), "-i", job["frames"],
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-pix_fmt", "yuv420p", job["movie"]],
                   check=True, timeout=3600)


def make_movie(job):
    if job.get("movie_tool") == "ffmpeg":
        return ffmpeg_movie(job)
    import nuke
    read = nuke.nodes.Read(file=job["frames"], first=job["first"],
                           last=job["last"])
    write = nuke.nodes.Write(inputs=[read], file=job["movie"])
    write["file_type"].setValue("mov")
    for name in ("mov64_codec", "meta_codec"):
        if name in write.knobs():
            for codec in CODECS:
                try:
                    write[name].setValue(codec)
                    break
                except Exception:
                    continue
    if os.path.isfile(job["movie"]):
        os.remove(job["movie"])         # a crash's half-written movie
    nuke.execute(write, job["first"], job["last"])


def main(path):
    job = version_publish.load(path)
    job["pid"] = os.getpid()
    version_publish.save(job)
    print("Flow: publishing {0}".format(job["name"]))
    version_publish.run(job, shotgrid.connect, make_movie,
                        version_publish.send_mail)
    print("Flow: published {0} as Version {1}".format(
        job["name"], job["version_id"]))


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Exception:
        traceback.print_exc()
        sys.exit(1)
