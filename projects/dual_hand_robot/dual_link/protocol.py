"""Distinct bounded protocol on TCP8770, with the original freshness barrier."""
from dataclasses import dataclass

from robot_link.protocol import JsonLines, ProtocolError, finite, host_address
from .profile import AXES, GROUPS, MAXIMUM

APP, VERSION, PORT = "aic-dual-hand-robot", 1, 8770
MODES = ("arms", "chassis", "both")


@dataclass(frozen=True)
class Intent:
    seq: int
    generation: int
    input_session: str
    frame_id: int
    enabled: bool
    source: str
    age_ms: float
    stage: str
    mode: str
    forward: float
    yaw_left: float
    targets: dict
    groups: tuple


def validate(m, session, challenge, issued, last_seq, now, profile):
    keys = {"app", "v", "type", "profile", "session", "challenge", "seq", "generation",
            "input_session", "frame_id", "enabled", "source", "age_ms", "stage", "mode",
            "forward", "yaw_left", "targets", "groups"}
    if (not isinstance(m, dict) or set(m) != keys or m["app"] != APP or type(m["v"]) is not int
            or m["v"] != VERSION or m["type"] != "intent"):
        raise ProtocolError("Wrong dual-hand application/schema; legacy receiver is incompatible")
    if m["profile"] != profile or m["session"] != session or m["challenge"] != challenge:
        raise ProtocolError("Wrong configuration/session/challenge")
    if not finite(now) or not finite(issued) or not 0 <= now-issued <= .30:
        raise ProtocolError("Expired receiver challenge")
    for key in ("seq", "generation", "frame_id"):
        if type(m[key]) is not int or not 0 <= m[key] < 2**53:
            raise ProtocolError("Invalid "+key)
    if m["seq"] <= last_seq:
        raise ProtocolError("Replayed message")
    if type(m["enabled"]) is not bool or m["source"] not in ("camera", "demo", "none"):
        raise ProtocolError("Invalid input source/enable")
    if not isinstance(m["input_session"], str) or not 16 <= len(m["input_session"]) <= 128:
        raise ProtocolError("Invalid input session")
    if m["mode"] not in MODES or m["stage"] not in ("off", "prepare", "active"):
        raise ProtocolError("Invalid control mode/stage")
    if any(not finite(m[k]) or not -1 <= m[k] <= 1 for k in ("forward", "yaw_left")):
        raise ProtocolError("Invalid chassis target")
    targets, groups = m["targets"], m["groups"]
    if not isinstance(targets, dict) or set(targets) != set(AXES):
        raise ProtocolError("Incomplete six-axis target dictionary")
    if not isinstance(groups, list) or len(groups) != 5 or any(type(x) is not bool for x in groups):
        raise ProtocolError("Invalid five control groups")
    if not finite(m["age_ms"]) or not 0 <= m["age_ms"] <= 300:
        raise ProtocolError("Invalid source age")
    age = m["age_ms"]+(now-issued)*1000
    if age > 300:
        raise ProtocolError("Combined source and transport age exceeded 300ms")
    if not m["enabled"]:
        if m["stage"] != "off" or m["forward"] != 0 or m["yaw_left"] != 0 or any(v is not None for v in targets.values()):
            raise ProtocolError("OFF must contain zero chassis and no arm targets")
    else:
        if m["stage"] == "off" or m["source"] == "none" or m["frame_id"] == 0:
            raise ProtocolError("Enabled message lacks fresh input")
        if m["stage"] == "prepare" and (m["forward"] != 0 or m["yaw_left"] != 0):
            raise ProtocolError("Preparation may not move the chassis")
        if m["mode"] == "arms" and (m["forward"] != 0 or m["yaw_left"] != 0):
            raise ProtocolError("Arm-only mode contains chassis motion")
        has_arms = m["mode"] in ("arms", "both")
        if has_arms and not any(groups):
            raise ProtocolError("No active arm group")
        if not has_arms and any(groups):
            raise ProtocolError("Chassis-only mode contains arm groups")
        for enabled, group in zip(groups, GROUPS):
            for name in group:
                value = targets[name]
                if enabled and has_arms:
                    if not finite(value) or not 0 <= value <= MAXIMUM[name]:
                        raise ProtocolError("Invalid arm angle: "+name)
                elif value is not None:
                    raise ProtocolError("Inactive joint must have no target")
        if groups[0] and abs(targets["left_upper"]-targets["right_upper"]) > 1e-6:
            raise ProtocolError("Paired upper arms must request mirrored semantic angles")
    return Intent(m["seq"], m["generation"], m["input_session"], m["frame_id"], m["enabled"],
                  m["source"], age, m["stage"], m["mode"], float(m["forward"]), float(m["yaw_left"]),
                  dict(targets), tuple(groups))


def check_reply(reply, kind, profile, session=None, seq=None):
    if (reply.get("app") != APP or type(reply.get("v")) is not int or reply["v"] != VERSION
            or reply.get("type") != kind or reply.get("profile") != profile):
        raise ProtocolError("请运行双手机器人独立接收端；旧底盘协议不兼容")
    if type(reply.get("hardware")) is not bool or type(reply.get("baseline_confirmed")) is not bool:
        raise ProtocolError("Receiver did not identify hardware/baseline status")
    for key in ("session", "challenge"):
        if not isinstance(reply.get(key), str) or not 16 <= len(reply[key]) <= 128:
            raise ProtocolError("Invalid receiver "+key)
    if session is not None and reply["session"] != session:
        raise ProtocolError("Receiver session changed")
    if type(reply.get("seq")) is not int or (seq is not None and reply["seq"] != seq):
        raise ProtocolError("Wrong ACK sequence")
    if (reply.get("state") not in ("DISARMED", "READY", "MOVING", "FAULT", "CLOSED")
            or type(reply.get("generation")) is not int or type(reply.get("rearm_required")) is not bool):
        raise ProtocolError("Invalid receiver state")
    reference = reply.get("arm_reference")
    if not isinstance(reference, dict) or set(reference) != set(AXES):
        raise ProtocolError("Missing software arm reference")
    if any(not finite(reference[k]) or not 0 <= reference[k] <= MAXIMUM[k] for k in AXES):
        raise ProtocolError("Invalid software arm reference")
    return reply["session"], reply["challenge"]
