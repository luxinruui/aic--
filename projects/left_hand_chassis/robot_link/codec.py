"""AIC vendor device 0x08, mode 0, chassis only; no arm command exists here."""
import struct
import sys
import threading


def frame(command, payload):
    body = bytes((0x08, command, len(payload))) + payload
    return b"\xf5\x5f" + body + bytes(((~sum(body)) & 255,))


def encode_move(angle, speed, turn, duration_ms=150):
    if type(angle) is not int or not 0 <= angle < 360:
        raise ValueError("Invalid direction")
    if type(speed) is not int or not 0 <= speed <= 100:
        raise ValueError("Invalid translation speed")
    if type(turn) is not int or not -100 <= turn <= 100:
        raise ValueError("Invalid signed mode-0 turn speed")
    if type(duration_ms) is not int or not 50 <= duration_ms <= 250:
        raise ValueError("Invalid finite command duration")
    return frame(0x02, struct.pack("<HHhH", angle, speed, turn, duration_ms))


def encode_mode():
    return frame(0x01, b"\x00")


class SerialWriter:
    """Only constructed by an explicit --drive. Faults latch; never retry motion."""
    def __init__(self, config, serial_factory=None):
        self.lock = threading.RLock()
        self.closed = self.faulted = False
        self.serial = None
        self.duration = config["duration_ms"]
        if serial_factory is None:
            if not sys.platform.startswith("linux"):
                raise RuntimeError("Hardware mode is supported only on the Linux backpack")
            from .offline import load_serial
            serial = load_serial()
            serial_factory = serial.Serial
        options = dict(port=config["serial_port"], baudrate=115200, bytesize=8,
                       parity="E", stopbits=1, timeout=.1, write_timeout=.1)
        if sys.platform.startswith("linux"):
            options["exclusive"] = True
        self.serial = serial_factory(**options)
        try:
            self.write((0, 0, 0))
            self._send(encode_mode())
            self.write((0, 0, 0))
        except Exception:
            self.close()
            raise

    def _send(self, data):
        with self.lock:
            if self.closed or self.faulted:
                raise RuntimeError("Chassis UART is closed or faulted")
            try:
                count = self.serial.write(data)
                if type(count) is not int or count != len(data):
                    raise IOError("Incomplete chassis frame; no retry")
            except Exception:
                self.closed = self.faulted = True
                try:
                    self.serial.close()
                finally:
                    raise

    def write(self, command):
        self._send(encode_move(*command, duration_ms=self.duration))

    def close(self):
        with self.lock:
            if self.closed:
                return
            try:
                if not self.faulted:
                    self.write((0, 0, 0))
            finally:
                self.closed = True
                if self.serial is not None:
                    self.serial.close()
