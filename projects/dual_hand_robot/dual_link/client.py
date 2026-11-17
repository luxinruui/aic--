"""One latest PC snapshot; OFF / matching prepare / ACTIVE; no auto reconnect."""
import socket
import time

from robot_link.client import Client as ChassisClient
from .profile import AXES, GROUPS
from .protocol import APP, VERSION, PORT, JsonLines, check_reply


class Client(ChassisClient):
    def __init__(self, host, profile, port=PORT):
        super().__init__(host, profile.chassis, port)
        self.profile = profile.signature
        self.info.update(baseline_confirmed=False, arm_reference=None, arm_state="HOLD")

    def packet(self, session, challenge, seq, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            snap = self.latest
            packet = dict(app=APP, v=VERSION, type="intent", profile=self.profile, session=session,
                challenge=challenge, seq=seq, generation=self.generation, input_session="0"*32, frame_id=0,
                enabled=False, source="none", age_ms=0., stage="off", mode="arms", forward=0., yaw_left=0.,
                targets=dict.fromkeys(AXES), groups=[False]*5)
            if snap is None:
                return packet
            packet["mode"] = snap["mode"]
            elapsed = now-snap["produced_at"]
            age = snap["sample_age_ms"]+elapsed*1000
            wanted = bool(snap["enabled"] and snap["input_valid"])
            if wanted and not 0 <= elapsed <= .15:
                self.inhibit("电脑窗口快照超过150毫秒，请暂停后重新启用")
            if wanted and not 0 <= age < 300:
                self.inhibit("所需手部采样过期，请暂停后重新启用")
            if wanted and self.info["hardware"] and snap["source"] != "camera":
                self.inhibit("实机拒绝合成演示输入")
            if wanted and snap["mode"] != "chassis" and not self.info["baseline_confirmed"]:
                self.inhibit("背包尚未确认原六轴基准，不能启用上肢")
            if not wanted:
                self.wanted, self.phase = False, "OFF"
            if wanted and not self.inhibited:
                if not self.wanted:
                    self.generation += 1
                    self.wanted, self.phase = True, "ARMING"
                packet.update(enabled=True, generation=self.generation, input_session=snap["session"],
                              frame_id=snap["frame_id"], source=snap["source"], age_ms=age,
                              groups=list(snap["groups"]), stage="prepare")
                if snap["mode"] in ("arms", "both"):
                    reference = self.info.get("arm_reference") or {}
                    packet["targets"] = {name: reference.get(name) if active else None
                        for active, group in zip(snap["groups"],GROUPS) for name in group}
                if self.phase == "ACTIVE":
                    packet.update(stage="active", forward=snap["forward"], yaw_left=snap["yaw_left"],
                                  targets=dict(snap["targets"]))
            return packet

    def accept_ack(self, reply):
        with self.lock:
            self.info.update(connected=True, state=reply["state"], hardware=reply["hardware"], ack_seq=reply["seq"],
                generation=reply["generation"], reason=reply.get("reason", ""), command=reply.get("command", [0,0,0]),
                baseline_confirmed=reply["baseline_confirmed"], arm_reference=dict(reply["arm_reference"]),
                arm_state=reply["arm_state"], arm_segments=reply.get("arm_segments",0))
            if self.wanted and not self.inhibited:
                if reply["generation"] != self.generation or reply["rearm_required"]:
                    self.inhibit("背包已暂停："+reply.get("reason", "请重新启用"))
                elif self.phase == "ARMING" and reply["state"] == "READY":
                    self.phase = "ACTIVE"

    def run(self):
        try:
            with socket.create_connection((self.host,self.port), timeout=1.) as connection:
                self.connection = connection
                connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                wire = JsonLines(connection)
                hello = wire.read(timeout=1.)
                session, challenge = check_reply(hello,"hello",self.profile,seq=0)
                self.accept_ack(hello)
                packet = self.packet(session,challenge,1)
                packet.update(enabled=False,stage="off",forward=0.,yaw_left=0.,targets=dict.fromkeys(AXES),
                              generation=0,frame_id=0,age_ms=0.,source="none",groups=[False]*5)
                wire.send(packet)
                reply = wire.read()
                _, challenge = check_reply(reply,"ack",self.profile,session,1)
                with self.lock:
                    self.wanted, self.phase = False,"OFF"
                    if self.latest and self.latest.get("enabled"):
                        self.inhibit("新连接需先暂停，再明确启用")
                self.accept_ack(reply)
                seq, deadline = 1,time.monotonic()
                while not self.stop_event.is_set():
                    if self.stop_event.wait(max(0.,deadline-time.monotonic())):
                        break
                    seq += 1
                    sent = time.monotonic()
                    wire.send(self.packet(session,challenge,seq,sent))
                    reply = wire.read()
                    _, challenge = check_reply(reply,"ack",self.profile,session,seq)
                    if reply["hardware"] != hello["hardware"]:
                        raise ValueError("Receiver hardware mode changed")
                    self.accept_ack(reply)
                    deadline = max(sent+.05,time.monotonic())
        except (OSError,ValueError,EOFError) as error:
            with self.lock:
                self.info["error"] = str(error)
        finally:
            with self.lock:
                self.connection = None
                self.info.update(connected=False,state="DISCONNECTED",inhibit=True)
                self.inhibited = True
