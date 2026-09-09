"""
dashboard.py

Airfoil Performance Demonstrator - PC dashboard.

Data sources, chosen automatically:

  Simulated   nothing connected. The angle you set is the angle plotted, and
              tap readings come from the XFOIL data plus realistic noise.
              This is the emulation mode for when the rig is unavailable.

  Hardware    connected over serial. Angles come from the firmware, and tap
              readings are real ADC counts converted through the calibration
              in calibration.json.

    python dashboard.py
"""

import sys
import time
import numpy as np

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QDoubleSpinBox, QCheckBox, QGridLayout,
    QComboBox, QSpinBox, QLineEdit,
)
import pyqtgraph as pg

import xfoil_data as xd
import taps
import sensors
from logger import RunLogger
from serial_link import SerialLink, available_ports


# --- Appearance -------------------------------------------------------------

pg.setConfigOptions(antialias=True, background="w", foreground="k")

PEN_REF     = pg.mkPen("#888", width=2, style=Qt.DashLine)
PEN_REF_BAD = pg.mkPen("#ccc", width=2, style=Qt.DotLine)
PEN_CL      = pg.mkPen("#888", width=2)
COL_UPPER   = "#c0392b"
COL_LOWER   = "#2471a3"
COL_MEAS    = "#117a3d"

AIR_DENSITY = 1.225

# Scans to average before plotting. One ADC count is about 0.011 Cp at
# 20 m/s, so a single scan is visibly noisy. Averaging N scans divides the
# random part by root N; 20 at 10 Hz is two seconds of data, which is a
# reasonable trade against watching the rig move.
AVG_SCANS = 20


# --- Sensor data sources ----------------------------------------------------

class SimulatedSource:
    """
    Stands in for the sensors when nothing is connected.

    Noise is added in pascals, not in Cp. That matters: dynamic pressure goes
    with the square of wind speed, so the same sensor noise becomes four times
    the Cp error when the speed halves. Drop the wind speed to 5 m/s and watch
    the scatter - that is a real property of the rig, not a display artefact.
    """

    label = "Simulated"

    def __init__(self, cp_set, noise_pa=3.0):
        self.cp_set = cp_set
        self.noise_pa = noise_pa

    def read(self, angle_deg, wind_ms):
        x, cp = xd.cp_at(self.cp_set, angle_deg)
        cp_up, cp_lo = taps.sample_at_taps(x, cp)

        q = 0.5 * AIR_DENSITY * wind_ms ** 2
        if q < 5.0:
            return cp_up * 0.0, cp_lo * 0.0

        p_up = cp_up * q + np.random.normal(0, self.noise_pa, cp_up.shape)
        p_lo = cp_lo * q + np.random.normal(0, self.noise_pa, cp_lo.shape)
        return p_up / q, p_lo / q


class HardwareSource:
    """
    Real sensors. Holds a rolling buffer of scans and averages them.

    Conversion lives in sensors.TapConverter: slope from bench calibration,
    zero from a live tare taken wind-off.
    """

    label = "Hardware"

    def __init__(self):
        self.conv = sensors.TapConverter()
        self.buf = []
        self.latest = np.zeros(sensors.N_TAPS)

    def raw(self):
        """Averaged ADC counts, before calibration. Logged alongside Cp so a
        run can be reprocessed if the calibration is ever revised."""
        return self.averaged()

    def push(self, counts):
        if len(counts) != sensors.N_TAPS:
            return
        self.latest = np.asarray(counts, dtype=float)
        self.buf.append(self.latest)
        if len(self.buf) > AVG_SCANS:
            self.buf.pop(0)

    def averaged(self):
        if not self.buf:
            return np.zeros(sensors.N_TAPS)
        return np.mean(self.buf, axis=0)

    def tare(self):
        """Take the current average as zero. Wind must be off."""
        self.conv.set_tare(self.averaged())

    def read(self, angle_deg, wind_ms):
        return self.conv.cp(self.averaged(), wind_ms)


# --- Sweep sequencing -------------------------------------------------------

# A sweep is driven from the PC: send an angle, wait for the firmware to
# report that it started moving, wait for it to stop, dwell, send the next.
# Watching for the start as well as the stop avoids the race where the status
# line still reads "idle" for one cycle after a command was accepted.

SWEEP_IDLE, SWEEP_WAIT_START, SWEEP_WAIT_STOP, SWEEP_DWELL = range(4)


