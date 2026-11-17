"""Deterministic combined PC preview validation. No camera, network or UART."""
from .capture import demo_observation
from .core import Calibration, Controller, PoseReference
from .runlog import RunLog
from .upper import ArmController, demo_calibration, demo_right


def run_demo(root):
    left, right = Controller(), ArmController()
    left.set_calibration(Calibration(*(PoseReference(demo_observation(1, 0, g).fingers, 0, "demo")
                                      for g in ("NEUTRAL", "FIST", "OPEN"))))
    right.set_calibration(demo_calibration())
    left.enable()
    right.enable()
    log = RunLog(root, "validation-dual-demo")
    from dataclasses import asdict
    log.enable_upper(asdict(right.settings))
    log.calibration(left.calibration)
    log.upper_calibration(right.calibration)
    extrema = {key: [] for key in right.command(0).targets}
    seen = set()
    try:
        for i in range(360):
            elapsed, now = i*.05, 100+i*.05
            gesture = "NEUTRAL" if elapsed < 2 else "FIST" if elapsed < 9 else "OPEN"
            roll = 0 if elapsed < 2 else 28 if elapsed < 6 else -28
            observation = demo_observation(i+1, now, gesture, roll)
            command = left.update(observation, now)
            upper = right.update(demo_right(i+1, now, elapsed), now)
            log.record(command, observation)
            log.record_upper(upper)
            if command.forward > 0: seen.add("forward")
            if command.forward < 0: seen.add("backward")
            if command.yaw_left > 0: seen.add("left")
            if command.yaw_left < 0: seen.add("right")
            if upper.enabled:
                assert upper.targets["left_upper"] == upper.targets["right_upper"]
                for key, value in upper.targets.items():
                    extrema[key].append(value)
        assert all(max(values)-min(values) > 10 for values in extrema.values()), extrema
        assert seen == {"forward", "backward", "left", "right"}, seen
        assert not right.tick(now+.31).enabled
        assert all(v is None for v in right.command(now+.31).targets.values())
        log.record_upper(right.command(now+.31))
        log.record(left.tick(now+.31))
        print("Synthetic dual-hand preview passed: five fingers / six software targets, paired upper arms,")
        print("four chassis directions and stale-input STOP. No camera, TCP or UART opened.")
        print("Numeric run log:", log.path)
    finally:
        log.close("synthetic dual-hand validation completed")
