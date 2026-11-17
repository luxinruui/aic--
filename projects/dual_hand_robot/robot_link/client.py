"""PC thread: one latest snapshot, OFF/zero-enable barrier, no reconnect/resume."""
import copy
import socket
import threading
import time

from .config import signature
from .protocol import APP, VERSION, PORT, JsonLines, ProtocolError, check_reply, host_address


class Client:
    def __init__(self, host, config, port=PORT):
        self.host, self.profile, self.port = host_address(host), signature(config), port
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("TCP端口须在1～65535之间")
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = self.connection = None
        self.latest = None
        self.generation = 0
        self.wanted = False
        self.phase = "OFF"
        self.inhibited = False
        self.info = dict(connected=False, state="CONNECTING", hardware=None, error="", inhibit=False, ack_seq=0)

    def start(self):
        self.thread = threading.Thread(target=self.run, name="hand-chassis-tcp", daemon=True)
        self.thread.start()

    def publish(self, command):
        # A fresh command snapshot is needed even while the camera is computing.
        with self.lock:
            self.latest = command.as_dict() if hasattr(command, "as_dict") else dict(command)
            if not self.latest.get("enabled"):
                self.inhibited = False
                self.wanted = False
                self.phase = "OFF"
                self.info["inhibit"] = False

    def status(self):
        with self.lock:
            return copy.deepcopy(self.info)

    def inhibit(self, reason):
        with self.lock:
            self.inhibited = True
            self.phase = "OFF"
            self.info.update(inhibit=True, error=str(reason))

    def packet(self, session, challenge, seq, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            snapshot = self.latest
            packet = dict(app=APP, v=VERSION, type="intent", profile=self.profile, session=session,
                          challenge=challenge, seq=seq, generation=self.generation,
                          input_session="0" * 32, frame_id=0, enabled=False,
                          source="none", forward=0.0, yaw_left=0.0, age_ms=0.0)
            if snapshot is None:
                return packet
            elapsed = now - snapshot["produced_at"]
            age = snapshot["sample_age_ms"] + elapsed * 1000
            wanted = bool(snapshot["enabled"] and snapshot["input_valid"])
            if wanted and not 0 <= elapsed * 1000 <= min(150, snapshot["valid_for_ms"]):
                self.inhibit("电脑窗口快照超过150毫秒，请重新启用")
            if wanted and not 0 <= age < 300:
                self.inhibit("左手采样过期，请重新启用")
            if wanted and self.info["hardware"] and snapshot["source"] != "camera":
                self.inhibit("实机模式拒绝合成演示，请使用摄像头并重新校准")
            if not wanted:
                self.wanted = False
                self.phase = "OFF"
            if wanted and not self.inhibited:
                if not self.wanted:
                    self.generation += 1
                    self.wanted = True
                    self.phase = "ARMING"
                packet.update(generation=self.generation, enabled=True, input_session=snapshot["session"],
                              frame_id=snapshot["frame_id"], source=snapshot["source"], age_ms=age)
                if self.phase == "ACTIVE":
                    packet.update(forward=snapshot["forward"], yaw_left=snapshot["yaw_left"])
            return packet

    def accept_ack(self, reply):
        with self.lock:
            self.info.update(connected=True, state=reply["state"], hardware=reply["hardware"],
                             ack_seq=reply["seq"], generation=reply["generation"], reason=reply.get("reason", ""),
                             command=reply.get("command", [0, 0, 0]), via=reply.get("via","direct"),
                             relay_armed=reply.get("relay_armed",False))
            if self.wanted and not self.inhibited:
                if reply["generation"] != self.generation or reply["rearm_required"]:
                    self.inhibit("背包已暂停：" + reply.get("reason", "需要重新启用"))
                elif self.phase == "ARMING" and reply["state"] == "READY":
                    self.phase = "ACTIVE"

    def run(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=1.0) as connection:
                self.connection = connection
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                wire = JsonLines(connection)
                hello = wire.read(timeout=1.0)
                session, challenge = check_reply(hello, "hello", self.profile, seq=0)
                with self.lock:
                    self.info.update(connected=True, state=hello["state"], hardware=hello["hardware"], error="",
                                     via=hello.get("via","direct"),relay_armed=hello.get("relay_armed",False),
                                     reason=hello.get("reason",""))
                # Every new connection begins with a confirmed OFF, even if a
                # caller published a pre-existing ON before connection completed.
                seq = 1
                packet = self.packet(session, challenge, seq)
                packet.update(enabled=False, forward=0.0, yaw_left=0.0, generation=0,
                              frame_id=0, age_ms=0.0, source="none")
                wire.send(packet)
                reply = wire.read()
                _, challenge = check_reply(reply, "ack", self.profile, session, seq)
                with self.lock:
                    self.wanted = False
                    self.phase = "OFF"
                    # GUI is stopped while connecting. External adapters must
                    # explicitly publish OFF before their own enable edge.
                    if self.latest and self.latest.get("enabled"):
                        self.inhibit("新连接需要先暂停，再手动启用")
                deadline = time.monotonic()
                while not self.stop_event.is_set():
                    if self.stop_event.wait(max(0, deadline - time.monotonic())):
                        break
                    seq += 1
                    sent = time.monotonic()
                    packet = self.packet(session, challenge, seq, sent)
                    wire.send(packet)
                    reply = wire.read()
                    _, challenge = check_reply(reply, "ack", self.profile, session, seq)
                    if reply["hardware"] != hello["hardware"]:
                        raise ProtocolError("Receiver hardware mode changed")
                    self.accept_ack(reply)
                    deadline = max(sent + .05, time.monotonic())
        except (OSError, ValueError, EOFError) as error:
            with self.lock:
                self.info.update(error=str(error))
        finally:
            with self.lock:
                self.connection = None
                self.info.update(connected=False, state="DISCONNECTED", inhibit=True)
                self.inhibited = True

    def close(self):
        self.stop_event.set()
        with self.lock:
            connection = self.connection
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(1.5)
