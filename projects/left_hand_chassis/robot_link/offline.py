"""Load the official pure-Python serial wheel without pip or network access."""
import hashlib
from pathlib import Path
import sys

WHEEL = "pyserial-3.5-py2.py3-none-any.whl"
SHA256 = "c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0"


def load_serial():
    wheel = Path(__file__).resolve().parent.parent / "offline_deps" / WHEEL
    if wheel.is_file():
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError("Offline pySerial wheel hash mismatch; copy the complete verified package again")
        if str(wheel) not in sys.path:
            sys.path.insert(0, str(wheel))
    import serial
    if not hasattr(serial, "Serial"):
        raise RuntimeError("The serial module is not pySerial")
    return serial
