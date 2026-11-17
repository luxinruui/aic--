"""Global OFF/prepare barrier, proven chassis gate, paired arm progress and UART."""
import math
import threading
import time

from robot_link.control import ChassisController
from robot_link.protocol import Intent as ChassisIntent
from .bus import ChassisOutput
from .profile import AXES, GROUPS, IDS, MAXIMUM


def advance(position, velocity, target, dt, rate, acceleration):
    """The original AIC braking ramp, expressed in common normalized progress."""
    error = target-position
    if abs(error) <= 1e-7 and abs(velocity) <= acceleration*dt:
        return target, 0.
    direction = 1. if error >= 0 else -1.
    speed = velocity*direction
    adt = acceleration*dt
    if speed >= 0:
        discriminant = adt*adt-4*adt*speed+8*acceleration*abs(error)
        safe_speed = max(0., (-adt+math.sqrt(max(0., discriminant)))/2)
    else:
        safe_speed = math.sqrt(2*acceleration*abs(error))
    desired = direction*min(rate, safe_speed)
    next_velocity = velocity+max(-adt, min(adt, desired-velocity))
    next_position = position+(velocity+next_velocity)*.5*dt
    bounded = max(0., min(1., next_position))
    return bounded, 0. if bounded != next_position else next_velocity


class Controller:
    def __init__(self, profile, bus, baseline_confirmed=False, clock=time.monotonic):
        self.profile, self.bus, self.clock = profile, bus, clock
        self.lock = threading.RLock()
        self.baseline_confirmed = bool(baseline_confirmed) or not bus.hardware
        self.chassis = ChassisController(profile.chassis, ChassisOutput(bus) if bus.hardware else None, clock)
        self.mode, self.groups = "arms", (False,)*5
        self.arm_active = self.faulted = False
        self.positions = dict(profile.positions)
        self.cursor = [profile.progress(group[0],profile.angle(group[0],self.positions[IDS[group[0]]])) for group in GROUPS]
        self.goals, self.velocity = list(self.cursor), [0.]*5
        self.last_step = None
        self.issued_arm_segments = 0

    @property
    def hardware(self): return self.bus.hardware

    @property
    def session(self): return self.chassis.session

    @session.setter
    def session(self, value): self.chassis.session = value

    def freeze_arms(self):
        self.arm_active = False
        self.goals, self.velocity = list(self.cursor), [0.]*5
        self.last_step = None

    def disarm(self, reason, force=False, generation=None):
        with self.lock:
            self.freeze_arms()
            self.chassis.disarm(reason, force=force, generation=generation)

    def new_session(self, session):
        with self.lock:
            self.freeze_arms()
            self.chassis.new_session(session)

    def reference(self):
        return {name:self.profile.progress_angle(name,self.cursor[i]) for i,group in enumerate(GROUPS) for name in group}

    def feedback(self):
        with self.lock:
            value = self.chassis.feedback()
            value.update(hardware=self.hardware, baseline_confirmed=self.baseline_confirmed,
                         mode=self.mode, arm_groups=list(self.groups), arm_reference=self.reference(),
                         reference_kind="last_commanded_not_position_feedback", arm_positions=dict(self.positions),
                         arm_segments=self.issued_arm_segments, uart_frames=self.bus.frames_written,
                         arm_state="ACTIVE" if self.arm_active else "HOLD", rearm_required=self.chassis.active is None)
            if self.arm_active and self.chassis.active is not None:
                value["state"] = "MOVING" if any(abs(a-b) > 1e-6 for a,b in zip(self.goals,self.cursor)) else value["state"]
            if self.faulted or self.bus.faulted:
                value.update(state="FAULT", rearm_required=True, reason="共享UART异常，已锁定，不重试")
            return value

    def process(self, intent, now=None):
        with self.lock:
            now = self.clock() if now is None else now
            if self.faulted or self.bus.faulted:
                return False
            if not intent.enabled:
                self.freeze_arms()
            else:
                has_arms = intent.mode in ("arms", "both")
                if has_arms and not self.baseline_confirmed:
                    self.disarm("请先按原六轴工程设置基准，并在背包确认基准", True, intent.generation)
                    return False
                starting = self.chassis.active is None
                if starting:
                    if intent.stage != "prepare":
                        self.disarm("需要先OFF，再以准备帧启用", True, intent.generation)
                        return False
                    if has_arms:
                        reference = self.reference()
                        if any(abs(intent.targets[name]-reference[name]) > .15
                               for active, group in zip(intent.groups,GROUPS) if active for name in group):
                            self.disarm("上肢首次目标未匹配已确认的软件参考姿态", True, intent.generation)
                            return False
                    self.mode, self.groups = intent.mode, intent.groups
                elif intent.mode != self.mode or intent.groups != self.groups:
                    self.disarm("模式或活动关节改变，须先暂停后重新启用", True, intent.generation)
                    return False
                if intent.stage == "prepare" and self.arm_active:
                    self.disarm("活动中不能退回准备阶段", True, intent.generation)
                    return False
            chassis = ChassisIntent(intent.seq, intent.generation, intent.input_session, intent.frame_id,
                intent.enabled, intent.source, intent.forward, intent.yaw_left, intent.age_ms)
            accepted = self.chassis.process(chassis, now)
            if self.chassis.active is None:
                self.freeze_arms()
                return False
            if accepted and intent.enabled:
                self.arm_active = intent.mode in ("arms", "both") and intent.stage == "active"
                if self.arm_active:
                    for i, (active, group) in enumerate(zip(intent.groups,GROUPS)):
                        if active:
                            goal = self.profile.progress(group[0],intent.targets[group[0]])
                            if goal in (0.,1.) or abs(goal-self.goals[i])*self.profile.group_scale(group) > self.profile.arm["target_deadband"]:
                                self.goals[i] = goal
            return accepted

    def tick(self, now=None):
        with self.lock:
            now = self.clock() if now is None else now
            self.chassis.tick(now)  # watchdog/STOP always runs before arm writes
            if self.bus.faulted or self.chassis.state == "FAULT":
                self.faulted = True
                self.freeze_arms()
                return False
            if self.chassis.active is None:
                self.freeze_arms()
                return False
            if not self.arm_active or (self.last_step is not None and now-self.last_step < self.profile.period-1e-8):
                return False
            dt = self.profile.period if self.last_step is None else min(self.profile.period, now-self.last_step)
            self.last_step = now
            updated = dict(self.positions)
            selected = {}
            for i, (active, group) in enumerate(zip(self.groups,GROUPS)):
                if not active:
                    continue
                scale = self.profile.group_scale(group)
                q, velocity = advance(self.cursor[i], self.velocity[i], self.goals[i], dt,
                                      self.profile.arm["max_position_rate"]/scale,
                                      self.profile.arm["max_acceleration"]/scale)
                self.cursor[i], self.velocity[i] = q, velocity
                for name in group:
                    # Ramp in raw-position progress: the elbow's 90-degree
                    # calibration kink must not introduce a raw velocity jump.
                    position = int(round(self.profile.progress_position(name,q)))
                    updated[IDS[name]] = selected[IDS[name]] = position
            if not any(updated[k] != self.positions[k] for k in selected):
                return False
            try:
                self.bus.write_arms(selected)
            except Exception as error:
                self.faulted = True
                self.disarm("上肢UART写入失败，不重试："+str(error), True)
                return False
            self.positions = updated
            self.issued_arm_segments += 1
            return True

    def close(self):
        with self.lock:
            self.freeze_arms()
            self.chassis.close()
            self.bus.close()
