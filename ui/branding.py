"""The 5 AND 8 logo, recoloured for the dark UI, and the pulse it animates with.

The artwork in media/ is black on opaque white, which is unusable as-is on a
the dark UI background. Rather than keep a second exported asset in step with the
first, the pixmap is tinted at runtime: the greyscale of the file becomes the
alpha channel, so the white ground drops out and the mark takes whatever
colour it is asked for.

One widget covers both places a logo animates -- the splash and the wait while
tasks arrive -- so the two never drift apart.
"""

import math
import os
import random
from functools import lru_cache

from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QSize, QElapsedTimer, QRectF,
)
from PySide6.QtGui import (
    QImage, QPixmap, QPainter, QColor, QLinearGradient, QRadialGradient,
    QBrush, QFont,
)
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QApplication

import config

from . import theme

LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "media", "5and8_logo.png")


@lru_cache(maxsize=16)
def logo_pixmap(height, colour=theme.TEXT):
    """The mark at `height` px, tinted `colour`, transparent everywhere else.

    Cached: the splash and every loading state ask for the same few sizes, and
    the source file is a 2000px PNG that is not worth decoding twice.
    """
    img = QImage(LOGO_PATH)
    if img.isNull():                      # asset missing: callers get nothing
        return QPixmap()                  # rather than a crash on a farm box

    grey = img.convertToFormat(QImage.Format_Grayscale8)
    grey.invertPixels()                   # black artwork becomes white on black

    out = QImage(img.size(), QImage.Format_ARGB32)
    out.fill(QColor(colour))
    out.setAlphaChannel(grey)             # ink opaque, white ground gone
    return QPixmap.fromImage(out).scaledToHeight(height, Qt.SmoothTransformation)


class LogoPulse(QWidget):
    """The mark breathing in and out: opacity and scale on one sine.

    A spinner says "busy"; this says "busy" in the studio's own mark, which is
    the whole point of putting it on the splash. The timer only runs while the
    widget is visible, so a hidden loading page costs nothing.
    """

    INTERVAL_MS = 40                      # 25fps, plenty for a slow breath

    def __init__(self, height=72, colour=theme.TEXT, parent=None):
        super().__init__(parent)
        self._pixmap = logo_pixmap(height, colour)
        self._phase = 0.0
        self.setMinimumSize(QSize(self._pixmap.width() or height, height))

        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        self._phase += 0.09               # ~2.9s per breath
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        if self._pixmap.isNull():
            return
        wave = (math.sin(self._phase) + 1) / 2        # 0..1
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(0.40 + 0.60 * wave)
        scale = 0.92 + 0.08 * wave
        w = self._pixmap.width() * scale
        h = self._pixmap.height() * scale
        p.drawPixmap(int((self.width() - w) / 2), int((self.height() - h) / 2),
                     int(w), int(h), self._pixmap)


