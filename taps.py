"""
taps.py

Pressure tap geometry for the demonstrator, and the maths that turns 24 tap
readings into a lift coefficient.

Kept separate from the GUI so it can be tested on its own:

    python taps.py
"""

import numpy as np

# numpy renamed trapz to trapezoid in 2.0; accept either
_trapz = getattr(np, "trapezoid", None) or np.trapz


# --- Geometry ---------------------------------------------------------------

CHORD_MM = 278.0

N_UPPER = 12
N_LOWER = 12
N_TAPS = N_UPPER + N_LOWER

# Aft limit. Past about 0.91c the skin is too thin to drill and fit a stub.
X_FIRST = 0.010
X_LAST = 0.910


def tap_positions():
    """
    Chordwise tap positions as fractions of chord.

    Cosine spacing, so taps cluster towards the leading edge where the suction
    peak is. An even spacing would smear the peak and the measured CL would
    come out low.

    Returns (x_upper, x_lower), each ascending from the leading edge.
    """
    beta = np.linspace(0.0, np.pi / 2, N_UPPER)
    frac = 1.0 - np.cos(beta)          # 0 at LE, 1 at the aft limit
    x_upper = X_FIRST + (X_LAST - X_FIRST) * frac

    beta = np.linspace(0.0, np.pi / 2, N_LOWER)
    frac = 1.0 - np.cos(beta)
    x_lower = X_FIRST + (X_LAST - X_FIRST) * frac

    return x_upper, x_lower


# --- Splitting an XFOIL Cp file into two surfaces ---------------------------

def split_surfaces(x, cp):
    """
    XFOIL writes Cp starting at the trailing edge, forward over the upper
    surface to the leading edge, then back along the lower surface. So x runs
    1 -> 0 -> 1 and the minimum marks the leading edge.

    Returns (x_up, cp_up, x_lo, cp_lo), each ascending from the leading edge.
    """
    le = int(np.argmin(x))

    if le == 0 or le == len(x) - 1:
        raise ValueError(
            "Cp data does not fold back at the leading edge - "
            "unexpected file ordering"
        )

    x_up = x[:le + 1][::-1]            # reverse so it ascends from the LE
    cp_up = cp[:le + 1][::-1]
    x_lo = x[le:]
    cp_lo = cp[le:]

    return x_up, cp_up, x_lo, cp_lo


def sample_at_taps(x, cp):
    """
    Interpolate a full XFOIL Cp distribution down to the 24 tap positions.

    This is what the sensors would read in an ideal world, so it also serves as
    the synthetic data source while the real sensors are on order.
    """
    x_up, cp_up, x_lo, cp_lo = split_surfaces(x, cp)
    xt_up, xt_lo = tap_positions()

    return np.interp(xt_up, x_up, cp_up), np.interp(xt_lo, x_lo, cp_lo)


# --- Lift from measured pressures ------------------------------------------

def cl_from_taps(cp_upper, cp_lower, alpha_deg):
    """
    Integrate the tap pressures to get a lift coefficient.

    Normal force comes from the area between the lower and upper Cp curves:

        CN = integral of (Cp_lower - Cp_upper) d(x/c)

    then CL = CN cos(alpha), ignoring the axial force term. Two approximations
    worth stating in the report:

      * the axial term needs the surface slope and is small at these angles
      * the taps stop at 0.91c, so the last 9% of chord is extrapolated flat

    Both push the answer slightly low, which is the honest direction to err.
    """
    xt_up, xt_lo = tap_positions()

    # Extend to the trailing edge holding the last value, and to the leading
    # edge where upper and lower must meet at the stagnation point.
    x_u = np.concatenate(([0.0], xt_up, [1.0]))
    c_u = np.concatenate(([cp_upper[0]], cp_upper, [cp_upper[-1]]))
    x_l = np.concatenate(([0.0], xt_lo, [1.0]))
    c_l = np.concatenate(([cp_lower[0]], cp_lower, [cp_lower[-1]]))

    # Put both on a common grid before subtracting
    grid = np.linspace(0.0, 1.0, 200)
    cn = _trapz(np.interp(grid, x_l, c_l) - np.interp(grid, x_u, c_u), grid)

    return float(cn * np.cos(np.radians(alpha_deg)))


# --- Self check -------------------------------------------------------------

if __name__ == "__main__":
    from xfoil_data import load_cp_set, XFOIL_DIR

    xt_up, xt_lo = tap_positions()
    print(f"Chord {CHORD_MM:.0f} mm, {N_TAPS} taps")
    print("Upper x/c:", np.array2string(xt_up, precision=3))
    print("       mm:", np.array2string(xt_up * CHORD_MM, precision=1))
    print()

    cps = load_cp_set()
    if not cps:
        print(f"No Cp files found in {XFOIL_DIR}")
        raise SystemExit(1)

    print(" alpha   CL(taps)   suction peak   upper mean   lower mean")
    print(" -----   --------   ------------   ----------   ----------")
    for a in [0, 5, 10, 12]:
        if a not in cps:
            continue
        x, cp = cps[a]
        cu, cl_ = sample_at_taps(x, cp)
        print(f" {a:5d}   {cl_from_taps(cu, cl_, a):8.4f}   "
              f"{cu.min():12.3f}   {cu.mean():10.3f}   {cl_.mean():10.3f}")

    print()
    print("At positive alpha the upper mean must be the more negative of the")
    print("two. If it is not, the surfaces are swapped and split_surfaces()")
    print("needs its two halves exchanged.")