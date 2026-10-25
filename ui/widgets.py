import datetime
import urllib.request

import shiboken6

from PySide6.QtCore import (
    Qt, QSize, Signal, QThreadPool, QRunnable, QObject, QRectF, QTimer,
)
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QLinearGradient, QBrush, QFont, QPainterPath,
    QPalette,
)
from PySide6.QtWidgets import (
    QFrame, QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy, QWidget,
    QGraphicsDropShadowEffect, QStyledItemDelegate, QStyle,
)

import config

from . import theme

_pool = QThreadPool.globalInstance()

# Thumbnails are fetched once per URL and kept for the life of the process.
# Without this, every grid rebuild re-downloads from ShotGrid.
_thumb_cache = {}


class _ThumbSignals(QObject):
    done = Signal(str, bytes)


class _ThumbJob(QRunnable):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.signals = _ThumbSignals()

    def run(self):
        try:
            with urllib.request.urlopen(self.url, timeout=10) as r:
                self.signals.done.emit(self.url, r.read())
        except Exception:
            pass    # a missing thumbnail is not worth bothering anyone about


_thumb_jobs = set()          # see ui/jobs.py for why these are held


def _deliver(callback, data):
    """Hand the decoded pixmap to the callback, on the UI thread.

    A thumbnail arriving after its window closed is normal: the widget the
    callback writes to is already gone and Qt raises RuntimeError from the
    dead wrapper. Swallowing it here keeps a closed dialog from taking the
    process with it, and a thumbnail is never worth an error dialog.
    """
    pm = QPixmap()
    if not pm.loadFromData(data):
        return
    try:
        callback(pm)
    except RuntimeError:
        pass


def load_thumbnail(url, callback):
    """Call `callback(QPixmap)` with the image at `url`, cached per URL.

    Shared by the tiles and the Version browser so a thumbnail already seen in
    one is never fetched again for the other.
    """
    if not url or not config.THUMBNAILS_ENABLED:
        return
    data = _thumb_cache.get(url)
    if data is not None:
        _deliver(callback, data)
        return

    job = _ThumbJob(url)
    _thumb_jobs.add(job)

    def done(u, raw):
        _thumb_cache[u] = raw
        _thumb_jobs.discard(job)
        _deliver(callback, raw)

    job.signals.done.connect(done)
    _pool.start(job)


def flash_refreshed(button, seconds=6):
    """Say on a refresh button that the reload landed, then put it back.

    Shown even when nothing changed, so the click visibly did something.
    """
    if not hasattr(button, "_resting_text"):
        button._resting_text = button.text()
        button._flash = QTimer(button)
        button._flash.setSingleShot(True)
        button._flash.timeout.connect(
            lambda: button.setText(button._resting_text))
    button.setText("✓ Refreshed " + datetime.datetime.now().strftime("%H:%M"))
    button._flash.start(seconds * 1000)


def rounded(pixmap, size, radius):
    """Scale to fill, centre-crop, and round the corners."""
    scaled = pixmap.scaled(size, Qt.KeepAspectRatioByExpanding,
                           Qt.SmoothTransformation)
    x = max(0, (scaled.width() - size.width()) // 2)
    y = max(0, (scaled.height() - size.height()) // 2)
    scaled = scaled.copy(x, y, size.width(), size.height())

    out = QPixmap(size)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size.width(), size.height()),
                        radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return out


def placeholder(text, size, radius):
    """Gradient square with initials, for anything without a thumbnail."""
    start, end = theme.tile_colours(text)
    out = QPixmap(size)
    out.fill(Qt.transparent)

    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, size.width(), size.height())
    gradient.setColorAt(0, QColor(start))
    gradient.setColorAt(1, QColor(end))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size.width(), size.height()),
                        radius, radius)
    painter.fillPath(path, QBrush(gradient))

    initials = "".join(w[0] for w in str(text).split()[:2]).upper() or "?"
    font = QFont(painter.font())
    font.setPixelSize(int(size.height() * 0.34))
    font.setWeight(QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255, 200))
    painter.drawText(out.rect(), Qt.AlignCenter, initials)
    painter.end()
    return out