# --- Main window ------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Airfoil Performance Demonstrator")
        self.resize(1300, 860)

        self.polar = xd.load_polar()
        self.cp_set = xd.load_cp_set()

        self.sim = SimulatedSource(self.cp_set)
        self.hw = HardwareSource()

        self.link = None
        self.actual_angle = 0.0
        self.moving = False

        self.sweep_state = SWEEP_IDLE
        self.sweep_targets = []
        self.sweep_i = 0
        self.sweep_deadline = 0
        self.sweep_dir = 1.0

        self.measured = []
        self.log = None                  # RunLogger while recording
        self.log_last = 0.0              # throttle, seconds

        self._build_ui()
        self._draw_references()
        self._refresh_ports()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)

    @property
    def source(self):
        return self.hw if self.link else self.sim

    # --- layout ---

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)

        col = QWidget()
        col.setFixedWidth(290)
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._conn_box())
        v.addWidget(self._motion_box())
        v.addWidget(self._flow_box())
        v.addWidget(self._record_box())
        v.addWidget(self._readout_box())
        v.addStretch(1)

        outer.addWidget(col, 0)
        outer.addWidget(self._plots(), 1)

    def _conn_box(self):
        box = QGroupBox("Connection")
        g = QGridLayout(box)

        self.cmb_port = QComboBox()
        g.addWidget(self.cmb_port, 0, 0, 1, 2)

        b = QPushButton("Refresh")
        b.clicked.connect(self._refresh_ports)
        g.addWidget(b, 1, 0)

        self.btn_conn = QPushButton("Connect")
        self.btn_conn.clicked.connect(self._toggle_connection)
        g.addWidget(self.btn_conn, 1, 1)

        self.lbl_conn = QLabel("Simulated - no hardware")
        self.lbl_conn.setWordWrap(True)
        g.addWidget(self.lbl_conn, 2, 0, 1, 2)

        return box

    def _motion_box(self):
        box = QGroupBox("Angle of attack")
        g = QGridLayout(box)
        r = 0

        g.addWidget(QLabel("Target"), r, 0, 1, 2); r += 1
        self.spin_angle = QDoubleSpinBox()
        self.spin_angle.setRange(-20.0, 20.0)
        self.spin_angle.setSingleStep(0.5)
        self.spin_angle.setDecimals(2)
        self.spin_angle.setSuffix("  deg")
        self.spin_angle.setKeyboardTracking(False)
        self.spin_angle.valueChanged.connect(self._target_changed)
        g.addWidget(self.spin_angle, r, 0, 1, 2); r += 1

        bm, bp = QPushButton("-1"), QPushButton("+1")
        bm.clicked.connect(lambda: self.spin_angle.setValue(
            self.spin_angle.value() - 1.0))
        bp.clicked.connect(lambda: self.spin_angle.setValue(
            self.spin_angle.value() + 1.0))
        g.addWidget(bm, r, 0); g.addWidget(bp, r, 1); r += 1

        b = QPushButton("Set zero here")
        b.clicked.connect(self._set_zero)
        g.addWidget(b, r, 0, 1, 2); r += 1

        b = QPushButton("STOP")
        b.clicked.connect(self._stop)
        g.addWidget(b, r, 0, 1, 2); r += 1

        g.addWidget(QLabel("Sweep step"), r, 0)
        self.spin_step = QDoubleSpinBox()
        self.spin_step.setRange(0.5, 5.0)
        self.spin_step.setValue(2.0)
        self.spin_step.setSingleStep(0.5)
        g.addWidget(self.spin_step, r, 1); r += 1

        g.addWidget(QLabel("Dwell (ms)"), r, 0)
        self.spin_dwell = QSpinBox()
        self.spin_dwell.setRange(100, 5000)
        self.spin_dwell.setValue(2000)
        self.spin_dwell.setSingleStep(100)
        g.addWidget(self.spin_dwell, r, 1); r += 1

        self.btn_sweep = QPushButton("Start sweep")
        self.btn_sweep.clicked.connect(self._toggle_sweep)
        g.addWidget(self.btn_sweep, r, 0, 1, 2); r += 1

        return box

    def _flow_box(self):
        box = QGroupBox("Flow")
        g = QGridLayout(box)

        g.addWidget(QLabel("Wind speed"), 0, 0, 1, 2)
        self.spin_wind = QDoubleSpinBox()
        self.spin_wind.setRange(0.0, 40.0)
        self.spin_wind.setValue(20.0)
        self.spin_wind.setSingleStep(1.0)
        self.spin_wind.setSuffix("  m/s")
        g.addWidget(self.spin_wind, 1, 0, 1, 2)

        self.lbl_re = QLabel("")
        g.addWidget(self.lbl_re, 2, 0, 1, 2)

        self.btn_tare = QPushButton("Tare sensors (wind off)")
        self.btn_tare.clicked.connect(self._tare)
        g.addWidget(self.btn_tare, 3, 0, 1, 2)

        self.lbl_tare = QLabel("")
        self.lbl_tare.setWordWrap(True)
        g.addWidget(self.lbl_tare, 4, 0, 1, 2)

        return box

    def _record_box(self):
        box = QGroupBox("Record")
        g = QGridLayout(box)

        g.addWidget(QLabel("Run name"), 0, 0)
        self.edit_name = QLineEdit("run")
        g.addWidget(self.edit_name, 0, 1)

        self.btn_rec = QPushButton("Start recording")
        self.btn_rec.clicked.connect(self._toggle_record)
        g.addWidget(self.btn_rec, 1, 0, 1, 2)

        self.lbl_rec = QLabel("Not recording")
        self.lbl_rec.setWordWrap(True)
        g.addWidget(self.lbl_rec, 2, 0, 1, 2)

        return box

    def _toggle_record(self):
        if self.log is None:
            self.log = RunLogger({
                "source": self.source.label,
                "wind_speed_ms": self.spin_wind.value(),
                "reynolds": round(
                    AIR_DENSITY * self.spin_wind.value()
                    * (taps.CHORD_MM / 1000.0) / 1.81e-5),
                "chord_mm": taps.CHORD_MM,
                "calibration": "calibration.json",
                "tared": self.hw.conv.tared if self.link else "n/a",
                "avg_scans": AVG_SCANS,
            })
            self.btn_rec.setText("Stop and save")
            self.lbl_rec.setText("Recording...")
        else:
            if len(self.log) == 0:
                self.lbl_rec.setText("Nothing recorded")
            else:
                xu, xl = taps.tap_positions()
                name = self.edit_name.text().strip() or "run"
                raw, summ = self.log.save(name, xu, xl)
                self.lbl_rec.setText(
                    f"Saved {len(self.log)} samples\n{raw.name}\n{summ.name}")
            self.log = None
            self.btn_rec.setText("Start recording")

    def _readout_box(self):
        box = QGroupBox("Readout")
        g = QGridLayout(box)

        self.lbl_actual = QLabel("0.00 deg")
        f = self.lbl_actual.font()
        f.setPointSize(f.pointSize() + 8)
        self.lbl_actual.setFont(f)
        g.addWidget(self.lbl_actual, 0, 0, 1, 2)

        self.lbl_cl = QLabel("")
        self.lbl_cl.setWordWrap(True)
        g.addWidget(self.lbl_cl, 1, 0, 1, 2)

        self.chk_hold = QCheckBox("Keep measured points")
        self.chk_hold.setChecked(True)
        g.addWidget(self.chk_hold, 2, 0, 1, 2)

        b = QPushButton("Clear points")
        b.clicked.connect(lambda: self.measured.clear())
        g.addWidget(b, 3, 0, 1, 2)

        return box

    def _plots(self):
        holder = QWidget()
        v = QVBoxLayout(holder)

        # Suction upwards, per the usual aerodynamic convention
        self.p_cp = pg.PlotWidget()
        self.p_cp.setLabel("bottom", "x/c")
        self.p_cp.setLabel("left", "Cp")
        self.p_cp.invertY(True)
        self.p_cp.showGrid(x=True, y=True, alpha=0.3)
        self.p_cp.setXRange(-0.02, 1.02)
        self.p_cp.addLegend(offset=(-10, 10))
        v.addWidget(self.p_cp, 3)

        self.p_cl = pg.PlotWidget()
        self.p_cl.setLabel("bottom", "Angle of attack (deg)")
        self.p_cl.setLabel("left", "CL")
        self.p_cl.showGrid(x=True, y=True, alpha=0.3)
        self.p_cl.setXRange(-21, 21)
        v.addWidget(self.p_cl, 2)

        return holder

    def _draw_references(self):
        self.curve_ref = self.p_cp.plot([], [], pen=PEN_REF, name="XFOIL")
        self.pts_upper = self.p_cp.plot(
            [], [], pen=None, symbol="o", symbolSize=8,
            symbolBrush=COL_UPPER, name="Upper taps")
        self.pts_lower = self.p_cp.plot(
            [], [], pen=None, symbol="s", symbolSize=8,
            symbolBrush=COL_LOWER, name="Lower taps")

        a, cl = self.polar["alpha"], self.polar["cl"]
        good = np.abs(a) <= xd.VALID_ALPHA

        self.p_cl.plot(a[good], cl[good], pen=PEN_CL)
        for side in (a < -xd.VALID_ALPHA, a > xd.VALID_ALPHA):
            if side.any():
                self.p_cl.plot(a[side], cl[side], pen=PEN_REF_BAD)

        note = pg.TextItem("XFOIL cannot stall - dotted region is not physical",
                           color="#999", anchor=(0.5, 0))
        note.setPos(0, cl.min())
        self.p_cl.addItem(note)

        self.pts_cl = self.p_cl.plot(
            [], [], pen=None, symbol="o", symbolSize=6, symbolBrush=COL_MEAS)
        self.pt_now = self.p_cl.plot(
            [], [], pen=None, symbol="o", symbolSize=13, symbolBrush=None,
            symbolPen=pg.mkPen(COL_MEAS, width=2))

    # --- connection ---

    def _refresh_ports(self):
        self.cmb_port.clear()
        for dev, label in available_ports():
            self.cmb_port.addItem(label, dev)
        if self.cmb_port.count() == 0:
            self.cmb_port.addItem("no ports found", None)

    def _toggle_connection(self):
        if self.link:
            self.link.stop()
            self.link.wait(1000)
            self.link = None
            self.btn_conn.setText("Connect")
            self.lbl_conn.setText("Simulated - no hardware")
            return

        port = self.cmb_port.currentData()
        if not port:
            return

        self.link = SerialLink(port)
        self.link.status.connect(self._on_status)
        self.link.reply.connect(self._on_reply)
        self.link.opened.connect(self._on_opened)
        self.link.closed.connect(self._on_closed)
        self.link.failed.connect(self._on_failed)
        self.link.start()

    def _on_opened(self, port):
        self.btn_conn.setText("Disconnect")
        self.lbl_conn.setText(f"Connected on {port}")
        if self.link:
            self.link.send("?")

    def _on_closed(self):
        self.btn_conn.setText("Connect")
        self.lbl_conn.setText("Simulated - no hardware")
        self.link = None
        self.moving = False
        self._end_sweep()

    def _on_failed(self, msg):
        self.lbl_conn.setText(f"Failed: {msg}")
        self.link = None
        self.btn_conn.setText("Connect")

    def _on_status(self, angle, moving, tap_counts):
        self.actual_angle = angle
        self.moving = moving
        if tap_counts:
            self.hw.push(tap_counts)

    def _on_reply(self, line):
        if line.startswith("ERR"):
            self.lbl_conn.setText(f"Firmware said: {line}")

    # --- commands ---

    def _target_changed(self, value):
        if self.link:
            self.link.send(f"A{value:.2f}")

    def _set_zero(self):
        if self.link:
            self.link.send("Z")
        else:
            self.spin_angle.setValue(0.0)

    def _stop(self):
        self._end_sweep()
        if self.link:
            self.link.send("X")

    def _tare(self):
        """
        Capture the current readings as zero differential.

        Must be done with the wind off and under the real operating supply.
        The sensors are ratiometric, so a tare taken on a bench supply does
        not hold once the buck converter is powering them.
        """
        if not self.link:
            self.lbl_tare.setText("No hardware connected")
            return
        self.hw.tare()
        self.lbl_tare.setText(
            f"Tared at {time.strftime('%H:%M:%S')} - "
            "redo if the supply or temperature changes")

    # --- sweep ---

    def _toggle_sweep(self):
        if self.sweep_state == SWEEP_IDLE:
            step = self.spin_step.value()
            up = list(np.arange(-20.0, 20.0 + 1e-6, step))
            self.sweep_targets = up + up[::-1][1:]
            self.sweep_i = -1
            self.sweep_state = SWEEP_DWELL
            self.sweep_deadline = 0
            self.btn_sweep.setText("Stop sweep")
        else:
            self._end_sweep()

    def _end_sweep(self):
        self.sweep_state = SWEEP_IDLE
        self.btn_sweep.setText("Start sweep")

    def _sweep_step(self, now_ms):
        if self.sweep_state == SWEEP_IDLE:
            return

        if not self.link:
            a = self.spin_angle.value() + self.sweep_dir * 0.2
            if a >= 20.0:
                a, self.sweep_dir = 20.0, -1.0
            elif a <= -20.0:
                a, self.sweep_dir = -20.0, 1.0
            self.spin_angle.setValue(a)
            return

        if self.sweep_state == SWEEP_DWELL:
            if now_ms < self.sweep_deadline:
                return
            self.sweep_i += 1
            if self.sweep_i >= len(self.sweep_targets):
                self.sweep_i = 0
            self.spin_angle.setValue(self.sweep_targets[self.sweep_i])
            self.sweep_state = SWEEP_WAIT_START
            self.sweep_deadline = now_ms + 1500

        elif self.sweep_state == SWEEP_WAIT_START:
            if self.moving:
                self.sweep_state = SWEEP_WAIT_STOP
            elif now_ms > self.sweep_deadline:
                self.sweep_state = SWEEP_DWELL
                self.sweep_deadline = now_ms + self.spin_dwell.value()

        elif self.sweep_state == SWEEP_WAIT_STOP:
            if not self.moving:
                self.sweep_state = SWEEP_DWELL
                self.sweep_deadline = now_ms + self.spin_dwell.value()

    # --- update loop ---

    def _tick(self):
        now_ms = int(time.monotonic() * 1000)
        self._sweep_step(now_ms)

        angle = self.actual_angle if self.link else self.spin_angle.value()
        wind = self.spin_wind.value()

        re = AIR_DENSITY * wind * (taps.CHORD_MM / 1000.0) / 1.81e-5
        self.lbl_re.setText(f"Re = {re:,.0f}")

        self.lbl_actual.setText(
            f"{angle:+.2f} deg" + ("  moving" if self.moving else ""))

        x_ref, cp_ref = xd.cp_at(self.cp_set, angle)
        self.curve_ref.setData(x_ref, cp_ref)

        cp_up, cp_lo = self.source.read(angle, wind)
        xt_up, xt_lo = taps.tap_positions()
        self.pts_upper.setData(xt_up, cp_up)
        self.pts_lower.setData(xt_lo, cp_lo)

        cl_meas = taps.cl_from_taps(cp_up, cp_lo, angle)
        cl_ref = xd.cl_at(self.polar, angle)

        self.pt_now.setData([angle], [cl_meas])

        # Only bank a point when the airfoil is settled, otherwise the CL plot
        # fills with readings taken mid-move
        if self.chk_hold.isChecked():
            if not self.moving:
                self.measured.append((angle, cl_meas))
                if len(self.measured) > 4000:
                    self.measured = self.measured[-4000:]
            if self.measured:
                arr = np.array(self.measured)
                self.pts_cl.setData(arr[:, 0], arr[:, 1])
        else:
            self.pts_cl.setData([], [])

        # Log at 5 Hz rather than every frame. The display runs at 20 Hz but
        # the sensor average only spans 20 scans, so faster logging would
        # write heavily overlapping samples and inflate the file for nothing.
        if self.log is not None:
            now_s = now_ms / 1000.0
            if now_s - self.log_last >= 0.2:
                self.log_last = now_s
                self.log.add(angle, self.moving, cp_up, cp_lo, cl_meas,
                             raw_counts=self.hw.raw() if self.link else None)
            self.lbl_rec.setText(f"Recording - {len(self.log)} samples")

        lines = [f"{self.source.label}",
                 f"CL measured   {cl_meas:+.3f}",
                 f"CL XFOIL      {cl_ref:+.3f}"]

        if self.link and not self.hw.conv.tared:
            lines.append("\nNot tared - readings are absolute, not "
                         "differential. Tare with the wind off.")
        if abs(angle) > xd.VALID_ALPHA:
            lines.append("\nBeyond the angle where XFOIL stops being physical.")

        self.lbl_cl.setText("\n".join(lines))

    def closeEvent(self, ev):
        if self.link:
            self.link.stop()
            self.link.wait(1000)
        ev.accept()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()