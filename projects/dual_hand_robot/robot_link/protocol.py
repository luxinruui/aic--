"""Bounded JSONL over TCP. Challenge age needs no synchronized device clocks."""
from dataclasses import dataclass
import ipaddress
import json
import math
import socket
import time

APP, VERSION, PORT = "aic-left-hand-chassis", 1, 8768
MAX_BYTES, MAX_AGE_MS = 2048, 300.0


class ProtocolError(ValueError):
    pass


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def host_address(value):
    value = value.strip()
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError:
        raise ValueError("请输入目标完整IPv4（背包或米联客），例如172.20.10.3；不要填写电脑本机IP")


def unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ProtocolError("Duplicate JSON key")
        obj[key] = value
    return obj


def bad_constant(value):
    raise ProtocolError("Nonfinite JSON number")


class JsonLines:
    def __init__(self, connection):
        self.connection, self.pending = connection, bytearray()

    def send(self, message):
        data = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        if len(data) > MAX_BYTES:
            raise ProtocolError("Message exceeds 2048 bytes")
        self.connection.sendall(data + b"\n")

    def read(self, timeout=.3):
        deadline = time.monotonic() + timeout
        while True:
            end = self.pending.find(b"\n")
            if end >= 0:
                if end > MAX_BYTES:
                    raise ProtocolError("Message too long")
                raw = bytes(self.pending[:end])
                del self.pending[:end + 1]
                try:
                    message = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=bad_constant)
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise ProtocolError(str(error))
                if not isinstance(message, dict):
                    raise ProtocolError("Expected a JSON object")
                return message
            if len(self.pending) > MAX_BYTES:
                raise ProtocolError("Message too long")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("Message timed out")
            self.connection.settimeout(remaining)
            chunk = self.connection.recv(MAX_BYTES + 1)
            if not chunk:
                raise EOFError("Connection closed")
            self.pending.extend(chunk)


@dataclass(frozen=True)
class Intent:
    seq: int
    generation: int
    input_session: str
    frame_id: int
    enabled: bool
    source: str
    forward: float
    yaw_left: float
    age_ms: float


def validate(message, session, challenge, issued, last_seq, now, profile):
    keys = {"app", "v", "type", "profile", "session", "challenge", "seq", "generation",
            "input_session", "frame_id", "enabled", "source", "forward", "yaw_left", "age_ms"}
    if set(message) != keys or message.get("app") != APP or type(message.get("v")) is not int or message["v"] != VERSION or message["type"] != "intent":
        raise ProtocolError("Wrong hand-chassis application/schema/version")
    if message["profile"] != profile:
        raise ProtocolError("robot_config.json mismatch")
    if message["session"] != session or message["challenge"] != challenge:
        raise ProtocolError("Wrong or consumed session/challenge")
    if not finite(now) or not finite(issued) or not 0 <= now - issued <= .3:
        raise ProtocolError("Challenge expired")
    for field in ("seq", "generation", "frame_id"):
        if type(message[field]) is not int or not 0 <= message[field] < 2**53:
            raise ProtocolError("Invalid " + field)
    if message["seq"] <= last_seq:
        raise ProtocolError("Replayed sequence")
    if type(message["enabled"]) is not bool or message["source"] not in ("camera", "demo", "none"):
        raise ProtocolError("Invalid enable/source")
    if not isinstance(message["input_session"], str) or not 16 <= len(message["input_session"]) <= 128:
        raise ProtocolError("Invalid input session")
    for field in ("forward", "yaw_left"):
        if not finite(message[field]) or not -1 <= message[field] <= 1:
            raise ProtocolError("Invalid normalized target")
    if not message["enabled"] and (message["forward"] != 0 or message["yaw_left"] != 0):
        raise ProtocolError("OFF contains movement")
    if message["enabled"] and (message["source"] == "none" or message["frame_id"] == 0):
        raise ProtocolError("Enabled target has no input")
    age = message["age_ms"]
    if not finite(age) or not 0 <= age <= MAX_AGE_MS:
        raise ProtocolError("Invalid sample age")
    age += (now - issued) * 1000
    if age > MAX_AGE_MS:
        raise ProtocolError("Combined sample/transport age expired")
    return Intent(message["seq"], message["generation"], message["input_session"], message["frame_id"],
                  message["enabled"], message["source"], float(message["forward"]), float(message["yaw_left"]), age)


def check_reply(reply, kind, profile, session=None, seq=None):
    if reply.get("via","direct") not in ("direct","mlk-relay"):
        raise ProtocolError("Unknown control path")
    if reply.get("via")=="mlk-relay" and (type(reply.get("relay_armed")) is not bool or
            type(reply.get("downstream_connected")) is not bool):
        raise ProtocolError("Invalid MLK relay status")
    if (reply.get("app") != APP or type(reply.get("v")) is not int or reply["v"] != VERSION or
            reply.get("type") != kind or reply.get("profile") != profile):
        raise ProtocolError("背包程序或robot_config.json不匹配")
    if type(reply.get("hardware")) is not bool:
        raise ProtocolError("Receiver did not identify hardware mode")
    for key in ("session", "challenge"):
        if not isinstance(reply.get(key), str) or not 16 <= len(reply[key]) <= 128:
            raise ProtocolError("Invalid receiver session/challenge")
    if session is not None and reply["session"] != session:
        raise ProtocolError("Receiver session changed")
    if type(reply.get("seq")) is not int or (seq is not None and reply["seq"] != seq):
        raise ProtocolError("Wrong acknowledgement sequence")
    if reply.get("state") not in ("DISARMED", "READY", "MOVING", "FAULT", "CLOSED"):
        raise ProtocolError("Invalid receiver state")
    if type(reply.get("generation")) is not int or type(reply.get("rearm_required")) is not bool:
        raise ProtocolError("Invalid receiver enable state")
    return reply["session"], reply["challenge"]
