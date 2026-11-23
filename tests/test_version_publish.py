"""flow_dcc.version_publish: Nuke's background publish, step by step."""

import os
import sys
import tempfile

import config

sys.path.insert(0, config.DCC_SOURCE_ROOT)

from flow_dcc import version_publish  # noqa: E402


class _SG:
    def __init__(self, fail_upload=1):
        self.created, self.uploads = [], []
        self.fail_upload = fail_upload

    def create(self, entity, data):
        self.created.append((entity, data))
        return {"id": 500 + len(self.created)}

    def upload(self, entity, entity_id, path, field):
        if self.fail_upload:
            self.fail_upload -= 1
            raise IOError("network down")
        self.uploads.append((entity, entity_id, path, field))

    def find_one(self, entity, filters, fields):
        if entity == "HumanUser":
            return {"id": 7}
        return {"task_reviewers": [{"type": "HumanUser", "id": 9}]}

    def find(self, entity, filters, fields):
        return [{"id": 9, "email": "lead@5and8.ai"}]


def _job(department="comp"):
    class Ctx:
        project_id, entity_type, entity_id, task_id = 1, "Shot", 2, 3
        site, artist = "https://x.shotgrid.autodesk.com", "nanda"
    Ctx.department = department
    os.environ["FLOW_USER_EMAIL"] = "nanda@5and8.ai"
    return version_publish.entry(
        "MV_001_nanda_comp_v0003",
        "/r/MV_001_nanda_comp_v0003/for_approval/MV_001_nanda_comp_v0003/"
        "MV_001_nanda_comp_v0003.%04d.exr", 1001, 1010, Ctx())


def test_a_failed_upload_resumes_without_a_second_version():
    version_publish.QUEUE_DIR = tempfile.mkdtemp()
    job = _job()
    assert job["movie"] == "/r/MV_001_nanda_comp_v0003/for_approval/" \
        "MV_001_nanda_comp_v0003.mov"
    sg, movies, mails = _SG(), [], []
    version_publish.save(job)
    try:
        version_publish.run(job, lambda: sg, movies.append,
                            lambda *a: mails.append(a))
        assert False, "the upload should have failed"
    except IOError:
        pass
    stuck = version_publish.pending()
    assert len(stuck) == 1 and stuck[0]["done"] == ["movie", "version"]
    assert stuck[0]["error"].startswith("upload:")
    assert version_publish.stalled(stuck[0])        # error: resume it

    version_publish.run(stuck[0], lambda: sg, movies.append,
                        lambda *a: mails.append(a))
    assert len(movies) == 1                         # not made twice
    assert [e for e, _ in sg.created] == ["Version"]
    version = sg.created[0][1]
    assert version["code"] == "MV_001_nanda_comp_v0003"
    assert version["sg_status_list"] == "rev"
    assert version["user"] == {"type": "HumanUser", "id": 7}
    assert sg.uploads == [("Version", 501, job["movie"],
                           "sg_uploaded_movie")]
    to, subject, html = mails[0]
    assert to == ["nanda@5and8.ai"] and "v0003" in subject
    assert "/detail/Version/501" in html
    assert version_publish.pending() == []


def test_roto_publishes_also_mail_the_reviewers():
    version_publish.QUEUE_DIR = tempfile.mkdtemp()
    job = _job("roto")
    sg, mails = _SG(fail_upload=0), []
    version_publish.save(job)
    version_publish.run(job, lambda: sg, lambda j: None,
                        lambda *a: mails.append(a))
    assert mails[0][0] == ["nanda@5and8.ai", "lead@5and8.ai"]


def test_a_frames_only_version_shows_its_first_frame():
    from ui import version_compare
    folder = tempfile.mkdtemp().replace("\\", "/")
    for f in (1003, 1001, 1002):
        open(os.path.join(folder, "an.{0}.png".format(f)), "w").close()
    open(os.path.join(folder, "an.mov"), "w").close()
    first = folder + "/an.1001.png"
    assert version_compare.first_frame(folder + "/an.%04d.png") == first
    assert version_compare.first_frame(folder + "/an.####.png") == first
    assert version_compare.first_frame(folder + "/zz.%04d.png") == ""
    assert version_compare.first_frame("/missing/an.%04d.exr") == ""
    assert version_compare.still(first) == first    # Qt reads it as it is


def test_a_mail_that_cannot_go_does_not_hold_the_publish():
    version_publish.QUEUE_DIR = tempfile.mkdtemp()
    job = _job()
    sg = _SG(fail_upload=0)
    version_publish.save(job)

    def no_route(*a):
        raise OSError("No route to host")
    done = version_publish.run(job, lambda: sg, lambda j: None, no_route)
    assert done["done"] == ["movie", "version", "upload", "mail"]
    assert "No route" in done["mail_error"]
    assert version_publish.pending() == []
