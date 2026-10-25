"""Play a Version, or compare two: the pixels, and the feedback on them.

Any two versions, not just adjacent ones. Four modes -- side by side, A/B,
wipe, and difference -- with difference offered only where it means something:
two frames of the same resolution. Movies are decoded into frames and painted
by the same view as stills, so a playing movie wipes, sits side by side and
differences exactly like a still does. B follows A's playhead rather than
being frame-locked to it; RV is one click away for a frame-accurate sign-off.

Media comes from whatever the Version has that this machine can read: the
local path first, ShotGrid's uploaded movie (streamed) second, the ShotGrid
thumbnail last. The notes panel sits on the right, full screen or not.
"""

import os
import time

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QSlider, QWidget, QButtonGroup, QMessageBox,
)

import applog
import config
import media_inspector
import rv_player
from . import jobs, theme
from .notes_panel import NotesPanel
from .widgets import load_thumbnail

log = applog.get()

try:
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink, QMediaMetaData
    HAVE_MULTIMEDIA = True
except ImportError:                                  # pragma: no cover
    HAVE_MULTIMEDIA = False

SIDE_BY_SIDE, AB, WIPE, DIFFERENCE = "side", "ab", "wipe", "difference"


def local_media(version):
    """The version's media on this machine, if it is reachable."""
    for field in ("sg_path_to_movie", "sg_path_to_frames"):
        path = version.get(field) or ""
        if path and os.path.isfile(path):
            return path
    return ""


def stream_url(version):
    """ShotGrid's copy of the uploaded movie: a presigned link that expires."""
    value = version.get(config.VERSION_MEDIA_FIELD)
    return (value.get("url") if isinstance(value, dict) else value) or ""


class VersionMedia:
    """One side of the comparison, and what it managed to load.

    Four sources, in order of how much they are worth: the full-res media on
    this machine, the movie uploaded to ShotGrid, the still ShotGrid holds for
    the version, and nothing. Most versions on a live site have no local path
    -- published by other tools, or on a mount this workstation does not have
    -- so the upload and the thumbnail are the difference between a working
    compare and two grey panes.
    """

    LOCAL, STREAM, THUMBNAIL, NONE = "local", "stream", "thumbnail", "none"

    def __init__(self, version):
        self.version = version
        self.path = local_media(version)
        # Anything local that is not a still goes to the player, which says so
        # itself if it cannot decode it.
        if self.path:
            self.movie = "" if config.media_kind(self.path) == "image" \
                else self.path
        else:
            self.movie = stream_url(version)
        self.pixmap = QPixmap()
        self.info = media_inspector.inspect(self.path) if self.path else None
        self.error = ""
        self.source = self.NONE
        self.decoded = False             # a movie frame has arrived
        self.relinked = False            # a fresh stream link was fetched

        if self.path and not self.movie:
            self.pixmap = QPixmap(self.path)
            if self.pixmap.isNull():
                self.error = (f"Qt has no reader for "
                              f"{os.path.splitext(self.path)[1]} files")
            else:
                self.source = self.LOCAL
        elif self.movie:
            self.source = self.LOCAL if self.path else self.STREAM
            self.error = "Loading the movie…"
        else:
            self.error = "Loading the ShotGrid thumbnail…" \
                if self.thumbnail_url else \
                "No media on this machine and no thumbnail in ShotGrid"

    @property
    def thumbnail_url(self):
        return self.version.get("image") or ""

    def use_thumbnail(self, pixmap):
        """Called when the ShotGrid still arrives. A movie keeps it only as a
        poster until its first frame, and keeps its own source label."""
        if pixmap.isNull():
            if not self.movie:
                self.error = "ShotGrid has no usable thumbnail for this version"
            return False
        self.pixmap = pixmap
        if not self.movie:
            self.source = self.THUMBNAIL
            self.error = ""
        return True

    @property
    def source_label(self):
        return {self.LOCAL: "full-resolution media",
                self.STREAM: "ShotGrid upload (streamed)",
                self.THUMBNAIL: "ShotGrid thumbnail",
                self.NONE: "no media"}[self.source]

    @property
    def code(self):
        return self.version.get("code") or f"Version {self.version['id']}"

    @property
    def resolution(self):
        if not self.pixmap.isNull() and (self.decoded or not self.movie):
            return f"{self.pixmap.width()} × {self.pixmap.height()}"
        if self.info and self.info.resolution:
            return self.info.resolution
        return ""

    @property
    def comparable_image(self):
        return not self.pixmap.isNull()


