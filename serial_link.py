"""
serial_link.py

Background thread that talks to the STM32 over the ST-LINK virtual COM port.

The firmware streams status at 10 Hz and replies to commands. Reading that in
the GUI thread would stall the interface, so it lives here and reaches the GUI
through Qt signals, which are thread-safe by design.

Commands go the other way through a queue rather than being written directly,
so only this thread ever touches the serial port.

    S,<angle>,<moving>,<t1>..<t24>   status, unsolicited, 10 Hz
    OK / ERR,<reason>                reply to a command
    AFD,<version>                    reply to '?'
"""

import queue

from PySide6.QtCore import QThread, Signal
import serial
from serial.tools import list_ports


BAUD = 115200
N_TAPS = 24


def available_ports():
    """
    COM ports on this machine, best guess first.

    The Nucleo shows up as an ST-LINK virtual COM port, so those are floated
    to the top - saves hunting through Bluetooth ports every time.
    """
    ports = list(list_ports.comports())
    ports.sort(key=lambda p: ("stlink" not in (p.description or "").lower()
                              and "stm" not in (p.description or "").lower()))
    return [(p.device, f"{p.device}  -  {p.description}") for p in ports]


class SerialLink(QThread):

    status = Signal(float, bool, list)   # angle, moving, tap counts
    reply  = Signal(str)                 # OK / ERR,... / AFD,...
    opened = Signal(str)
    closed = Signal()
    failed = Signal(str)

    def __init__(self, port, parent=None):
        super().__init__(parent)
        self.port = port
        self._tx = queue.Queue()
        self._running = False

    # --- called from the GUI thread ---

    def send(self, cmd):
        """Queue a command. A newline is added if missing."""
        if not cmd.endswith("\n"):
            cmd += "\n"
        self._tx.put(cmd)

    def stop(self):
        self._running = False

    # --- runs in the background thread ---

    def run(self):
        try:
            ser = serial.Serial(self.port, BAUD, timeout=0.05)
        except serial.SerialException as e:
            self.failed.emit(str(e))
            return

        self._running = True
        self.opened.emit(self.port)

        buf = b""
        try:
            while self._running:
                while True:
                    try:
                        ser.write(self._tx.get_nowait().encode("ascii"))
                    except queue.Empty:
                        break

                chunk = ser.read(1024)
                if chunk:
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._handle(line.decode("ascii", "ignore").strip())

        except serial.SerialException as e:
            self.failed.emit(str(e))
        finally:
            try:
                ser.close()
            except Exception:
                pass
            self.closed.emit()

    def _handle(self, line):
        if not line:
            return

        if line.startswith("S,"):
            parts = line.split(",")
            # Angle and moving are always fields 1 and 2. Taps follow when the
            # firmware has them. Accepting short lines keeps this working
            # against older firmware and against the 32-channel raw mode.
            if len(parts) < 3:
                return
            try:
                angle = float(parts[1])
                moving = parts[2] == "1"
            except ValueError:
                return                      # corrupt line

            taps = []
            for p in parts[3:3 + N_TAPS]:
                try:
                    taps.append(int(p))
                except ValueError:
                    taps.append(0)

            self.status.emit(angle, moving, taps)
            return

        self.reply.emit(line)