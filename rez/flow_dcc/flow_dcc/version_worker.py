"""Run one publish job: `nuke -t version_worker.py <job.json>`.

Started by version_publish.start() in a process of its own, so closing (or
losing) the artist's Nuke does not stop it. See version_publish.py.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow_dcc import shotgrid, version_publish  # noqa: E402

# Nuke's mov writer. h264 keeps the upload small; a Nuke without it falls
# back to the writer's default codec rather than failing the publish.
CODECS = ("h264", "avc1")


def make_movie(job):
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
