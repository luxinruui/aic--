from contextlib import redirect_stderr
from dataclasses import asdict, replace
import csv
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

from left_hand_drive.__main__ import main
from left_hand_drive.core import CalibrationError, Observation, extract_hand, extract_left
from left_hand_drive.runlog import RunLog
from left_hand_drive.upper import (AXES, GROUPS, ArmCalibration, ArmController, Settings,
                                  demo_calibration, demo_right)
from test_control import hand_points


class RightHandGeometryTests(unittest.TestCase):
    def test_anatomical_identity_is_independent_of_detection_order(self):
        right = ("Right", .99, hand_points(25))
        left = ("Left", .99, hand_points(-20))
        for hands in ((right, left), (left, right)):
            a = extract_hand(hands, 640, 480, 9, 100, side="Right")
            b = extract_left(hands, 640, 480, 9, 100)
            self.assertTrue(a.valid and b.valid)
            self.assertAlmostEqual(a.roll_deg, 25)
            self.assertAlmostEqual(b.roll_deg, -20)
            self.assertEqual((a.frame_id, a.sampled_at), (b.frame_id, b.sampled_at))
        self.assertFalse(extract_hand([left], 640, 480, 9, 100, side="Right").valid)
        self.assertFalse(extract_hand([right, right], 640, 480, 9, 100, side="Right").valid)

    def test_right_features_ignore_in_plane_wrist_rotation_and_image_aspect(self):
        baseline = extract_hand([("Right", .99, hand_points())], 640, 480, 1, 100, side="Right")
        for tilt in (-25, 25):
            value = extract_hand([("Right", .99, hand_points(tilt))], 640, 480, 1, 100, side="Right")
            self.assertTrue(value.valid)
            for a, b in zip(baseline.fingers, value.fingers):
                self.assertAlmostEqual(a, b)
        wide = [(x/2, y, z) for x,y,z in hand_points()]
        value = extract_hand([("Right", .99, wide)], 1280, 480, 1, 100, side="Right")
        for a, b in zip(baseline.fingers, value.fingers):
            self.assertAlmostEqual(a, b)


