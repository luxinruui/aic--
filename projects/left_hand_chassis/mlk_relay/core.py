"""A local enable gate between PC and backpack; preserves original sample age."""
import threading
import time

from robot_link.client import Client
from robot_link.control import ChassisController


class RelayController:
    def __init__(self, config, downstream, clock=time.monotonic):
        self.downstream, self.clock = downstream, clock
        self.gate = ChassisController(config, clock=clock)  # No UART writer on MLK.
        self.lock = threading.RLock()
        self.armed = False
        self.local_reason = "米联客本地尚未启用，输入a后仍须电脑手动启用"
        self.closed = False

    @property
    def hardware(self):
        return self.downstream.status().get("hardware") is True

    @property
    def session(self):
        return self.gate.session

    @session.setter
    def session(self, value):
        self.gate.session = value

    def _off(self, now=None):
        now = self.clock() if now is None else now
        self.downstream.publish(dict(session="mlk-relay-off-session", seq=0, produced_at=now,
            valid_for_ms=150, enabled=False, input_valid=False, source="none", frame_id=0,
            sample_age_ms=0, forward=0.0, yaw_left=0.0))

    def feedback(self):
        with self.lock:
            status = self.downstream.status()
            value = self.gate.feedback()
            value.update(hardware=self.hardware, via="mlk-relay", relay_armed=self.armed,
                         downstream_connected=status["connected"], downstream_state=status["state"],
                         command=status.get("command", [0,0,0]))
            if not self.armed:
                value.update(state="DISARMED", rearm_required=True, reason=self.local_reason)
            elif not status["connected"]:
                value.update(state="DISARMED", rearm_required=True, reason="米联客未连接背包")
            return value

    def arm_local(self):
        with self.lock:
            status = self.downstream.status()
            if self.closed or not status["connected"]:
                self.stop_local("背包未连接，不能启用米联客")
                return False
            self.gate.disarm("本地已允许，等待电脑OFF及新代次", force=True)
            self._off()
            self.armed = True
            self.local_reason = "本地已允许，等待电脑手动启用"
            return True

    def stop_local(self, reason="米联客本地停止"):
        with self.lock:
            self.armed = False
            self.local_reason = str(reason)
            self.gate.disarm(reason, force=True)
            self._off()

    def disarm(self, reason, force=False, generation=None):
        # Server calls this on disconnect or malformed TCP: revoke local consent.
        self.stop_local(reason)

    def new_session(self, session):
        with self.lock:
            self.gate.new_session(session)
            self._off()

    def process(self, intent, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            if self.closed:
                return False
            if not intent.enabled:
                self.gate.process(intent, now)
                self._off(now)
                return False
            status = self.downstream.status()
            if (not self.armed or not status["connected"] or status.get("inhibit") or
                    (self.hardware and intent.source != "camera")):
                reason = ("米联客本地未启用" if not self.armed else "米联客下游未就绪或来源被拒绝")
                self.gate.disarm(reason, force=True, generation=intent.generation)
                self._off(now)
                return False
            was_active = self.gate.active is not None
            accepted = self.gate.process(intent, now)
            if accepted:
                # Clock is local to MLK, age already includes PC->MLK transit.
                # The downstream client adds MLK dwell time and the backpack
                # adds the second hop challenge age. Never reset original age.
                self.downstream.publish(dict(session=intent.input_session, seq=intent.seq,
                    produced_at=now, valid_for_ms=150, enabled=True, input_valid=True,
                    source=intent.source, frame_id=intent.frame_id, sample_age_ms=intent.age_ms,
                    forward=intent.forward, yaw_left=intent.yaw_left))
            elif self.gate.active is None:
                if was_active:
                    self.armed = False
                    self.local_reason = "电脑数据异常，米联客已停止；需重新本地启用"
                self._off(now)
            return accepted

    def tick(self, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            if self.closed:
                return False
            status = self.downstream.status()
            if self.armed and (not status["connected"] or status.get("inhibit")):
                self.stop_local("背包链路中断或拒绝，米联客已停止")
                return False
            was_active = self.gate.active is not None
            self.gate.tick(now)  # Local watchdog only; no serial writer exists.
            if was_active and self.gate.active is None:
                self.stop_local("上游超时，米联客已停止")
            return False

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.stop_local("米联客程序退出")
            self.gate.close()
            self.downstream.close()
            self.closed = True