class Tile(QFrame):
    """Project / app card: thumbnail, name, and an optional second line."""

    clicked = Signal()

    def __init__(self, title, image_url=None, subtitle=None,
                 size=QSize(168, 104)):
        super().__init__()
        self.setObjectName("tile")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(size.width() + 20,
                          size.height() + (68 if subtitle else 52))

        self._title = title
        self._thumb_size = size

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self.thumb = QLabel()
        self.thumb.setObjectName("tileThumb")
        self.thumb.setFixedSize(size)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setPixmap(placeholder(title, size, theme.RADIUS_SM))
        lay.addWidget(self.thumb, alignment=Qt.AlignHCenter)

        name = QLabel(title)
        name.setObjectName("tileName")
        name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lay.addWidget(name)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("tileSub")
            lay.addWidget(sub)

        lay.addStretch()

        if config.TILE_SHADOWS_ENABLED:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(16)
            shadow.setOffset(0, 3)
            shadow.setColor(QColor(0, 0, 0, 90))
            self.setGraphicsEffect(shadow)

        if image_url:
            self._load(image_url)

    def _load(self, url):
        # Goes through load_thumbnail rather than starting a _ThumbJob here:
        # the pool owns the C++ runnable but nothing owned the Python wrapper,
        # so the wrapper — and the QObject carrying its signals — could be
        # collected while the download thread was still running, and the emit
        # then landed on freed memory. See ui/jobs.py for the same hazard.
        load_thumbnail(url, self._apply)

    def _apply(self, pixmap):
        if pixmap.isNull() or not shiboken6.isValid(self):
            return              # tile was destroyed while the fetch was in flight
        self.thumb.setPixmap(rounded(pixmap, self._thumb_size, theme.RADIUS_SM))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Avatar(QLabel):
    """Small circle with the user's initials."""

    def __init__(self, text, diameter=20):
        super().__init__()
        self.setObjectName("avatar")
        self.setFixedSize(diameter, diameter)
        self.setAlignment(Qt.AlignCenter)
        initials = (text or "?").split("@")[0][:2].upper()
        self.setText(initials)


class UserChip(QWidget):
    """The signed-in artist in the header: avatar, name, and a details menu.

    The chip answers "who am I signed in as, and how" without a preferences
    window — which account launched the DCC is the first thing to check when a
    task list comes back empty.
    """

    clicked = Signal()

    def __init__(self, name, avatar_text=None, parent=None):
        super().__init__(parent)
        self.setObjectName("userChip")
        self.setCursor(Qt.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 8, 0)
        lay.setSpacing(8)

        self.avatar = Avatar(avatar_text or name)
        lay.addWidget(self.avatar)

        self.name_lbl = QLabel(name)
        lay.addWidget(self.name_lbl)

        self.caret = QLabel("⌄")       # small chevron, "there is more here"
        self.caret.setObjectName("tileSub")
        lay.addWidget(self.caret)

    def set_name(self, name, avatar_text=None):
        self.name_lbl.setText(name)
        if avatar_text is not None:
            self.avatar.setText((avatar_text or "?").split("@")[0][:2].upper())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class EmptyState(QWidget):
    """Shown instead of a blank grid or table."""

    def __init__(self, icon, title, hint=""):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(6)

        icon_lbl = QLabel(icon)
        icon_lbl.setObjectName("emptyIcon")
        icon_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("emptyTitle")
        title_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(title_lbl)

        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setObjectName("emptyHint")
            hint_lbl.setAlignment(Qt.AlignCenter)
            hint_lbl.setWordWrap(True)
            lay.addWidget(hint_lbl)


class StatusPill(QStyledItemDelegate):
    """Draws a task status as a coloured pill instead of a bare short code.

    The cell shows its text. When Qt.UserRole holds the status code, the cell
    is showing a label ("In Progress") and the code picks the colour; without
    it the text is the code itself and is shown upper-case.
    """

    def paint(self, painter, option, index):
        shown = index.data(Qt.DisplayRole) or ""
        code = index.data(Qt.UserRole) or shown
        if not shown:
            return

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(61, 157, 255, 36))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, QColor(theme.SURFACE))

        colour = QColor(theme.status_colour(code))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        font = QFont(option.font)
        font.setPixelSize(11)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)

        text = shown if index.data(Qt.UserRole) else shown.upper()
        width = min(painter.fontMetrics().horizontalAdvance(text) + 20,
                    option.rect.width() - 16)
        height = 20
        rect = QRectF(option.rect.left() + 12,
                      option.rect.center().y() - height / 2 + 1,
                      width, height)

        bg = QColor(colour)
        bg.setAlpha(38)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, height / 2, height / 2)

        painter.setPen(colour)
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(90, 38)


class DueDate(QStyledItemDelegate):
    """Due dates, with overdue ones picked out in red.

    Reads the ISO date from Qt.UserRole when the cell shows friendlier text
    ("2 days late"), otherwise from the text itself.
    """

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        role = QPalette.ColorRole.Text
        raw = index.data(Qt.UserRole)
        if raw is None:
            raw = index.data(Qt.DisplayRole)
        raw = (raw or "").strip()
        if not raw:
            option.palette.setColor(role, QColor(theme.TEXT_FAINT))
            return
        try:
            due = datetime.date.fromisoformat(raw)
        except ValueError:
            return
        today = datetime.date.today()
        if due < today:
            option.palette.setColor(role, QColor(theme.ERROR))
        elif (due - today).days <= 2:
            option.palette.setColor(role, QColor(theme.WARN))


STYLE = theme.STYLE
