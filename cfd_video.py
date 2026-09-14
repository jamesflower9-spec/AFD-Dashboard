"""
cfd_video.py

Shows the CFD flow visualisation at the angle the rig is currently at.

Two clips, each a continuous rotation from zero: one to +20 degrees, one to
-20. The panel shows whichever matches the sign of the current angle. Both
run outward from zero, so both map the same way - fraction along the clip is
the magnitude of the angle over 20 - and neither needs trimming to line up.

Neither clip is ever played. The rig steps and holds; a looping video beside a
stepping airfoil would show the two at different angles at almost every
moment, and a viewer would reasonably assume they corresponded. So the clip is
scrubbed. Each angle maps to the frame computed at that angle, and the two
stay locked.

If only the positive clip is present the panel still works and says what it is
missing on the negative side, rather than showing a black rectangle. A missing
file and a file the media backend refuses are different faults with different
fixes, so both are reported distinctly.

The panel is deliberately size-limited. The CFD clip is supporting evidence
next to the Cp and CL plots, not the main result, and an expanding video
widget will take every pixel the layout offers it.
"""

from PySide6.QtCore import Qt, QUrl, QTimer, QSize
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QSizePolicy)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from paths import bundled


# Both clips start at zero incidence and rotate outward to the magnitude
# below. Keep the solver settings identical between the two runs - same
# colour range, same camera - or the picture will jump at the changeover.
VIDEO_UP    = "assets/cfd/stall_sweep.mp4"
VIDEO_DOWN  = "assets/cfd/stall_sweep_neg.mp4"
ANGLE_LIMIT = 20.0

# Frames come out of Fluent at 1207 x 507. Holding the true aspect avoids the
# black letterbox bands QVideoWidget would otherwise draw.
VIDEO_ASPECT = 1207 / 507

# Tallest the picture is allowed to get, in pixels. Raise or lower this one
# number to trade CFD panel against plot area.
MAX_VIDEO_HEIGHT = 260

# Seeking on every frame would thrash the decoder, and the difference between
# 12.0 and 12.05 degrees is invisible. Only seek when the angle has moved
# enough to change the picture.
SEEK_THRESHOLD_DEG = 0.25
SEEK_MIN_INTERVAL_MS = 100

# Deadband around zero. Without it, an angle sitting on the boundary would
# swap clips back and forth on successive updates and the panel would flicker.
SIGN_DEADBAND_DEG = 0.2

CAPTION = "CFD: velocity magnitude, Re = 380 000"


class _Clip:
    """One video file, its player and its widget. Owned by the panel."""

    def __init__(self, rel, parent):
        self.rel = rel
        self.path = bundled(rel)
        self.duration_ms = 0
        self.ready = False
        self.error = None

        self.widget = QVideoWidget(parent)
        self.widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.widget.hide()

        exists = self.path.exists()
        size = self.path.stat().st_size if exists else 0
        print(f"[cfd] {rel}")
        print(f"[cfd]   -> {self.path}")
        print(f"[cfd]   exists={exists} size={size} bytes")

        if not exists:
            self.player = None
            self.error = "missing"
            return

        self.audio = QAudioOutput()
        self.audio.setMuted(True)
        self.player = QMediaPlayer()
        self.player.setVideoOutput(self.widget)
        self.player.setAudioOutput(self.audio)
        self.player.durationChanged.connect(self._on_duration)
        self.player.errorOccurred.connect(self._on_error)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))

        # Qt will not render a frame until the pipeline has started, so play
        # briefly and pause. Without this the widget stays black until the
        # first seek, which looks like a failure.
        self.player.play()
        QTimer.singleShot(150, self._prime)

    @property
    def ok(self):
        return self.player is not None and self.error is None

    def _prime(self):
        if self.player:
            self.player.pause()
            self.player.setPosition(0)
            self.ready = True
            print(f"[cfd] {self.rel} primed, duration={self.duration_ms} ms")

    def _on_duration(self, ms):
        self.duration_ms = ms

    def _on_status(self, status):
        # InvalidMedia means the backend read the file and refused it, which
        # is a codec problem rather than a path problem.
        print(f"[cfd] {self.rel} status: {status}")

    def _on_error(self, err, text):
        print(f"[cfd] {self.rel} error: {err} - {text}")
        self.error = text
        self.widget.hide()

    def seek_fraction(self, frac):
        if self.ok and self.ready and self.duration_ms > 0:
            self.player.setPosition(int(frac * self.duration_ms))

    def stop(self):
        if self.player:
            self.player.stop()


