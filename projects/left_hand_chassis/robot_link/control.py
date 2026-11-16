"""Backpack's independent 20Hz scheduler; latest target, watchdog and UART owner."""
import math
import threading
import time

from .config import validate
from .protocol import Intent

ZERO = (0, 0, 0)


class ChassisController:
    def __init__(self, config, writer=None, clock=time.monotonic):
        self.config, self.writer, self.clock = validate(config), writer, clock
        self.lock = threading.RLock()
        self.state, self.reason = "DISARMED", "等待电脑明确启用"
        self.session = None
        self.generation, self.blocked = 0, -1
        self.active = None
        self.seen_off = False
        self.last_seq = self.last_frame = -1
        self.input_session = None
        self.deadline = self.source_deadline = self.last_tick = None
        self.goal = (0.0, 0.0)
        self.current = (0.0, 0.0)
        self.command = ZERO

    @property
    def hardware(self):
        return self.writer is not None

    def feedback(self):
        with self.lock:
            return dict(state=self.state, reason=self.reason, hardware=self.hardware,
                        generation=self.generation, rearm_required=self.active is None,
                        command=list(self.command))

    def _write(self, command):
        try:
            if self.writer is not None:
                self.writer.write(command)
            self.command = command
            return True
        except Exception as error:
            self.state, self.reason = "FAULT", "串口写入失败，已锁定且不重试：" + str(error)
            self.goal = self.current = (0.0, 0.0)
            self.active = None
            try:
                self.writer.close()
            except Exception:
                pass
            return False

    def disarm(self, reason, force=False, generation=None):
        with self.lock:
            if self.state in ("FAULT", "CLOSED"):
                return
            if generation is not None:
                self.generation = max(self.generation, generation)
                self.blocked = max(self.blocked, generation)
            if self.active is not None:
                self.blocked = max(self.blocked, self.active)
            self.active, self.seen_off = None, False
            self.goal = self.current = (0.0, 0.0)
            self.deadline = self.source_deadline = self.last_tick = None
            if (force or self.command != ZERO) and not self._write(ZERO):
                return
            self.state, self.reason = "DISARMED", str(reason)

    def new_session(self, session):
        with self.lock:
            self.disarm("新连接，请手动启用", force=True)
            self.session = session
            self.generation, self.blocked = 0, -1
            self.last_seq = self.last_frame = -1

    def process(self, intent, now=None):
        with self.lock:
            now = self.clock() if now is None else now
            if self.state in ("FAULT", "CLOSED") or self.session is None:
                return False
            if not isinstance(intent, Intent) or intent.seq <= self.last_seq:
                self.disarm("无效或重复指令", force=True)
                return False
            self.last_seq = intent.seq
            if not intent.enabled:
                self.disarm("电脑暂停", generation=intent.generation)
                self.seen_off = self.state != "FAULT"
                return False
            if self.hardware and intent.source != "camera":
                self.disarm("实机拒绝合成演示输入", generation=intent.generation, force=True)
                return False
            if not 0 <= intent.age_ms < 300:
                self.disarm("采样过期", generation=intent.generation, force=True)
                return False
            if intent.generation <= self.blocked:
                return False
            if self.active is None:
                if not self.seen_off or intent.forward != 0 or intent.yaw_left != 0:
                    self.disarm("先OFF，再以零目标启用", generation=intent.generation, force=True)
                    return False
                self.active = self.generation = intent.generation
                self.seen_off = False
                self.last_frame = -1
                self.input_session = intent.input_session
            if intent.generation != self.active or intent.input_session != self.input_session:
                self.disarm("输入会话/启用代次改变", generation=intent.generation, force=True)
                return False
            if self.deadline is not None and now >= self.deadline:
                self.disarm("数据超时，请重新启用", generation=intent.generation, force=True)
                return False
            if intent.frame_id < self.last_frame:
                self.disarm("输入帧倒退", generation=intent.generation, force=True)
                return False
            source_deadline = now + (300 - intent.age_ms) / 1000
            if intent.frame_id == self.last_frame:
                # Repeated snapshots never refresh input freshness or change a target.
                self.source_deadline = min(self.source_deadline, source_deadline)
                self.deadline = min(self.source_deadline, now + .15)
                return False
            self.last_frame = intent.frame_id
            self.source_deadline = source_deadline
            self.deadline = min(source_deadline, now + .15)
            self.goal = (intent.forward * self.config["max_translation"], intent.yaw_left * self.config["max_rotation"])
            self.state = "MOVING" if self.goal != (0, 0) else "READY"
            self.reason = "目标已接收" if self.state == "MOVING" else "已就绪，等待手势"
            return True

    def tick(self, now=None):
        with self.lock:
            if self.state in ("DISARMED", "FAULT", "CLOSED"):
                return False
            now = self.clock() if now is None else now
            if self.deadline is None or now >= self.deadline:
                self.disarm("链路/采样超时，已发送STOP", force=True)
                return False
            if self.last_tick is not None and now - self.last_tick < .05 - 1e-8:
                return False
            dt = .05 if self.last_tick is None else min(.05, max(0, now - self.last_tick))
            self.last_tick = now
            values = []
            for current, goal, acceleration in zip(self.current, self.goal,
                    (self.config["translation_acceleration"], self.config["rotation_acceleration"])):
                # A released axis stops immediately; reversals ramp through zero.
                values.append(0.0 if goal == 0 else current + max(-acceleration * dt, min(acceleration * dt, goal - current)))
            self.current = tuple(values)
            linear, turn = (int(round(v)) for v in values)
            command = (0 if linear >= 0 else 180, abs(linear), turn)
            if command == ZERO and self.command == ZERO:
                return False
            return self._write(command)

    def close(self):
        with self.lock:
            if self.state == "CLOSED":
                return
            self.disarm("接收端退出", force=True)
            try:
                if self.writer:
                    self.writer.close()
            finally:
                self.state, self.session = "CLOSED", None
