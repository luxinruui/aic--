from dataclasses import replace
import unittest

from aic_hand.capture import anatomical_side, synthetic_hand
from aic_hand.config import AXES, load, validate
from aic_hand.core import Mapper, Observation, Point, hand_feature


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.config = load()
        self.config.update(mode="six", smoothing_seconds=0, deadband_degrees=0)

    def observation(self, hands, frame=1, at=10, width=640, height=480):
        return Observation(frame, at, width, height, tuple(hands), "test")

    def test_missing_hand_never_produces_zero_target(self):
        result = Mapper(self.config).process(self.observation(()), 10)
        self.assertTrue(all(not state.valid and state.target is None for state in result.axes.values()))

    def test_single_axis_uses_selected_hand_vertical_motion(self):
        self.config.update(mode="single", single_axis="right_elbow")
        mapper = Mapper(self.config)
        # Palm includes wrist y + .1, hence its mean is center y + .02.
        top = mapper.process(self.observation([synthetic_hand("Right", y=.18)]), 10)
        bottom = mapper.process(self.observation([synthetic_hand("Right", y=.78)], 2, 10.1), 10.1)
        self.assertAlmostEqual(top.axes["right_elbow"].target, 180)
        self.assertAlmostEqual(bottom.axes["right_elbow"].target, 0)
        self.assertEqual(sum(axis.valid for axis in top.axes.values()), 1)

    def test_single_axis_does_not_fall_back_to_wrong_hand(self):
        self.config["mode"] = "single"
        result = Mapper(self.config).process(self.observation([synthetic_hand("Right")]), 10)
        self.assertFalse(result.axes["left_elbow"].valid)

    def test_six_axis_endpoints_and_left_right_directions(self):
        left = synthetic_hand("Left", x=.5, y=.18, pinch=.15)
        right = synthetic_hand("Right", x=.5, y=.18, pinch=1.6)
        result = Mapper(self.config).process(self.observation([right, left]), 10)
        self.assertTrue(all(state.valid for state in result.axes.values()))
        self.assertAlmostEqual(result.axes["left_upper"].target, 90)
        self.assertAlmostEqual(result.axes["right_upper"].target, 90)
        self.assertAlmostEqual(result.axes["left_lift"].target, 90)
        self.assertAlmostEqual(result.axes["left_elbow"].target, 0)
        self.assertAlmostEqual(result.axes["right_elbow"].target, 180)

    def test_target_ranges_are_clamped(self):
        result = Mapper(self.config).process(self.observation([synthetic_hand("Left", y=.8, pinch=2.0)]), 10)
        for axis, state in result.axes.items():
            if state.valid:
                self.assertGreaterEqual(state.target, 0)
                self.assertLessEqual(state.target, 180 if axis.endswith("elbow") else 90)

    def test_pinch_uses_image_aspect_ratio(self):
        hand = synthetic_hand("Left")
        points = list(hand.points)
        points[8] = Point(points[4].x + .06, points[4].y + .08)
        hand = replace(hand, points=tuple(points))
        ratio = hand_feature(hand, 640, 480).pinch
        resized = replace(hand, points=tuple(Point(p.x / 2, p.y) for p in hand.points))
        self.assertAlmostEqual(ratio, hand_feature(resized, 1280, 480).pinch)

    def test_duplicate_identity_invalidates_only_ambiguous_arm(self):
        result = Mapper(self.config).process(self.observation([
            synthetic_hand("Left"), synthetic_hand("Left"), synthetic_hand("Right")]), 10)
        self.assertFalse(result.axes["left_elbow"].valid)
        self.assertTrue(result.axes["right_elbow"].valid)

    def test_low_handedness_score_rejected(self):
        result = Mapper(self.config).process(self.observation([replace(synthetic_hand("Left"), score=.2)]), 10)
        self.assertFalse(result.axes["left_elbow"].valid)

    def test_nonfinite_landmark_is_invalid(self):
        hand = synthetic_hand("Left")
        points = list(hand.points)
        points[8] = Point(float("nan"), .4)
        result = Mapper(self.config).process(self.observation([replace(hand, points=tuple(points))]), 10)
        self.assertFalse(result.axes["left_elbow"].valid)

    def test_degenerate_palm_and_incomplete_hand_rejected(self):
        hand = synthetic_hand("Left")
        with self.assertRaises(ValueError):
            hand_feature(replace(hand, points=(Point(.5, .5),) * 21), 640, 480)
        with self.assertRaises(ValueError):
            hand_feature(replace(hand, points=hand.points[:20]), 640, 480)

    def test_stale_future_and_invalid_clock_clear_targets(self):
        observation = self.observation([synthetic_hand("Left")])
        for now in (10.36, 9.9, float("nan")):
            with self.subTest(now=now):
                result = Mapper(self.config).process(observation, now)
                self.assertTrue(all(not axis.valid and axis.target is None for axis in result.axes.values()))

    def test_duplicate_and_reversed_frame_not_reused(self):
        mapper = Mapper(self.config)
        self.assertTrue(mapper.process(self.observation([synthetic_hand("Left")], 2), 10).axes["left_elbow"].valid)
        for frame in (2, 1):
            result = mapper.process(self.observation([synthetic_hand("Left")], frame), 10)
            self.assertFalse(result.axes["left_elbow"].valid)

    def test_new_frame_id_cannot_refresh_an_old_sample(self):
        mapper = Mapper(self.config)
        mapper.process(self.observation([synthetic_hand("Left")]), 10)
        result = mapper.process(self.observation([synthetic_hand("Left")], frame=2, at=10), 10.1)
        self.assertTrue(all(not state.valid for state in result.axes.values()))

    def test_loss_and_reacquisition_does_not_reuse_old_filter(self):
        self.config["smoothing_seconds"] = 1
        mapper = Mapper(self.config)
        mapper.process(self.observation([synthetic_hand("Left", pinch=.15)]), 10)
        missing = mapper.process(self.observation([], 2, 10.1), 10.1)
        returned = mapper.process(self.observation([synthetic_hand("Left", pinch=1.6)], 3, 10.2), 10.2)
        self.assertIsNone(missing.axes["left_elbow"].target)
        self.assertAlmostEqual(returned.axes["left_elbow"].target, 180)

    def test_filter_reduces_step_without_exceeding_target(self):
        self.config["smoothing_seconds"] = .12
        mapper = Mapper(self.config)
        mapper.process(self.observation([synthetic_hand("Left", pinch=.15)]), 10)
        result = mapper.process(self.observation([synthetic_hand("Left", pinch=1.6)], 2, 10.05), 10.05)
        self.assertGreater(result.axes["left_elbow"].target, 0)
        self.assertLess(result.axes["left_elbow"].target, 180)

    def test_bad_calibration_is_rejected(self):
        for key, value in (("x_in", .85), ("pinch_open", .16), ("y_bottom", .2)):
            config = load()
            config["calibration"]["Left"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(config)

    def test_preview_mirroring_does_not_change_mapping(self):
        observation = self.observation([synthetic_hand("Left"), synthetic_hand("Right")])
        first = Mapper(self.config).process(observation, 10)
        self.config["mirror"] = not self.config["mirror"]
        second = Mapper(self.config).process(observation, 10)
        self.assertEqual(first.axes, second.axes)

    def test_legacy_handedness_converted_for_unmirrored_input(self):
        self.assertEqual(anatomical_side("Right"), "Left")
        self.assertEqual(anatomical_side("Left"), "Right")
        self.assertEqual(anatomical_side("Right", swap=True), "Right")
        self.assertEqual(anatomical_side("unknown"), "Unknown")


if __name__ == "__main__":
    unittest.main()