class UpperTests(unittest.TestCase):
    def setUp(self):
        self.c = ArmController()
        self.cal = demo_calibration()
        self.c.set_calibration(self.cal)
        self.now, self.frame = 100., 0

    def feed(self, values=(.5,)*5, **changes):
        self.now += .05
        self.frame += 1
        fingers = tuple(a+u*(b-a) for u,a,b in zip(values, self.cal.fist.fingers, self.cal.opened.fingers))
        observation = Observation(self.frame, self.now, "demo", fingers, 0, True)
        return self.c.update(replace(observation, **changes), self.now)

    def arm(self):
        self.assertTrue(self.c.enable())
        for _ in range(13):
            command = self.feed()
        self.assertTrue(command.enabled, command.reason)
        return command

    def test_explicit_enable_calibration_and_neutral_dwell_required(self):
        self.assertFalse(ArmController().enable())
        self.assertFalse(self.feed().enabled)
        self.c.enable()
        for _ in range(20):
            self.assertFalse(self.feed((1,)*5).enabled)
        command = self.arm()
        self.assertEqual(command.targets, self.c.map_targets((.5,)*5))

    def test_each_finger_changes_only_its_group_with_expected_direction(self):
        for finger, group in enumerate(GROUPS):
            self.setUp()
            initial = self.arm().targets
            values = [.5]*5
            values[finger] = 1
            for _ in range(80):
                command = self.feed(values)
            changed = {key for key in AXES if abs(command.targets[key]-initial[key]) > 1}
            self.assertEqual(changed, {AXES[j] for j in group})
            for j in group:
                if finger in (2, 3):
                    self.assertGreater(command.targets[AXES[j]], initial[AXES[j]])
                else:
                    self.assertLess(command.targets[AXES[j]], initial[AXES[j]])

    def test_paired_upper_arms_share_progress_and_respect_slower_joint(self):
        ranges = ((0., 30.), (10., 90.), *self.c.settings.ranges[2:])
        self.c.set_settings(Settings(ranges=ranges, speed=10))
        previous = self.arm().targets
        for _ in range(100):
            command = self.feed((0., .5, .5, .5, .5))
            left, right = command.targets["left_upper"], command.targets["right_upper"]
            self.assertAlmostEqual(left/30, (right-10)/80)
            for key in ("left_upper", "right_upper"):
                self.assertLessEqual(abs(command.targets[key]-previous[key]), .5+1e-8)
            previous = command.targets

    def test_wrist_does_not_control_a_joint_or_disable_armed_upper_body(self):
        initial = self.arm().targets
        for tilt in (-170, -70, 0, 70, 170):
            command = self.feed(roll_deg=tilt)
            self.assertTrue(command.enabled)
            self.assertEqual(command.targets, initial)

    def test_small_noise_does_not_drift_targets(self):
        initial = self.arm().targets
        for i in range(100):
            command = self.feed((.51 if i%2 else .49,)*5)
        self.assertEqual(command.targets, initial)

    def test_preview_is_available_off_but_effective_targets_are_empty(self):
        command = self.feed()
        self.assertTrue(command.input_valid)
        self.assertTrue(all(v is None for v in command.targets.values()))
        self.assertTrue(all(v is not None for v in command.preview_targets.values()))
        self.assertTrue(command.preview_only)
        self.assertFalse(command.hardware_connected)

    def test_loss_invalid_values_and_old_or_future_samples_latch_off(self):
        for change in ({"valid": False}, {"fingers": (math.nan,)*5},
                       {"sampled_at": 90}, {"sampled_at": 200}):
            self.setUp()
            self.arm()
            command = self.feed(**change)
            self.assertFalse(command.enabled)
            self.assertTrue(all(v is None for v in command.targets.values()))
            self.assertFalse(self.feed().enabled)

    def test_watchdog_focus_manual_pause_and_duplicate_do_not_auto_resume(self):
        for stop in (lambda: self.c.tick(self.now+.31), lambda: self.c.tick(self.now, False),
                     lambda: self.c.update(self.c.last, self.now), lambda: self.c.stop()):
            self.setUp()
            self.arm()
            stop()
            self.assertEqual(self.c.state, "OFF")
            self.assertFalse(self.feed().enabled)
            self.assertTrue(all(v is None for v in self.c.command(self.now).targets.values()))

    def test_source_change_invalidates_calibration(self):
        self.arm()
        self.feed(source="camera")
        self.assertIsNone(self.c.calibration)

    def test_inactive_groups_have_no_targets_and_thumb_group_reverses_together(self):
        self.c.set_settings(Settings(active=(True, False, False, False, False), reverse=(True, False, False, False, False)))
        initial = self.arm().targets
        for _ in range(50):
            command = self.feed((1., .5, .5, .5, .5))
        self.assertGreater(command.targets["left_upper"], initial["left_upper"])
        self.assertEqual(command.targets["left_upper"], command.targets["right_upper"])
        self.assertTrue(all(command.targets[key] is None for key in AXES[2:]))

    def test_bad_endpoint_identifies_finger_and_cannot_enable(self):
        opened = replace(self.cal.opened, fingers=(*self.cal.opened.fingers[:4], self.cal.fist.fingers[4]))
        bad = replace(self.cal, opened=opened)
        with self.assertRaisesRegex(CalibrationError, "小指"):
            self.c.set_calibration(bad)
        with self.assertRaises(CalibrationError):
            self.c.set_calibration(replace(self.cal, opened=replace(self.cal.opened, source="camera")))
        self.c.set_settings(Settings(active=(True, True, True, True, False)))
        self.c.set_calibration(bad)
        self.c.set_settings(Settings())
        self.assertIsNone(self.c.calibration)

    def test_invalid_ranges_and_empty_or_malformed_active_groups_rejected(self):
        for kwargs in ({"ranges": ((0, 91),)*6}, {"ranges": ((1, 1),)*6},
                       {"active": (False,)*5}, {"active": (True,)}, {"speed": math.nan}):
            with self.assertRaises(ValueError):
                Settings(**kwargs)

    def test_log_records_final_off_without_throttle_hiding_stop(self):
        self.arm()
        with tempfile.TemporaryDirectory() as temporary:
            log = RunLog(temporary, "demo")
            log.enable_upper(asdict(self.c.settings))
            log.upper_calibration(self.cal)
            log.record_upper(self.c.command(self.now))
            self.c.stop()
            log.record_upper(self.c.command(self.now+.001))
            log.close("test")
            with (log.path/"upper_commands.csv").open(encoding="utf-8",newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]["enabled"], "False")
            self.assertTrue(all(rows[-1][key] == "" for key in AXES))
            info = json.loads((log.path/"run.json").read_text(encoding="utf-8"))
            self.assertTrue(info["upper_preview_only"])
            self.assertFalse(info["hardware_connected"])
            self.assertFalse(info["image_recording"])

    def test_upper_cli_rejects_hardware_before_creating_gui_or_network(self):
        for args in (["--upper", "--robot"], ["--upper", "--host", "192.168.1.2"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main(args)
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
