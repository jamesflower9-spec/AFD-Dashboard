"""
paths.py

Where files live, whether running from source or from a PyInstaller build.

A frozen executable unpacks its bundled data into a temporary folder that is
deleted on exit, so the two cases have to be kept apart:

  bundled()     read-only data shipped inside the executable. The XFOIL
                reference files never change, so they travel with it.

  beside_exe()  files the operator needs to see, edit or keep. These sit next
                to the .exe in a normal folder. Logs written into the
                temporary folder would disappear the moment the program
                closed, and a replacement calibration file dropped in there
                would never be read.

The split matters for handover: the department can swap calibration.json or
collect logs without rebuilding anything.
"""

import sys
from pathlib import Path


def _frozen():
    return getattr(sys, "frozen", False)


def bundled(rel):
    """Read-only data packaged with the program."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / rel
    return Path(__file__).parent / rel


def beside_exe(rel):
    """Files that live alongside the executable and persist between runs."""
    if _frozen():
        return Path(sys.executable).parent / rel
    return Path(__file__).parent / rel


def calibration_file():
    """
    Calibration, preferring a copy next to the executable.

    A default is bundled so the program runs out of the box, but a file placed
    beside the .exe overrides it. That is how the rig gets recalibrated after
    a sensor is replaced, without anyone needing Python installed.
    """
    external = beside_exe("calibration.json")
    if external.exists():
        return external
    return bundled("calibration.json")