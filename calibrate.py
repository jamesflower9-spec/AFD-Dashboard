"""
calibrate.py

Two-point calibration of all 24 pressure sensors.

Doing this by hand means 48 numbers copied from a terminal into a
spreadsheet, which is slow and easy to get wrong. This reads the counts
straight off the serial link, averages them, and writes calibration.json.

Procedure:

  1. Connect. Leave every sensor open to atmosphere.
  2. Capture zeros. One press records the zero count for all 24 channels.
  3. For each channel: apply a known pressure to that sensor, type the
     reading from the reference gauge, and capture. Around 1000 Pa is a
     good point - near the top of the working range, so the slope is well
     determined, and still far below the sensor's 10 kPa span.
  4. Fit and save.

The table flags any channel whose slope is more than 5% off the group mean.
That is the one worth investigating rather than accepting.

    python calibrate.py
"""

import sys
import csv
import time
from pathlib import Path
import numpy as np

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QPushButton, QDoubleSpinBox, QGridLayout,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)

import sensors
from serial_link import SerialLink, available_ports


N_TAPS = sensors.N_TAPS
AVG_SCANS = 40          # about four seconds at 10 Hz

OUT_CSV = Path(__file__).parent / "calibration_raw.csv"


def tap_name(i):
    """Human label for a tap index, matching the physical wiring."""
    if i < 12:
        return f"{i:2d}  mux A C{i}   upper"
    return f"{i:2d}  mux B C{i - 12}   lower"


class CalWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AFD sensor calibration")
        self.resize(900, 780)

        self.link = None
        self.buf = []                                  # rolling scans
        self.zeros = np.full(N_TAPS, np.nan)
        self.cal_p = np.full(N_TAPS, np.nan)           # applied pressure
        self.cal_c = np.full(N_TAPS, np.nan)           # count at that pressure

        self._build_ui()
        self._refresh_ports()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_table)
        self.timer.start(200)

    # --- layout ---

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)

        v.addWidget(self._top_bar())
        v.addWidget(self._capture_bar())

        self.table = QTableWidget(N_TAPS, 6)
        self.table.setHorizontalHeaderLabels(
            ["Tap", "Live", "Zero", "Applied Pa", "Count", "Slope"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        for i in range(N_TAPS):
            self.table.setItem(i, 0, QTableWidgetItem(tap_name(i)))
            for c in range(1, 6):
                self.table.setItem(i, c, QTableWidgetItem(""))
        v.addWidget(self.table, 1)

        self.lbl_status = QLabel("Not connected")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

    def _top_bar(self):
        box = QGroupBox("Connection")
        g = QGridLayout(box)

        self.cmb_port = QComboBox()
        g.addWidget(self.cmb_port, 0, 0)

        b = QPushButton("Refresh")
        b.clicked.connect(self._refresh_ports)
        g.addWidget(b, 0, 1)

        self.btn_conn = QPushButton("Connect")
        self.btn_conn.clicked.connect(self._toggle_connection)
        g.addWidget(self.btn_conn, 0, 2)

        return box

    def _capture_bar(self):
        box = QGroupBox("Capture")
        g = QGridLayout(box)

        b = QPushButton("1.  Capture zeros  (all sensors open to atmosphere)")
        b.clicked.connect(self._capture_zeros)
        g.addWidget(b, 0, 0, 1, 4)

        g.addWidget(QLabel("2.  Channel"), 1, 0)
        self.cmb_chan = QComboBox()
        for i in range(N_TAPS):
            self.cmb_chan.addItem(tap_name(i), i)
        g.addWidget(self.cmb_chan, 1, 1)

        g.addWidget(QLabel("Applied"), 1, 2)
        self.spin_p = QDoubleSpinBox()
        self.spin_p.setRange(-2000.0, 5000.0)
        self.spin_p.setDecimals(1)
        self.spin_p.setValue(1000.0)
        self.spin_p.setSuffix("  Pa")
        g.addWidget(self.spin_p, 1, 3)

        b = QPushButton("Capture this channel")
        b.clicked.connect(self._capture_point)
        g.addWidget(b, 2, 0, 1, 2)

        b = QPushButton("Skip to next channel")
        b.clicked.connect(self._next_channel)
        g.addWidget(b, 2, 2, 1, 2)

        b = QPushButton("3.  Fit and save calibration.json")
        b.clicked.connect(self._fit_and_save)
        g.addWidget(b, 3, 0, 1, 4)

        return box

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
            self.lbl_status.setText("Disconnected")
            return

        port = self.cmb_port.currentData()
        if not port:
            return

        self.link = SerialLink(port)
        self.link.status.connect(self._on_status)
        self.link.opened.connect(
            lambda p: (self.btn_conn.setText("Disconnect"),
                       self.lbl_status.setText(f"Connected on {p}")))
        self.link.closed.connect(
            lambda: (self.btn_conn.setText("Connect"),
                     self.lbl_status.setText("Disconnected")))
        self.link.failed.connect(
            lambda m: self.lbl_status.setText(f"Failed: {m}"))
        self.link.start()

    def _on_status(self, angle, moving, taps):
        if len(taps) != N_TAPS:
            return
        self.buf.append(np.asarray(taps, dtype=float))
        if len(self.buf) > AVG_SCANS:
            self.buf.pop(0)

    def _avg(self):
        if not self.buf:
            return None
        return np.mean(self.buf, axis=0)

    # --- capture ---

    def _capture_zeros(self):
        a = self._avg()
        if a is None:
            self.lbl_status.setText("No data - connect first")
            return
        self.zeros = a.copy()
        self.lbl_status.setText(
            f"Zeros captured from {len(self.buf)} scans at "
            f"{time.strftime('%H:%M:%S')}. Now do each channel in turn.")

    def _capture_point(self):
        a = self._avg()
        if a is None:
            self.lbl_status.setText("No data - connect first")
            return
        if np.isnan(self.zeros).all():
            self.lbl_status.setText("Capture zeros first")
            return

        ch = self.cmb_chan.currentData()
        p = self.spin_p.value()

        if abs(p) < 50:
            self.lbl_status.setText(
                "Applied pressure too small - the slope would be dominated "
                "by noise. Use something near 1000 Pa.")
            return

        self.cal_p[ch] = p
        self.cal_c[ch] = a[ch]

        slope = (a[ch] - self.zeros[ch]) / p
        self.lbl_status.setText(
            f"Tap {ch}: {a[ch]:.1f} counts at {p:.0f} Pa  ->  "
            f"slope {slope:.5f} counts/Pa")

        self._next_channel()

    def _next_channel(self):
        i = self.cmb_chan.currentIndex()
        if i < N_TAPS - 1:
            self.cmb_chan.setCurrentIndex(i + 1)

    # --- fit ---

    def _slopes(self):
        s = np.full(N_TAPS, np.nan)
        ok = ~np.isnan(self.zeros) & ~np.isnan(self.cal_c) & ~np.isnan(self.cal_p)
        s[ok] = (self.cal_c[ok] - self.zeros[ok]) / self.cal_p[ok]
        return s

    def _fit_and_save(self):
        s = self._slopes()
        done = ~np.isnan(s)

        if done.sum() == 0:
            self.lbl_status.setText("Nothing captured yet")
            return

        if done.sum() < N_TAPS:
            missing = [int(i) for i in np.where(~done)[0]]
            r = QMessageBox.question(
                self, "Incomplete",
                f"{done.sum()} of {N_TAPS} channels captured.\n"
                f"Missing: {missing}\n\n"
                "Save anyway? Missing channels will use the mean slope.",
                QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        mean = float(np.mean(s[done]))

        cal = sensors.default_calibration()
        cal["nominal_slope"] = round(mean, 5)
        cal["slopes"] = {str(i): round(float(s[i]), 5)
                         for i in range(N_TAPS) if done[i]}
        cal["notes"] = (
            f"two-point calibration, {done.sum()} channels, "
            f"{time.strftime('%Y-%m-%d')}; "
            "slope in ADC counts per pascal; offset comes from live tare")
        sensors.save_calibration(cal)

        # Raw numbers as well, so the report can show the working
        with open(OUT_CSV, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["tap", "surface", "mux", "mux_channel",
                        "zero_counts", "applied_pa", "counts", "slope"])
            for i in range(N_TAPS):
                w.writerow([i,
                            "upper" if i < 12 else "lower",
                            "A" if i < 12 else "B",
                            i if i < 12 else i - 12,
                            "" if np.isnan(self.zeros[i]) else f"{self.zeros[i]:.1f}",
                            "" if np.isnan(self.cal_p[i]) else f"{self.cal_p[i]:.1f}",
                            "" if np.isnan(self.cal_c[i]) else f"{self.cal_c[i]:.1f}",
                            "" if np.isnan(s[i]) else f"{s[i]:.5f}"])

        spread = 100 * (np.nanmax(s[done]) - np.nanmin(s[done])) / mean
        self.lbl_status.setText(
            f"Saved {done.sum()} slopes. Mean {mean:.5f} counts/Pa, "
            f"spread {spread:.1f}%. Raw data in {OUT_CSV.name}.")

    # --- table ---

    def _refresh_table(self):
        live = self._avg()
        s = self._slopes()
        done = ~np.isnan(s)
        mean = float(np.mean(s[done])) if done.any() else None

        for i in range(N_TAPS):
            self.table.item(i, 1).setText(
                "" if live is None else f"{live[i]:.1f}")
            self.table.item(i, 2).setText(
                "" if np.isnan(self.zeros[i]) else f"{self.zeros[i]:.1f}")
            self.table.item(i, 3).setText(
                "" if np.isnan(self.cal_p[i]) else f"{self.cal_p[i]:.0f}")
            self.table.item(i, 4).setText(
                "" if np.isnan(self.cal_c[i]) else f"{self.cal_c[i]:.1f}")

            cell = self.table.item(i, 5)
            if np.isnan(s[i]):
                cell.setText("")
                cell.setBackground(Qt.white)
            else:
                dev = 100 * (s[i] - mean) / mean if mean else 0.0
                cell.setText(f"{s[i]:.5f}   ({dev:+.1f}%)")
                # More than 5% off the group is worth a look, not an accept
                cell.setBackground(Qt.yellow if abs(dev) > 5.0 else Qt.white)

    def closeEvent(self, ev):
        if self.link:
            self.link.stop()
            self.link.wait(1000)
        ev.accept()


def main():
    app = QApplication(sys.argv)
    w = CalWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()