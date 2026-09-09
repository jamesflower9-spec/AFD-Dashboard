"""
sensors.py

Turns raw ADC counts from the STM32 into pressure coefficients.

The chain is:

    counts  ->  (counts - tare) / slope  ->  Pa  ->  Cp = sign * Pa / q

Two numbers per channel, and they come from different places on purpose:

  slope   from bench calibration against a reference pressure source.
          A property of the sensor and its divider. Stable.

  tare    captured live, wind-off, at the start of a session.
          Offset moves with temperature and supply voltage, so a stored
          offset goes stale. Taking it fresh also absorbs any error in the
          5 V rail, since these sensors are ratiometric.

Run this file to refit the calibration from measured data:

    python sensors.py
"""

import json
from pathlib import Path
import numpy as np


CAL_FILE = Path(__file__).parent / "calibration.json"

N_TAPS = 24

# Datasheet transfer function with the 10k/22k divider gives 0.3839 counts/Pa.
# Measured channels come out around 0.3786, about 1.4% below that, which is
# well inside the part's gain tolerance. Used for channels not yet calibrated.
NOMINAL_SLOPE = 0.3786

# Sign of Cp relative to the measured differential.
#
# The tap tube goes to the sensor's low-pressure port and the tunnel static
# reference to the high-pressure port. That orientation is deliberate: the
# sensor output cannot go below 0 V, which puts a floor near -444 Pa on the
# differential, and a suction peak at 20 m/s wants -980 Pa. Reversing the
# ports puts the large excursion on the side with range to spare.
#
# The consequence is that suction raises the reading, so a rising count means
# a falling Cp. Confirm this once with the tunnel running: at a few degrees
# positive incidence the upper surface must read negative Cp.
CP_SIGN = -1.0

AIR_DENSITY = 1.225


# --- Fitting ----------------------------------------------------------------

def fit_channel(pressures_pa, counts):
    """
    Least-squares fit of counts against applied pressure.

    Returns (slope, offset, rms_error_pa). The offset is returned for
    inspection only - the live tare is what gets used at runtime.
    """
    p = np.asarray(pressures_pa, dtype=float)
    c = np.asarray(counts, dtype=float)

    if len(p) < 2:
        raise ValueError("need at least two points")

    slope, offset = np.polyfit(p, c, 1)
    resid_pa = (c - (offset + slope * p)) / slope
    return float(slope), float(offset), float(np.sqrt((resid_pa ** 2).mean()))


# --- Calibration file -------------------------------------------------------

def default_calibration():
    return {
        "cp_sign": CP_SIGN,
        "nominal_slope": NOMINAL_SLOPE,
        "slopes": {},          # {"0": 0.3798, ...} keyed by tap index as text
        "notes": "slope in ADC counts per pascal; offset comes from live tare",
    }


def load_calibration(path=None):
    path = Path(path) if path else CAL_FILE
    if not path.exists():
        return default_calibration()
    with open(path) as f:
        return json.load(f)


def save_calibration(cal, path=None):
    path = Path(path) if path else CAL_FILE
    with open(path, "w") as f:
        json.dump(cal, f, indent=2)


def slope_array(cal):
    """
    Slopes for all 24 taps, filling uncalibrated channels with the nominal.

    Returns (slopes, calibrated_mask) so the dashboard can show which
    channels are measured and which are assumed.
    """
    nominal = cal.get("nominal_slope", NOMINAL_SLOPE)
    slopes = np.full(N_TAPS, nominal, dtype=float)
    done = np.zeros(N_TAPS, dtype=bool)

    for k, v in cal.get("slopes", {}).items():
        i = int(k)
        if 0 <= i < N_TAPS:
            slopes[i] = float(v)
            done[i] = True

    return slopes, done


# --- Runtime conversion -----------------------------------------------------

class TapConverter:
    """
    Holds the calibration and the current tare, and converts scans to Cp.

    Tap index 0..11 are the upper surface, 12..23 the lower, matching the
    order the firmware sends them.
    """

    def __init__(self, cal=None):
        self.cal = cal if cal is not None else load_calibration()
        self.slopes, self.calibrated = slope_array(self.cal)
        self.sign = float(self.cal.get("cp_sign", CP_SIGN))
        self.tare = np.zeros(N_TAPS, dtype=float)
        self.tared = False

    def set_tare(self, counts):
        """Take the current reading as zero differential. Wind off."""
        self.tare = np.asarray(counts, dtype=float).copy()
        self.tared = True

    def pressures(self, counts):
        """Counts to pascals, tare applied."""
        c = np.asarray(counts, dtype=float)
        return (c - self.tare) / self.slopes

    def cp(self, counts, wind_ms):
        """
        Counts to pressure coefficient.

        Returns (cp_upper, cp_lower), 12 values each. Below about 3 m/s the
        dynamic pressure is too small to divide by meaningfully, so zeros are
        returned rather than a very large number.
        """
        q = 0.5 * AIR_DENSITY * wind_ms ** 2
        if q < 5.0:
            return np.zeros(12), np.zeros(12)

        cp = self.sign * self.pressures(counts) / q
        return cp[:12], cp[12:]


# --- Refit from measured data ----------------------------------------------

# Bench calibration, applied pressure in Pa against ADC counts.
#
# Tap 0  = mux A channel 0 = upper surface, leading edge
# Tap 12 = mux B channel 0 = lower surface, leading edge
#
# Mux B C0 was measured twice; both runs are included so the fit uses all
# the evidence. Add channels here as they are measured, then rerun this file.
MEASURED = {
    0:  ([381, 590, 882, 1610],
         [372, 452, 562,  839]),

    12: ([0,  33, 183, 475, 649, 987,     0,  34, 174, 505, 709, 978],
         [219, 234, 290, 400, 465, 595,  219, 235, 287, 410, 487, 590]),
}


if __name__ == "__main__":
    cal = default_calibration()

    print("Fitting measured channels\n")
    print(" tap   slope       Pa/count   offset    rms")
    print(" ---   ---------   --------   ------   -----")

    all_slopes = []
    for tap in sorted(MEASURED):
        p, c = MEASURED[tap]
        slope, offset, rms = fit_channel(p, c)
        cal["slopes"][str(tap)] = round(slope, 5)
        all_slopes.append(slope)
        print(f" {tap:3d}   {slope:.5f}   {1/slope:8.3f}   {offset:6.1f}   "
              f"{rms:5.1f} Pa")

    cal["nominal_slope"] = round(float(np.mean(all_slopes)), 5)
    save_calibration(cal)

    print(f"\nMean slope {cal['nominal_slope']:.5f} counts/Pa, "
          f"used for the {N_TAPS - len(all_slopes)} channels not yet measured.")
    print(f"Written to {CAL_FILE}")

    # What the resolution means in Cp terms
    print("\nOne ADC count in Cp, by wind speed:")
    for v in (10, 15, 20):
        q = 0.5 * AIR_DENSITY * v * v
        print(f"   {v:2d} m/s   {1/cal['nominal_slope']/q:.4f} Cp per count")
    print("\nAveraging 200 scans divides the random part of that by ~14.")