class ImageCompare(QWidget):
    """Both frames, in whichever mode is selected, sharing zoom and pan."""

    wipe_moved = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.a = self.b = None
        self.mode = SIDE_BY_SIDE
        self.zoom = 1.0
        self.fit = True
        self.wipe = 0.5
        self.showing_b = False
        self.setMinimumHeight(280)

    def set_media(self, a, b):
        self.a, self.b = a, b
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self.setCursor(Qt.SplitHCursor if mode == WIPE else
                       Qt.PointingHandCursor if mode == AB else Qt.ArrowCursor)
        self.update()

    def set_wipe(self, value):
        self.wipe = max(0.0, min(1.0, value))
        self.update()

    def toggle(self):
        self.showing_b = not self.showing_b
        self.update()

    def set_zoom(self, zoom):
        self.fit = False
        self.zoom = max(0.05, min(8.0, zoom))
        self.update()

    def fit_to_window(self):
        self.fit = True
        self.update()

    # -- mouse: drag the wipe, click to flip A/B, as in RV ------------------

    def mousePressEvent(self, event):
        if self.mode == AB:
            self.toggle()
        self._drag_wipe(event)

    def mouseMoveEvent(self, event):
        self._drag_wipe(event)

    def _drag_wipe(self, event):
        if self.mode == WIPE and self.width():
            self.set_wipe(event.position().x() / self.width())
            self.wipe_moved.emit(self.wipe)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.SURFACE_SUNK))
        if not self.a or not self.b:
            return

        if self.mode == SIDE_BY_SIDE:
            half = self.width() // 2
            self._draw(painter, self.a, self.rect().adjusted(0, 0, -half, 0))
            self._draw(painter, self.b, self.rect().adjusted(half, 0, 0, 0))
            painter.fillRect(half - 1, 0, 2, self.height(),
                             QColor(theme.BORDER))
            return

        if self.mode == AB:
            self._draw(painter, self.b if self.showing_b else self.a,
                       self.rect())
            return

        if self.mode == WIPE:
            split = int(self.width() * self.wipe)
            painter.save()
            painter.setClipRect(0, 0, split, self.height())
            self._draw(painter, self.a, self.rect())
            painter.restore()
            painter.save()
            painter.setClipRect(split, 0, self.width() - split, self.height())
            self._draw(painter, self.b, self.rect())
            painter.restore()
            painter.fillRect(split - 1, 0, 2, self.height(),
                             QColor(theme.ACCENT))
            return

        if self.mode == DIFFERENCE:
            diff = self._difference()
            if diff is None:
                return
            self._draw_pixmap(painter, diff, self.rect())

    def _draw(self, painter, media, rect):
        if media.comparable_image:
            self._draw_pixmap(painter, media.pixmap, rect)
            return
        painter.setPen(QColor(theme.TEXT_FAINT))
        painter.drawText(rect, Qt.AlignCenter,
                         media.error or "No preview")

    def _draw_pixmap(self, painter, pixmap, rect):
        if self.fit:
            scaled = pixmap.scaled(rect.size(), Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
        else:
            scaled = pixmap.scaled(pixmap.size() * self.zoom,
                                   Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
        # Same rectangle for both sides, so a zoom or a pan lines up.
        x = rect.x() + (rect.width() - scaled.width()) // 2
        y = rect.y() + (rect.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)

    def _difference(self):
        """abs(A - B), which only means anything at a shared resolution.

        Done with Qt's own Difference composition rather than a per-pixel
        loop: two 2K frames is four million pixels, and in Python that is a
        frozen window rather than a comparison.
        """
        if not (self.a.comparable_image and self.b.comparable_image):
            return None
        a = self.a.pixmap.toImage().convertToFormat(QImage.Format_RGB32)
        b = self.b.pixmap.toImage().convertToFormat(QImage.Format_RGB32)
        if a.size() != b.size():
            return None

        out = QImage(a)
        painter = QPainter(out)
        painter.setCompositionMode(QPainter.CompositionMode_Difference)
        painter.drawImage(0, 0, b)
        painter.end()
        return QPixmap.fromImage(out)

    def difference_available(self):
        """Why difference is or is not offered, in one place."""
        if not (self.a and self.b):
            return False, "Nothing loaded"
        if not (self.a.comparable_image and self.b.comparable_image):
            return False, "Difference needs two frames to compare"
        if self.a.pixmap.size() != self.b.pixmap.size():
            return False, ("Difference needs matching resolutions "
                           f"({self.a.resolution} vs {self.b.resolution})")
        return True, ""


class VersionCompare(QDialog):
    """The review player: one version, or two compared, and their notes.

    version_b None plays version_a on its own, with the compare modes hidden.
    """

    def __init__(self, sg, project, version_a, version_b=None, versions=None,
                 parent=None):
        super().__init__(parent)
        self.sg = sg
        self.project = project
        self.versions = versions or []
        self.media_a = self.media_b = None
        self.players = []
        self._jobs = set()
        self._frame_ms = 1000 / 24
        self._resynced = 0.0

        self.setStyleSheet(theme.STYLE)
        self.resize(1440, 860)

        root = QHBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(12)
        root.addLayout(left, 1)

        top = QHBoxLayout()
        self.heading = QLabel("")
        self.heading.setObjectName("headerTitle")
        top.addWidget(self.heading)
        top.addStretch()
        self.full_btn = self._button("Full screen", self.toggle_fullscreen)
        self.full_btn.setToolTip("F to toggle, Esc to leave")
        top.addWidget(self.full_btn)
        left.addLayout(top)

        self.header_host = QWidget()
        self.headers = QHBoxLayout(self.header_host)
        self.headers.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.header_host)

        self.image_view = ImageCompare()
        self.image_view.wipe_moved.connect(self._wipe_dragged)
        left.addWidget(self.image_view, 1)

        self.wipe_slider = QSlider(Qt.Horizontal)
        self.wipe_slider.setRange(0, 100)
        self.wipe_slider.setValue(50)
        self.wipe_slider.setFocusPolicy(Qt.NoFocus)
        self.wipe_slider.valueChanged.connect(
            lambda v: self.image_view.set_wipe(v / 100.0))
        self.wipe_slider.hide()
        left.addWidget(self.wipe_slider)

        left.addWidget(self._transport())
        left.addLayout(self._mode_bar())
        self.meta_host = self._metadata()
        left.addWidget(self.meta_host)

        root.addWidget(self._notes_column())

        self.set_versions(version_a, version_b)

    # -- construction ------------------------------------------------------

    def _button(self, text, slot, name="consoleBtn"):
        button = QPushButton(text)
        button.setObjectName(name)
        button.setCursor(Qt.PointingHandCursor)
        # Space and the arrows belong to the player, not to the last button
        # clicked.
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(slot)
        return button

    def _transport(self):
        self.transport = QWidget()
        bar = QHBoxLayout(self.transport)
        bar.setContentsMargins(0, 0, 0, 0)
        back = self._button("◀︎ Frame", lambda: self._step(-1))
        back.setToolTip("Previous frame (←)")
        bar.addWidget(back)
        self.play_btn = self._button("Play", self._toggle_play, "termBtn")
        self.play_btn.setToolTip("Play / pause (Space)")
        bar.addWidget(self.play_btn)
        forward = self._button("Frame ▶︎", lambda: self._step(1))
        forward.setToolTip("Next frame (→)")
        bar.addWidget(forward)

        self.scrub = QSlider(Qt.Horizontal)
        self.scrub.setFocusPolicy(Qt.NoFocus)
        self.scrub.sliderMoved.connect(self._seek)
        bar.addWidget(self.scrub, 1)

        self.time_label = QLabel("")
        self.time_label.setObjectName("tileSub")
        bar.addWidget(self.time_label)
        return self.transport

    def _mode_bar(self):
        bar = QHBoxLayout()
        bar.addStretch()
        self.mode_buttons = QButtonGroup(self)
        for mode, label in ((SIDE_BY_SIDE, "Side by Side"), (AB, "A/B"),
                            (WIPE, "Wipe"), (DIFFERENCE, "Difference")):
            button = self._button(label,
                                  lambda _=False, m=mode: self.set_mode(m),
                                  "termBtn")
            button.setCheckable(True)
            self.mode_buttons.addButton(button)
            button.setProperty("mode", mode)
            bar.addWidget(button)
        self.mode_buttons.buttons()[0].setChecked(True)

        bar.addSpacing(16)
        self.fit_btn = self._button("Fit", self.image_view.fit_to_window)
        bar.addWidget(self.fit_btn)
        self.actual_btn = self._button(
            "Actual size", lambda: self.image_view.set_zoom(1.0))
        bar.addWidget(self.actual_btn)

        # RV plays the actual render, colour managed and frame-locked, in the
        # same mode as here.
        bar.addSpacing(16)
        self.rv_btn = self._button("Open in RV", self._open_in_rv)
        bar.addWidget(self.rv_btn)              # enabled in set_versions
        bar.addStretch()
        return bar

    def _open_in_rv(self):
        """Send the versions to RV in whatever mode is selected here.

        The mode constants are RV's compare flags in rv_player.MODES, so the
        wipe stays a wipe and the difference stays a difference -- switching
        player should not switch what is being looked at.
        """
        self._pause()
        try:
            pid, log_path = rv_player.open_versions(
                [v for v, _m in self._sides()],
                mode="" if self.single else self.image_view.mode,
                project=self.project)
        except Exception as e:
            log.error("could not open RV: %s", e)
            QMessageBox.warning(self, "Open in RV", str(e))
            return
        log.info("RV pid %s, log %s", pid, log_path)

    def _metadata(self):
        host = QFrame()
        host.setObjectName("tile")
        wrap = QVBoxLayout(host)
        wrap.setContentsMargins(12, 8, 12, 8)
        self.meta_grid = QGridLayout()
        self.meta_grid.setHorizontalSpacing(14)
        self.meta_grid.setVerticalSpacing(3)
        wrap.addLayout(self.meta_grid)
        return host

    def _notes_column(self):
        host = QFrame()
        host.setObjectName("tile")
        host.setFixedWidth(400)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(12, 8, 12, 8)

        # Which version the panel is showing: in a compare that is the
        # difference between feedback on the old cut and on the new one.
        self.notes_switch = QWidget()
        switch = QHBoxLayout(self.notes_switch)
        switch.setContentsMargins(0, 0, 0, 0)
        self.notes_buttons = []
        for side in (0, 1):
            button = self._button("", lambda _=False, s=side: self._show_notes(s),
                                  "termBtn")
            button.setCheckable(True)
            switch.addWidget(button)
            self.notes_buttons.append(button)
        lay.addWidget(self.notes_switch)

        self.notes_panel = NotesPanel(self.sg, self.project, None)
        lay.addWidget(self.notes_panel, 1)
        return host

    # -- state -------------------------------------------------------------

    def _sides(self):
        """[(version, media), ...]: one entry when playing, two comparing."""
        sides = [(self.version_a, self.media_a)]
        if not self.single:
            sides.append((self.version_b, self.media_b))
        return sides

    def set_versions(self, version_a, version_b=None):
        self._stop()
        self.single = version_b is None
        self.version_a, self.version_b = version_a, version_b or version_a
        self.media_a = VersionMedia(version_a)
        self.media_b = self.media_a if self.single else VersionMedia(version_b)
        self.image_view.set_media(self.media_a, self.media_b)

        title = "Version Player" if self.single else "Version Compare"
        self.setWindowTitle(title)
        self.heading.setText(title)

        # Logged because "compare shows nothing" is almost always one of these
        # lines saying "none" — the Terminal panel answers it without a
        # debugging session.
        for _version, media in self._sides():
            log.info("player %s (%s: %s)", media.code, media.source,
                     media.movie or media.path or media.thumbnail_url
                     or "nothing")

        for button in self.mode_buttons.buttons():
            button.setVisible(not self.single)

        # Offered only when RV would have every side: one real render against
        # a missing mount is not a comparison.
        playable = all(rv_player.playable(v) for v, _m in self._sides())
        self.rv_btn.setEnabled(playable)
        self.rv_btn.setToolTip(
            "Play the full-resolution media in OpenRV, in the mode selected "
            "here" if playable else
            "This machine cannot read the media for RV")

        self.notes_switch.setVisible(not self.single)
        for button, media in zip(self.notes_buttons,
                                 (self.media_a, self.media_b)):
            button.setText(f"Notes · {media.code}")

        self._build_headers()
        self._build_metadata()
        self._load_thumbnails()
        self._start_movies()
        self._show_notes(0)
        self.set_mode(AB if self.single else WIPE)

    def _show_notes(self, side):
        version = self.version_b if side else self.version_a
        self.notes_panel.task = version.get(config.VERSION_TASK_FIELD)
        self.notes_panel.set_version(version)
        for i, button in enumerate(self.notes_buttons):
            button.setChecked(i == side)

    def _load_thumbnails(self):
        """Fill any side with nothing to show yet from ShotGrid's still.

        Cached per URL by `widgets.load_thumbnail`, so a version already seen
        in the browser costs nothing here. A movie uses it as a poster.
        """
        for _version, media in self._sides():
            if not media.pixmap.isNull() or not media.thumbnail_url:
                continue

            def arrived(pixmap, target=media):
                if target.decoded:
                    return                # the movie got there first
                if target.use_thumbnail(pixmap):
                    self.image_view.update()
                    self._build_metadata()
                    self._build_headers()

            load_thumbnail(media.thumbnail_url, arrived)

    def set_mode(self, mode):
        if mode == DIFFERENCE:
            ok, why = self.image_view.difference_available()
            if not ok:
                # Refused rather than shown as black: a meaningless difference
                # image is worse than saying why there isn't one.
                QMessageBox.information(self, "Difference", why)
                self._check_button(self.image_view.mode)
                return
        self.image_view.set_mode(mode)
        self.wipe_slider.setVisible(mode == WIPE)
        self._check_button(mode)

    def _wipe_dragged(self, value):
        self.wipe_slider.blockSignals(True)
        self.wipe_slider.setValue(round(value * 100))
        self.wipe_slider.blockSignals(False)

    def _check_button(self, mode):
        for button in self.mode_buttons.buttons():
            button.setChecked(button.property("mode") == mode)

    def toggle_fullscreen(self):
        full = not self.isFullScreen()
        # Full screen is for looking: the picture and the notes, no forms.
        self.header_host.setVisible(not full)
        self.meta_host.setVisible(not full)
        self.full_btn.setText("Exit full screen" if full else "Full screen")
        if full:
            self.showFullScreen()
        else:
            self.showNormal()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Space and self.players:
            self._toggle_play()
        elif key in (Qt.Key_Left, Qt.Key_Right) and self.players:
            self._step(-1 if key == Qt.Key_Left else 1)
        elif key == Qt.Key_F:
            self.toggle_fullscreen()
        elif key == Qt.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()      # leave full screen, not the dialog
        else:
            super().keyPressEvent(event)

    def _build_headers(self):
        while self.headers.count():
            item = self.headers.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for label, (version, media) in zip(("Version A", "Version B"),
                                           self._sides()):
            card = QFrame()
            card.setObjectName("tile")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 8, 12, 8)
            lay.setSpacing(2)
            if not self.single:
                side = QLabel(label)
                side.setObjectName("tileSub")
                lay.addWidget(side)
            name = QLabel(media.code)
            name.setObjectName("tileName")
            lay.addWidget(name)
            who = QLabel(f"{_person(version)}   ·   "
                         f"{_when(version.get('created_at'))}")
            who.setObjectName("tileSub")
            lay.addWidget(who)

            # Say which source is on screen: comparing two ShotGrid thumbnails
            # is useful, but it is not the same as comparing the renders and
            # nobody should have to guess which.
            source = QLabel(f"Showing {media.source_label}")
            source.setObjectName(
                "tileSub" if media.source in (media.LOCAL, media.STREAM)
                else "checkWarn")
            lay.addWidget(source)
            self.headers.addWidget(card)

    def _build_metadata(self):
        while self.meta_grid.count():
            item = self.meta_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        rows = [
            ("Version", lambda v, m: m.code),
            ("User", lambda v, m: _person(v)),
            ("Created", lambda v, m: _when(v.get("created_at"))),
            ("Status", lambda v, m: v.get("sg_status_list") or ""),
            ("Department", lambda v, m: _name(v.get("sg_task.Task.step"))),
            ("Description", lambda v, m: v.get("description") or ""),
            ("Resolution", lambda v, m: m.resolution),
            ("Frame range", lambda v, m: v.get("frame_range") or ""),
            ("Duration", lambda v, m: _duration(m)),
        ]

        for r, (key, value) in enumerate(rows):
            k = QLabel(key)
            k.setObjectName("tileSub")
            self.meta_grid.addWidget(k, r, 0, Qt.AlignRight | Qt.AlignTop)
            values = [str(value(v, m) or "") for v, m in self._sides()]
            # A difference in the values is the whole point of the row, so it
            # is coloured rather than left for the eye to find.
            differs = all(values) and len(set(values)) > 1
            for c, text in enumerate(values, start=1):
                label = QLabel(text or "—")
                label.setWordWrap(True)
                label.setObjectName("checkWarn" if differs else "tileName")
                self.meta_grid.addWidget(label, r, c)
                self.meta_grid.setColumnStretch(c, 1)

    # -- movies ------------------------------------------------------------

    def _start_movies(self):
        """One player per movie, each decoding into frames for the view.

        players[0] is the master: the scrub bar and the time follow it, and
        the other player is pulled back to it when it drifts.
        """
        movies = [m for _v, m in self._sides() if m.movie]
        self.transport.setVisible(bool(movies) and HAVE_MULTIMEDIA)
        if not HAVE_MULTIMEDIA:
            for media in movies:
                media.error = ("Movie playback needs Qt Multimedia, which is "
                               "not available here.")
            return

        fps = next((m.info.fps for m in movies if m.info and m.info.fps), 24)
        self._frame_ms = 1000 / fps
        for media in movies:
            player = QMediaPlayer(self)
            sink = QVideoSink(player)
            player.setVideoSink(sink)
            player.setLoops(QMediaPlayer.Loops.Infinite)
            sink.videoFrameChanged.connect(
                lambda frame, m=media: self._frame(m, frame))
            player.errorOccurred.connect(
                lambda _e, text, m=media, p=player: self._movie_error(m, p, text))
            source = QUrl.fromLocalFile(media.movie) \
                if os.path.isfile(media.movie) else QUrl(media.movie)
            player.setSource(source)
            self.players.append(player)

        if self.players:
            master = self.players[0]
            master.positionChanged.connect(self._sync)
            master.durationChanged.connect(self._duration_known)
            master.playbackStateChanged.connect(
                lambda state: self.play_btn.setText(
                    "Pause" if state == QMediaPlayer.PlayingState else "Play"))
            self._play()

    def _frame(self, media, frame):
        image = frame.toImage()
        if image.isNull():
            return
        media.pixmap = QPixmap.fromImage(image)
        media.error = ""
        if not media.decoded:
            media.decoded = True
            self._build_metadata()        # the resolution is known now
        self.image_view.update()

    def _movie_error(self, media, player, text):
        # ShotGrid's links expire after 15 minutes, so a list loaded earlier
        # gets one fresh link before the error is believed.
        if media.source == media.STREAM and not media.relinked:
            media.relinked = True

            def relink():
                fresh = self.sg.version(media.version["id"],
                                        [config.VERSION_MEDIA_FIELD])
                return stream_url(fresh or {})

            def retry(url):
                if player not in self.players:
                    return                    # closed or switched meanwhile
                if not url:
                    self._movie_error(media, player, text)
                    return
                media.movie = url
                player.setSource(QUrl(url))
                player.play()

            jobs.run(self._jobs, relink, retry,
                     on_error=lambda _m: self._movie_error(media, player, text))
            return
        log.warning("could not play %s: %s", media.code, text)
        media.error = f"Could not play this movie: {text}"
        self.image_view.update()

    def _duration_known(self, duration):
        self.scrub.setRange(0, duration)
        rate = self.players[0].metaData().value(QMediaMetaData.VideoFrameRate)
        if rate:
            self._frame_ms = 1000 / float(rate)
        self._sync(self.players[0].position())

    def _sync(self, position):
        if not self.scrub.isSliderDown():
            self.scrub.blockSignals(True)
            self.scrub.setValue(position)
            self.scrub.blockSignals(False)
        frame = int(position / self._frame_ms) + 1
        total = int(self.scrub.maximum() / self._frame_ms)
        self.time_label.setText(f"{frame} / {total}" if total else f"{frame}")
        # shortcut: B is pulled back to A when they drift over two frames, at
        # most once a second, not frame-locked; RV is the frame-accurate check
        # if a sign-off needs it.
        if time.monotonic() - self._resynced < 1:
            return
        for follower in self.players[1:]:
            # A seek before the movie has loaded restarts the load, so a slow
            # stream would never show a frame.
            if follower.mediaStatus() not in (
                    QMediaPlayer.MediaStatus.LoadedMedia,
                    QMediaPlayer.MediaStatus.BufferedMedia):
                continue
            if abs(follower.position() - position) > 2 * self._frame_ms:
                self._resynced = time.monotonic()
                follower.setPosition(position)

    def _toggle_play(self):
        if self.players and \
                self.players[0].playbackState() == QMediaPlayer.PlayingState:
            self._pause()
        else:
            self._play()

    def _play(self):
        for player in self.players:
            player.play()

    def _pause(self):
        for player in self.players:
            player.pause()

    def _seek(self, position):
        for player in self.players:
            player.setPosition(position)

    def _step(self, frames):
        if not self.players:
            return
        self._pause()
        position = self.players[0].position() + frames * self._frame_ms
        self._seek(max(0, int(position)))

    def _stop(self):
        for player in self.players:
            player.stop()
            player.deleteLater()
        self.players = []

    def done(self, result):
        self._stop()                     # Esc and Close both end up here
        super().done(result)

    def closeEvent(self, event):
        self._stop()
        super().closeEvent(event)


# -- helpers ----------------------------------------------------------------

def _name(entity):
    if not entity:
        return ""
    return entity.get("name") or entity.get("code") or ""


def _person(version):
    return _name(version.get("user") or version.get("created_by"))


def _when(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return f"{value:%d %b %Y %H:%M}"


def _duration(media):
    info = media.info
    if not info:
        return ""
    if info.frames and info.fps:
        return f"{info.frames} frames  ·  {info.frames / info.fps:.1f}s"
    if info.duration:
        return f"{info.duration:.1f}s"
    return ""
