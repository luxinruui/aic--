"""One exclusive UART; measured AIC 0x07/0x5F arms + proven chassis codec."""
import struct
import sys
import threading

from robot_link.codec import encode_mode, encode_move

SLOTS = ("1", "2", "3", "4", "20", "21", "31", "32", "33", "34")
DYNAMIC = {"1", "3", "4", "31", "33", "34"}


def encode_arms(positions, duration_ms=50, include_fixed=False):
    if not isinstance(positions, dict) or not positions or not set(positions) <= DYNAMIC:
        raise ValueError("Expected selected moving servo IDs only")
    if any(type(v) is not int or not 1 <= v <= 4095 for v in positions.values()):
        raise ValueError("Invalid measured servo position")
    if type(duration_ms) is not int or duration_ms != 50 or type(include_fixed) is not bool:
        raise ValueError("Preserve approved 50ms arm segments")
    values = dict.fromkeys(SLOTS, 0)  # vendor zero means HOLD, including waist
    if include_fixed:
        values.update({"2": 2048, "32": 2048})
    values.update(positions)
    payload = struct.pack("<11H", *(values[k] for k in SLOTS), duration_ms)
    body = bytes((0x07, 0x5f, len(payload)))+payload
    return b"\xf5\x5f"+body+bytes(((~sum(body)) & 255,))


class Bus:
    def __init__(self, profile, drive=False, serial_factory=None):
        self.profile, self.hardware = profile, bool(drive)
        self.lock = threading.RLock()
        self.serial = None
        self.closed = self.faulted = self.fixed_sent = False
        self.frames_written = 0
        if not drive:
            if serial_factory is not None:
                raise ValueError("Fake or real serial factory requires explicit drive mode")
            return
        if serial_factory is None:
            if not sys.platform.startswith("linux"):
                raise RuntimeError("Hardware UART runs on the Linux backpack")
            from robot_link.offline import load_serial
            serial_factory = load_serial().Serial
        self.serial = serial_factory(port=profile.chassis["serial_port"], baudrate=115200, bytesize=8,
            parity="E", stopbits=1, timeout=.1, write_timeout=.1, exclusive=True)
        try:
            self.write_chassis((0, 0, 0))
            self.send(encode_mode())
            self.write_chassis((0, 0, 0))
        except Exception:
            self.close()
            raise

    def send(self, frame):
        with self.lock:
            if self.closed or self.faulted:
                raise RuntimeError("Shared UART closed/faulted; motion is never retried")
            if not self.hardware:
                return
            try:
                count = self.serial.write(frame)
                if type(count) is not int or count != len(frame):
                    raise IOError("Incomplete UART frame; no retry")
                self.frames_written += 1
            except Exception:
                self.faulted = True
                try:
                    self.serial.close()
                finally:
                    raise

    def write_chassis(self, command):
        self.send(encode_move(*command, duration_ms=self.profile.chassis["duration_ms"]))

    def write_arms(self, positions):
        with self.lock:
            self.send(encode_arms(positions, include_fixed=not self.fixed_sent))
            self.fixed_sent = True

    def close(self):
        with self.lock:
            if self.closed:
                return
            try:
                if not self.faulted:
                    self.write_chassis((0, 0, 0))
            finally:
                self.closed = True
                if self.serial is not None:
                    self.serial.close()


class ChassisOutput:
    def __init__(self, bus): self.bus = bus
    def write(self, command): self.bus.write_chassis(command)
    def close(self): pass  # only the combined owner closes the shared port
