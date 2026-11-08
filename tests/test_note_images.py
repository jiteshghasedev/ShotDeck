"""A note's pictures (RV annotations) in Flow's task notes chat."""

import os
import tempfile

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

import fakes

app = None


def setup_module():
    global app
    app = QApplication.instance() or QApplication([])


def settle():
    for _ in range(3):
        app.processEvents()
        QThreadPool.globalInstance().waitForDone(4000)
    app.processEvents()


def test_an_annotation_note_shows_its_picture_in_the_task_chat():
    from ui import task_notes

    task_notes.IMAGE_CACHE = tempfile.mkdtemp()
    sg = fakes.FakeShotgun()
    png = os.path.join(tempfile.mkdtemp(), "drawn.png")
    pm = QPixmap(64, 36)
    pm.fill(QColor("#3d9dff"))
    pm.save(png)

    def download(attachment, file_path=None):
        sg.calls.append(("download", attachment["id"], file_path))
        with open(png, "rb") as src, open(file_path, "wb") as dst:
            dst.write(src.read())
        return file_path
    sg.download_attachment = download

    sg.create("Note", {
        "content": "Priya: Edge flickers here",
        "subject": "SH010_comp_v004 annotations by Priya, frame 1012",
        "tasks": [{"type": "Task", "id": fakes.TASK["id"]}],
        "attachments": [{"type": "Attachment", "id": 8,
                         "name": "SH010_comp_v004_annotation_f1012_priya.png"},
                        {"type": "Attachment", "id": 9, "name": "notes.txt"}],
        "user": fakes.PRODUCER})

    dialog = task_notes.TaskNotesDialog(fakes.client(sg), fakes.PROJECT,
                                        fakes.TASK)
    settle()
    pictures = dialog.findChildren(task_notes.Picture)
    assert [os.path.basename(p.path) for p in pictures] == \
        ["SH010_comp_v004_annotation_f1012_priya.png"], "pictures only"
    assert not pictures[0].pixmap().isNull()

    pictures[0].mouseReleaseEvent(None)          # a click: full screen
    viewer = pictures[0].viewer
    assert viewer.isFullScreen() and viewer.isVisible()
    assert "SH010_comp_v004_annotation_f1012_priya.png" in viewer.caption.text()
    assert not viewer.image.pixmap().isNull()
    viewer.close()

    dialog.set_filter(task_notes.ALL)            # a redraw: no second download
    settle()
    assert [c for c in sg.calls if c[0] == "download"] == [
        ("download", 8, pictures[0].path)]
