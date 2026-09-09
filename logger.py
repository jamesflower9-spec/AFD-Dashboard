"""
logger.py

Writes a run to CSV.

Two files come out of a logged run, because they answer different questions:

  <name>.csv          one row per logged sample, all 24 taps plus CL.
                      The raw record - everything that was measured.

  <name>_summary.csv  one row per angle visited, taps and CL averaged over
                      the samples taken while the rig was settled there.
                      This is what goes into a plot or a report table.

Both carry a header block describing the conditions, so a file found six
months later still says what it is. Without that a CSV of numbers is close
to useless.
"""

import csv
import time
from pathlib import Path
import numpy as np


LOG_DIR = Path(__file__).parent / "logs"


class RunLogger:
    """
    Accumulates samples during a run and writes them out at the end.

    Samples are held in memory rather than streamed to disk. A sweep is a few
    hundred rows at most, so there is no reason to keep a file handle open
    and risk a half-written file if the program is closed mid-run.
    """

    def __init__(self, meta):
        """
        meta: dict of conditions recorded in the file header - wind speed,
        Reynolds number, calibration file, whether the sensors were tared.
        """
        self.meta = dict(meta)
        self.meta["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.rows = []
        self.t0 = time.monotonic()

    def add(self, angle, moving, cp_upper, cp_lower, cl, raw_counts=None):
        self.rows.append({
            "t": time.monotonic() - self.t0,
            "angle": float(angle),
            "moving": bool(moving),
            "cp": np.concatenate([cp_upper, cp_lower]).astype(float),
            "cl": float(cl),
            "raw": None if raw_counts is None
                   else np.asarray(raw_counts, dtype=float),
        })

    def __len__(self):
        return len(self.rows)

    # --- writing ---

    def _header(self, w):
        w.writerow(["# Airfoil Performance Demonstrator - logged run"])
        for k, v in self.meta.items():
            w.writerow([f"# {k}", v])
        w.writerow([f"# samples", len(self.rows)])
        w.writerow([])

    def save(self, name, tap_x_upper, tap_x_lower):
        """
        Write both files. Returns (raw_path, summary_path).

        tap_x_upper / tap_x_lower are the chordwise positions, written into
        the header so the file is self-describing - a column called cp_07
        means nothing on its own.
        """
        LOG_DIR.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        raw_path = LOG_DIR / f"{name}_{stamp}.csv"
        sum_path = LOG_DIR / f"{name}_{stamp}_summary.csv"

        xs = list(tap_x_upper) + list(tap_x_lower)
        cols = ([f"cp_u{i:02d}" for i in range(len(tap_x_upper))] +
                [f"cp_l{i:02d}" for i in range(len(tap_x_lower))])

        has_raw = any(r["raw"] is not None for r in self.rows)

        # --- raw ---
        with open(raw_path, "w", newline="") as f:
            w = csv.writer(f)
            self._header(w)
            w.writerow(["# tap x/c"] + [f"{x:.4f}" for x in xs])
            w.writerow([])
            head = ["time_s", "angle_deg", "moving", "cl"] + cols
            if has_raw:
                head += [c.replace("cp_", "adc_") for c in cols]
            w.writerow(head)

            for r in self.rows:
                row = [f"{r['t']:.3f}", f"{r['angle']:.3f}",
                       1 if r["moving"] else 0, f"{r['cl']:.4f}"]
                row += [f"{v:.4f}" for v in r["cp"]]
                if has_raw:
                    row += ([f"{v:.1f}" for v in r["raw"]]
                            if r["raw"] is not None else [""] * len(cols))
                w.writerow(row)

        # --- summary ---
        # Group by commanded station. Samples taken while moving are dropped:
        # a reading captured in transit belongs to no particular angle.
        settled = [r for r in self.rows if not r["moving"]]
        groups = {}
        for r in settled:
            key = round(r["angle"], 1)
            groups.setdefault(key, []).append(r)

        with open(sum_path, "w", newline="") as f:
            w = csv.writer(f)
            self._header(w)
            w.writerow(["# one row per angle, averaged over settled samples"])
            w.writerow(["# tap x/c"] + [f"{x:.4f}" for x in xs])
            w.writerow([])
            w.writerow(["angle_deg", "n", "cl_mean", "cl_sd"] + cols)

            for angle in sorted(groups):
                g = groups[angle]
                cp = np.mean([r["cp"] for r in g], axis=0)
                cl = np.array([r["cl"] for r in g])
                w.writerow([f"{angle:.2f}", len(g),
                            f"{cl.mean():.4f}",
                            f"{cl.std():.4f}" if len(cl) > 1 else ""]
                           + [f"{v:.4f}" for v in cp])

        return raw_path, sum_path