class CfdVideoPanel(QWidget):
    """
    A pair of video widgets that seek rather than play, one per direction.

    Only one is visible at a time. Both are loaded and primed at startup, so
    crossing zero is an instant show/hide rather than a source reload.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self.last_angle = None
        self.last_seek = 0
        self.current = None          # the _Clip being shown

        # Vertical policy is Maximum, not Expanding: the panel accepts less
        # than its hint but never claims more.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # Stretches either side keep the picture centred once its width is
        # pinned to the aspect ratio.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self.up = _Clip(VIDEO_UP, self)
        self.down = _Clip(VIDEO_DOWN, self)
        row.addWidget(self.up.widget)
        row.addWidget(self.down.widget)
        row.addStretch(1)
        v.addLayout(row)

        self.msg = QLabel()
        self.msg.setAlignment(Qt.AlignCenter)
        self.msg.setWordWrap(True)
        self.msg.setStyleSheet("color: #666;")
        v.addWidget(self.msg)

        if not (self.up.ok or self.down.ok):
            self.msg.setText(
                "No CFD video found.\nLooked for:\n"
                f"{self.up.path}\n{self.down.path}")
        else:
            self.msg.setText(CAPTION)

        self._fit()

    # --- sizing ---

    def sizeHint(self):
        return QSize(int(MAX_VIDEO_HEIGHT * VIDEO_ASPECT),
                     MAX_VIDEO_HEIGHT + 20)

    def maximumHeight(self):
        return MAX_VIDEO_HEIGHT + 40

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        """Pin both pictures to the true aspect, within the height cap."""
        avail_w = max(1, self.width())
        h = MAX_VIDEO_HEIGHT
        w = h * VIDEO_ASPECT
        if w > avail_w:
            w = avail_w
            h = w / VIDEO_ASPECT
        self.up.widget.setFixedSize(int(w), int(h))
        self.down.widget.setFixedSize(int(w), int(h))

    # --- clip selection ---

    def _clip_for(self, angle_deg):
        """
        Which clip covers this angle.

        The deadband is applied against the clip already showing, so an angle
        parked near zero keeps whatever is on screen instead of alternating.
        """
        if self.current is self.up and angle_deg > -SIGN_DEADBAND_DEG:
            return self.up
        if self.current is self.down and angle_deg < SIGN_DEADBAND_DEG:
            return self.down
        return self.up if angle_deg >= 0.0 else self.down

    def _show_clip(self, clip):
        if clip is self.current:
            return
        if self.current:
            self.current.widget.hide()
        self.current = clip
        clip.widget.show()
        self.msg.setText(CAPTION)
        self._fit()
        # Force a seek on the next update: the new clip is sitting wherever it
        # was last left, which is not this angle.
        self.last_angle = None

    def _show_message(self, text):
        if self.current:
            self.current.widget.hide()
            self.current = None
        self.msg.setText(text)

    # --- driven from the dashboard's update loop ---

    def show_angle(self, angle_deg, now_ms):
        """
        Seek to the frame for this angle.

        Called at the dashboard's refresh rate, so it rate-limits itself: a
        seek per frame would thrash the decoder for no visible gain.
        """
        if not (self.up.ok or self.down.ok):
            return

        if abs(angle_deg) > ANGLE_LIMIT + 0.5:
            self._show_message(
                f"CFD covers -{ANGLE_LIMIT:.0f} to +{ANGLE_LIMIT:.0f} degrees.")
            return

        clip = self._clip_for(angle_deg)

        if not clip.ok:
            side = "negative" if clip is self.down else "positive"
            self._show_message(
                f"No CFD clip for {side} incidence.\n"
                f"Expected {clip.rel}")
            return

        self._show_clip(clip)

        if not clip.ready or clip.duration_ms <= 0:
            return

        if (self.last_angle is not None
                and abs(angle_deg - self.last_angle) < SEEK_THRESHOLD_DEG):
            return
        if now_ms - self.last_seek < SEEK_MIN_INTERVAL_MS:
            return

        # Both clips run outward from zero, so the fraction is the magnitude
        # of the angle regardless of which one is showing.
        frac = min(abs(angle_deg), ANGLE_LIMIT) / ANGLE_LIMIT
        clip.seek_fraction(frac)

        self.last_angle = angle_deg
        self.last_seek = now_ms

    def stop(self):
        self.up.stop()
        self.down.stop()