class LoadingPage(QWidget):
    """The pulsing mark with a line of text, for a QStackedWidget page."""

    def __init__(self, message="Loading...", height=64, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.addStretch()

        self.logo = LogoPulse(height)
        lay.addWidget(self.logo, alignment=Qt.AlignHCenter)

        self.label = QLabel(message)
        self.label.setObjectName("emptyHint")
        lay.addWidget(self.label, alignment=Qt.AlignHCenter)
        lay.addStretch()

    def set_message(self, text):
        self.label.setText(text)


def _ease_out(t):
    """Cubic ease-out on 0..1. Motion that decelerates reads as deliberate."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _fade_in(elapsed, start, length):
    """0..1 over [start, start+length] ms, clamped either side."""
    if length <= 0:                   # a stage with no duration is instant
        return 1.0 if elapsed >= start else 0.0
    return _ease_out((elapsed - start) / length) if elapsed > start else 0.0


class Splash(QWidget):
    """The window-sized opening sequence, painted on one elapsed-ms clock.

    Not QSplashScreen: that paints a fixed pixmap. Everything here is drawn in
    paintEvent against QElapsedTimer, so the stages are read straight off the
    clock rather than juggled between five QPropertyAnimations.

    Stages, in ms:
        0-800     ground fades up, mark starts to register
        800-2500  mark eases to full size, grid and particles arrive, the scan
                  line starts running
        2500+     status line fades in under the wordmark
        MIN_MS    earliest the sequence will hand over, even if the window was
                  ready long before -- a splash that flickers past is worse
                  than no splash

    finish() marks the app ready; the fade-out waits for whichever of the two
    is later, then shows the window.
    """

    MIN_MS = config.SPLASH_MS        # the whole sequence; stages divide it up
    FADE_MS = 450
    INTERVAL_MS = 33                 # ~30fps

    # Stages as fractions of MIN_MS, so retiming is one number in config.py and
    # the beats keep their proportions instead of drifting apart.
    GROUND_AT = 0.20                 # ground fade done, mark starts easing up
    MARK_AT = 0.63                   # mark at full size
    STATUS_AT = 0.63                 # status line starts fading in

    GRID = 46                        # px between grid lines
    PARTICLES = 34

    def __init__(self, message="Loading Pipeline"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.SplashScreen
                         | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._message = message
        self._ready = False
        self._closing = False

        # Instance copies: the tests and FLOW_SPLASH_MS both retime a
        # single splash without touching the class.
        self.GROUND_MS = int(self.MIN_MS * self.GROUND_AT)
        self.MARK_MS = int(self.MIN_MS * self.MARK_AT)
        self.STATUS_MS = int(self.MIN_MS * self.STATUS_AT)

        # Roughly the main window's footprint (MainWindow opens 1080x720), so
        # the UI appears where the splash was rather than somewhere else.
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        w, h = 1080, 720
        if avail is not None:
            w = min(w, int(avail.width() * 0.85))
            h = min(h, int(avail.height() * 0.85))
        self.setFixedSize(w, h)
        if avail is not None:
            self.move(avail.center() - self.rect().center())

        self._logo = logo_pixmap(int(h * 0.34))

        # Seeded, so the drift is identical every launch and never lands on a
        # pattern that looks like a bug.
        rng = random.Random(58)
        self._particles = [(rng.random(), rng.random(),
                            0.10 + rng.random() * 0.22,
                            1.0 + rng.random() * 1.6)
                           for _ in range(self.PARTICLES)]

        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(self.FADE_MS)
        self._window = None

    # -- lifecycle ---------------------------------------------------------

    def show(self):
        if self.MIN_MS <= 0:
            return                   # FLOW_SPLASH_MS=0: never appears
        self.setWindowOpacity(0.0)
        super().show()
        self._clock.start()
        self._timer.start()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setDuration(max(120, self.GROUND_MS))
        self._fade.start()

    def hideEvent(self, event):
        # Hidden while the login dialog is up: no reason to keep repainting.
        self._timer.stop()
        super().hideEvent(event)

    def set_message(self, text):
        self._message = text
        self.update()

    def finish(self, window=None):
        """The app is ready. Hands over once MIN_MS has also passed."""
        self._window = window
        self._ready = True
        self._maybe_close()

    def _tick(self):
        self.update()
        if self._ready:
            self._maybe_close()

    def _maybe_close(self):
        if self._closing:
            return
        # An invalid clock means the splash was never shown (disabled), so
        # there is nothing to wait for.
        if self._clock.isValid() and self._clock.elapsed() < self.MIN_MS:
            return
        self._closing = True
        self._timer.stop()
        if self._window is not None:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
        if not self.isVisible():
            return                   # disabled: no fade to run
        self._fade.setDuration(self.FADE_MS)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self.close)
        self._fade.start()

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event):
        t = self._clock.elapsed() if self._clock.isValid() else 0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        self._paint_ground(p)
        depth = _fade_in(t, self.GROUND_MS, 900)     # grid, particles, scan
        if depth > 0:
            self._paint_grid(p, t, depth)
            self._paint_particles(p, t, depth)
        self._paint_mark(p, t)
        if depth > 0:
            self._paint_scan(p, t, depth)
        self._paint_text(p, t)

    def _paint_ground(self, p):
        """Vertical wash plus a soft pool of accent behind where the mark sits."""
        rect = self.rect()
        wash = QLinearGradient(0, 0, 0, rect.height())
        wash.setColorAt(0.0, QColor(theme.SURFACE))
        wash.setColorAt(0.55, QColor(theme.BG))
        wash.setColorAt(1.0, QColor(theme.SURFACE_SUNK))
        p.fillRect(rect, QBrush(wash))

        centre = rect.center()
        radius = rect.height() * 0.52
        pool = QRadialGradient(centre.x(), centre.y() - rect.height() * 0.06,
                               radius)
        glow = QColor(theme.ACCENT_SUNK)
        glow.setAlpha(46)
        pool.setColorAt(0.0, glow)
        pool.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(pool))

        p.setPen(QColor(theme.BORDER))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5),
                          theme.RADIUS, theme.RADIUS)

    def _paint_grid(self, p, t, depth):
        """A slow-drifting pipeline grid. Faint enough to read as texture."""
        line = QColor(theme.BORDER_HI)
        line.setAlpha(int(30 * depth))
        p.setPen(line)
        drift = (t * 0.012) % self.GRID          # one cell per ~3.8s
        x = -self.GRID + drift
        while x < self.width():
            p.drawLine(int(x), 0, int(x), self.height())
            x += self.GRID
        y = -self.GRID + drift
        while y < self.height():
            p.drawLine(0, int(y), self.width(), int(y))
            y += self.GRID

    def _paint_particles(self, p, t, depth):
        """Data rising through the grid: small, slow, never in step."""
        p.setPen(Qt.NoPen)
        for fx, fy, speed, size in self._particles:
            y = (fy - (t / 1000.0) * speed * 0.12) % 1.0
            dot = QColor(theme.ACCENT_HI)
            # Dimmest at the top of the travel, so they arrive rather than blink.
            dot.setAlpha(int((36 + 54 * (1 - y)) * depth))
            p.setBrush(dot)
            p.drawEllipse(QRectF(fx * self.width(), y * self.height(),
                                 size, size))

    def _paint_mark(self, p, t):
        if self._logo.isNull():
            return
        # Registers faintly during the ground fade, then eases up to full.
        early = _fade_in(t, 0, self.GROUND_MS) * 0.35
        settle = _fade_in(t, self.GROUND_MS, self.MARK_MS - self.GROUND_MS)
        opacity = min(1.0, early + settle)
        if opacity <= 0:
            return
        scale = 0.86 + 0.14 * settle if t > self.GROUND_MS else 0.86

        w = int(self._logo.width() * scale)
        h = int(self._logo.height() * scale)
        x = int((self.width() - w) / 2)
        y = int(self.height() * 0.40 - h / 2)
        p.setOpacity(opacity)
        p.drawPixmap(x, y, w, h, self._logo)
        p.setOpacity(1.0)
        self._mark_rect = QRectF(x, y, w, h)

    def _paint_scan(self, p, t, depth):
        """A highlight band crossing the frame every few seconds.

        Brighter where it crosses the mark, which is what sells it as a scan
        of the artwork rather than a stripe over the window.
        """
        period = 3200.0
        pos = ((t - self.GROUND_MS) % period) / period
        y = pos * self.height()
        band = QLinearGradient(0, y - 90, 0, y + 90)
        edge = QColor(theme.ACCENT_HI)
        edge.setAlpha(0)
        mid = QColor(theme.ACCENT_HI)
        mid.setAlpha(int(30 * depth))
        band.setColorAt(0.0, edge)
        band.setColorAt(0.5, mid)
        band.setColorAt(1.0, edge)
        p.fillRect(QRectF(0, y - 90, self.width(), 180), QBrush(band))

        hairline = QColor(theme.ACCENT)
        hairline.setAlpha(int(70 * depth))
        p.setPen(hairline)
        p.drawLine(0, int(y), self.width(), int(y))

    def _paint_text(self, p, t):
        """Wordmark under the logo, then the status line under that."""
        title = QFont()
        title.setPointSizeF(19)
        title.setWeight(QFont.DemiBold)
        title.setLetterSpacing(QFont.AbsoluteSpacing, 7)
        p.setFont(title)
        p.setOpacity(_fade_in(t, self.GROUND_MS * 0.5, 1200))
        p.setPen(QColor(theme.TEXT))
        top = self.height() * 0.40 + self._logo.height() * 0.5
        p.drawText(QRectF(0, top + 18, self.width(), 40),
                   Qt.AlignHCenter | Qt.AlignTop, config.APP_TITLE.upper())

        status = QFont()
        status.setPointSizeF(10)
        status.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(status)
        # Breathes rather than blinks, and only after the mark has landed.
        pulse = 0.55 + 0.45 * (math.sin(t / 380.0) + 1) / 2
        p.setOpacity(_fade_in(t, self.STATUS_MS, 700) * pulse)
        p.setPen(QColor(theme.TEXT_FAINT))
        p.drawText(QRectF(0, top + 62, self.width(), 30),
                   Qt.AlignHCenter | Qt.AlignTop, self._message)
        p.setOpacity(1.0)
