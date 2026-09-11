"""
build.py

Builds the standalone dashboard and assembles the handover folder.

    python build.py

Checks the source tree first, because PyInstaller will happily produce a
build that is missing data files and only fails when someone runs it.
"""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
DIST = HERE / "dist" / "AFD-Dashboard"


def check_sources():
    """Fail early rather than shipping a broken build."""
    problems = []

    xf = HERE / "assets" / "xfoil"
    if not xf.exists():
        problems.append("assets/xfoil is missing")
    else:
        pol = list(xf.glob("*.pol"))
        cps = list(xf.glob("cp_*.dat"))
        if not pol:
            problems.append("no .pol file in assets/xfoil")
        if len(cps) != 41:
            problems.append(f"expected 41 Cp files, found {len(cps)}")

    if not (HERE / "calibration.json").exists():
        problems.append("calibration.json is missing")

    for f in ("dashboard.py", "sensors.py", "taps.py", "xfoil_data.py",
              "serial_link.py", "logger.py", "paths.py"):
        if not (HERE / f).exists():
            problems.append(f"{f} is missing")

    return problems


def main():
    print("Checking sources...")
    problems = check_sources()
    if problems:
        for p in problems:
            print(f"  ERROR  {p}")
        return 1
    print("  ok\n")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:")
        print("    pip install pyinstaller")
        return 1

    # A stale build directory is a common cause of confusing failures
    for d in ("build", "dist"):
        p = HERE / d
        if p.exists():
            print(f"Removing old {d}/")
            shutil.rmtree(p)

    print("\nBuilding...\n")
    r = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "AFD-Dashboard.spec",
         "--noconfirm"],
        cwd=HERE)
    if r.returncode != 0:
        print("\nBuild failed")
        return r.returncode

    if not DIST.exists():
        print(f"\nBuild reported success but {DIST} does not exist")
        return 1

    # Put an editable calibration beside the executable. The bundled copy is
    # only a fallback; this one is what the program will actually read, and
    # it can be replaced without rebuilding.
    shutil.copy(HERE / "calibration.json", DIST / "calibration.json")
    (DIST / "logs").mkdir(exist_ok=True)

    readme = DIST / "README.txt"
    readme.write_text(
        "Airfoil Performance Demonstrator - dashboard\n"
        "===========================================\n"
        "\n"
        "Run AFD-Dashboard.exe. No installation is required and Python does\n"
        "not need to be present.\n"
        "\n"
        "Connecting\n"
        "----------\n"
        "Plug the Nucleo board into a USB port and switch on the 60 V supply\n"
        "for the stepper driver. Choose the ST-LINK virtual COM port from the\n"
        "dropdown and press Connect.\n"
        "\n"
        "With nothing connected the dashboard runs in emulation mode: tap\n"
        "readings are generated from the XFOIL reference data so the display\n"
        "can be demonstrated without the rig.\n"
        "\n"
        "Before taking data\n"
        "------------------\n"
        "Allow fifteen minutes for the sensors to reach a steady temperature,\n"
        "then press Tare with the wind off. The tare must be repeated if the\n"
        "supply voltage or the temperature changes. Re-tare between long runs.\n"
        "\n"
        "Files\n"
        "-----\n"
        "calibration.json   sensor sensitivities, one per channel, in ADC\n"
        "                   counts per pascal. Replace this file to apply a\n"
        "                   new calibration; no rebuild is needed.\n"
        "\n"
        "logs\\              recorded runs. Each run writes two files: the\n"
        "                   full sample record, and a summary averaged over\n"
        "                   the samples taken while the rig was settled at\n"
        "                   each angle.\n"
        "\n"
        "Known limitations\n"
        "-----------------\n"
        "Some Windows 11 systems have Smart App Control enabled, which blocks\n"
        "executables that are not code-signed. If the program will not start,\n"
        "the Python source is supplied alongside this folder and can be run\n"
        "directly:\n"
        "\n"
        "    pip install PySide6 pyqtgraph pyserial numpy\n"
        "    python dashboard.py\n"
        "\n"
        "The XFOIL reference curve is drawn dotted beyond plus or minus 12\n"
        "degrees. The panel method cannot represent stall, so lift continues\n"
        "to rise in that region instead of breaking. Measurements, not the\n"
        "reference curve, are valid there.\n"
        "\n"
        "Channel 23 (mux B, channel 11) has a sensitivity 45 percent above\n"
        "the others, consistent with reduced attenuation at its input. The\n"
        "calibration accounts for it and the channel reads correctly.\n"
    )

    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"\nBuilt {DIST}")
    print(f"  {total/1e6:.0f} MB, {sum(1 for _ in DIST.rglob('*'))} files")
    print("\nBefore handing over, test the build on a machine without Python.")
    return 0


if __name__ == "__main__":
    sys.exit(main())