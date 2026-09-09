"""
xfoil_data.py

Loads the XFOIL reference data for the Airfoil Performance Demonstrator.

Two things get loaded once at startup and then held in memory:

  * the polar  (alpha, CL, CD, CM)          -> the CL vs alpha reference curve
  * 41 Cp files (one per degree, -20..+20)  -> the Cp vs x/c reference curve

Nothing reads from disk during a run.

Run this file directly to check the data:

    python xfoil_data.py
"""

from pathlib import Path
import numpy as np

from paths import bundled


# --- Where the data lives ---------------------------------------------------
# Relative to this file, so the folder can be moved or packaged with PyInstaller
# without anything breaking.

XFOIL_DIR = bundled("assets/xfoil")
POLAR_FILE = "naca0018_Re380k.pol"

# XFOIL's integral boundary-layer method cannot produce stall. Past this angle
# the polar keeps climbing instead of dropping, so the data is not physical.
# The dashboard should draw beyond this dashed, or not at all.
VALID_ALPHA = 12.0


# --- Polar ------------------------------------------------------------------

def load_polar(path=None):
    """
    Read an XFOIL PACC polar file.

    Handles the two quirks of a two-pass sweep: the 0.000 row appears twice
    (once at the start of each pass) and the rows are not in ascending order.

    Returns a dict of numpy arrays: alpha, cl, cd, cdp, cm.
    """
    path = Path(path) if path else XFOIL_DIR / POLAR_FILE

    rows = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) != 7:
            continue
        try:
            rows.append([float(p) for p in parts])
        except ValueError:
            continue          # header or separator line

    if not rows:
        raise ValueError(f"No data rows found in {path}")

    data = np.array(rows)

    # Drop duplicate angles, then sort ascending
    _, keep = np.unique(data[:, 0], return_index=True)
    data = data[np.sort(keep)]
    data = data[np.argsort(data[:, 0])]

    return {
        "alpha": data[:, 0],
        "cl":    data[:, 1],
        "cd":    data[:, 2],
        "cdp":   data[:, 3],
        "cm":    data[:, 4],
    }


# --- Cp distributions -------------------------------------------------------

def load_cp(path):
    """
    Read one XFOIL CPWR file.

    CPWR writes either two columns (x, Cp) or three (x, y, Cp) depending on
    version and settings, so the column count is detected rather than assumed.

    Returns (x, cp) as numpy arrays.
    """
    path = Path(path)

    rows = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) not in (2, 3):
            continue
        try:
            rows.append([float(p) for p in parts])
        except ValueError:
            continue          # header line

    if not rows:
        raise ValueError(f"No data rows found in {path}")

    data = np.array(rows)
    x  = data[:, 0]
    cp = data[:, -1]          # last column is Cp in both layouts
    return x, cp


def cp_filename(angle):
    """Map an integer angle to its filename. Negatives use 'am' not a minus."""
    a = int(angle)
    return f"cp_a{a}.dat" if a >= 0 else f"cp_am{abs(a)}.dat"


def load_cp_set(folder=None):
    """
    Load every Cp file present into a dict keyed by integer angle.

    Missing files are skipped rather than raising, so a partial dataset still
    starts the dashboard. Check the returned keys to see what you actually got.
    """
    folder = Path(folder) if folder else XFOIL_DIR

    out = {}
    for a in range(-20, 21):
        f = folder / cp_filename(a)
        if f.exists():
            out[a] = load_cp(f)
    return out


# --- Lookup with interpolation ---------------------------------------------

def cp_at(cp_set, angle):
    """
    Cp distribution at any angle, interpolating between the two nearest files.

    The rig can sit at 12.4 deg but the files are at whole degrees, so without
    this the reference curve visibly jumps as the airfoil moves.

    Returns (x, cp).
    """
    lo = int(np.floor(angle))
    hi = int(np.ceil(angle))

    if lo not in cp_set and hi not in cp_set:
        raise KeyError(f"No Cp data near {angle} deg")
    if lo == hi or hi not in cp_set:
        return cp_set[lo]
    if lo not in cp_set:
        return cp_set[hi]

    x_lo, cp_lo = cp_set[lo]
    x_hi, cp_hi = cp_set[hi]

    t = angle - lo
    # Both files share the same panel x-positions, so a straight blend is safe
    return x_lo, (1.0 - t) * cp_lo + t * cp_hi


def cl_at(polar, angle):
    """XFOIL CL at any angle, interpolated from the polar."""
    return float(np.interp(angle, polar["alpha"], polar["cl"]))


# --- Self check -------------------------------------------------------------

if __name__ == "__main__":
    print(f"Looking in: {XFOIL_DIR}")
    print()

    polar = load_polar()
    a = polar["alpha"]
    print(f"Polar: {len(a)} points, {a.min():.2f} to {a.max():.2f} deg")

    # Lift curve slope over the linear region, as a sanity check.
    # Thin-airfoil theory gives 0.1097 per degree.
    mask = (a >= -5) & (a <= 5)
    if mask.sum() > 2:
        slope = np.polyfit(a[mask], polar["cl"][mask], 1)[0]
        print(f"       lift slope {slope:.4f} /deg  (theory 0.1097)")

    i0 = int(np.argmin(np.abs(a)))
    print(f"       CL at {a[i0]:.2f} deg = {polar['cl'][i0]:+.4f}  (should be ~0)")
    print()

    cps = load_cp_set()
    print(f"Cp files loaded: {len(cps)} of 41")

    missing = [x for x in range(-20, 21) if x not in cps]
    if missing:
        print(f"       MISSING: {missing}")
    else:
        print("       none missing")

    if cps:
        k = sorted(cps)[0]
        x, cp = cps[k]
        print(f"       {k:+d} deg: {len(x)} points, "
              f"x {x.min():.3f}..{x.max():.3f}, "
              f"Cp {cp.min():+.3f}..{cp.max():+.3f}")
    print()

    # Interpolation check
    if 12 in cps and 13 in cps:
        _, c12 = cps[12]
        _, c13 = cps[13]
        _, c125 = cp_at(cps, 12.5)
        ok = np.allclose(c125, 0.5 * (c12 + c13))
        print(f"Interpolation at 12.5 deg: {'ok' if ok else 'FAILED'}")

    print(f"CL at 7.5 deg (interpolated): {cl_at(polar, 7.5):+.4f}")
    print()
    print(f"Reminder: only trust +/-{VALID_ALPHA:.0f} deg and inward. "
          "XFOIL does not stall.")