import math
import unittest
from dataclasses import replace

from left_hand_drive.capture import demo_observation
from left_hand_drive.core import (Calibration, CalibrationError, Controller, Observation,
                                  PoseReference, extract_left, reference)


def calibration():
    return Calibration(*(PoseReference(demo_observation(1,0,g).fingers,17,"demo")
                         for g in ("NEUTRAL","FIST","OPEN")))


def hand_points(roll=0, scale=1):
    # Pixel-space skeleton with known straight fingers. Mirror before inference.
    p=[(320,350)] + [(240,310),(210,280),(180,250),(145,225)]
    for x in (260,300,340,380):
        p.extend(((x,240),(x,195),(x,155),(x,120)))
    out=[]
    for x,y in p:
        x,y=(x-320)*scale,(y-300)*scale
        a=math.radians(roll)
        out.append(((320+x*math.cos(a)+y*math.sin(a))/640,
                    (300-x*math.sin(a)+y*math.cos(a))/480,0))
    return out


class GeometryTests(unittest.TestCase):
    def observation(self,points=None,hands=None):
        return extract_left(hands if hands is not None else [("Left",.99,points or hand_points())],640,480,1,100)

    def test_only_anatomical_left_controls(self):
        self.assertFalse(self.observation(hands=[("Right",.99,hand_points())]).valid)
        self.assertTrue(self.observation().valid)

    def test_two_left_or_low_confidence_is_rejected(self):
        h=("Left",.99,hand_points())
        self.assertFalse(self.observation(hands=[h,h]).valid)
        self.assertFalse(self.observation(hands=[("Left",.7,hand_points())]).valid)

    def test_screen_left_tilt_has_positive_sign_and_is_finger_independent(self):
        self.assertAlmostEqual(self.observation(hand_points(25)).roll_deg,25,places=5)
        self.assertAlmostEqual(self.observation(hand_points(-25)).roll_deg,-25,places=5)
        points=hand_points()
        for i in (8,12,16,20): points[i]=(points[i][0],.58,0)
        self.assertAlmostEqual(self.observation(points).roll_deg,0,places=5)

    def test_features_preserve_scale_and_aspect_ratio(self):
        a=self.observation()
        b=self.observation(hand_points(scale=.8))
        # Same pixel skeleton represented by a different normalized resolution.
        wide=[(x/2,y,z) for x,y,z in hand_points()]
        c=extract_left([("Left",.99,wide)],1280,480,1,100)
        for x,y,z in zip(a.fingers,b.fingers,c.fingers):
            self.assertAlmostEqual(x,y)
            self.assertAlmostEqual(x,z)

    def test_clipped_nonfinite_small_and_degenerate_hands_stop(self):
        for points in ([(-.1,.4,0)]*21,[(float("nan"),.4,0)]*21,hand_points(scale=.1),[(.5,.5,0)]*21):
            self.assertFalse(self.observation(points).valid)


class CalibrationTests(unittest.TestCase):
    def test_individual_fingers_including_thumb_must_match(self):
        cal=calibration()
        self.assertEqual(cal.gesture(demo_observation(1,0,"FIST").fingers),"FIST")
        self.assertEqual(cal.gesture(demo_observation(1,0,"OPEN").fingers),"OPEN")
        for i in range(5):
            values=list(cal.fist.fingers)
            values[i]=cal.opened.fingers[i]
            self.assertEqual(cal.gesture(values),"NEUTRAL")

    def test_static_pose_cannot_equal_a_movement_pose(self):
        cal=calibration()
        for pose in (cal.fist,cal.opened):
            with self.assertRaises(CalibrationError): Calibration(pose,cal.fist,cal.opened)

    def test_relaxed_neutral_match_never_arms_a_movement_gesture(self):
        cal=calibration()
        # A repeat of the half grip can differ from the captured finger position.
        values=list(cal.neutral.fingers)
        values[1]+=.27*(cal.opened.fingers[1]-cal.fist.fingers[1])
        self.assertTrue(cal.at_neutral(Observation(1,0,"demo",tuple(values),17,True)))
        # Even near a calibrated neutral pose, movement endpoints cannot arm.
        middle=PoseReference(tuple(a+.45*(b-a) for a,b in zip(cal.fist.fingers,cal.opened.fingers)),17,"demo")
        near=Calibration(middle,cal.fist,cal.opened)
        for fraction in (.25,.75):
            fingers=tuple(a+fraction*(b-a) for a,b in zip(cal.fist.fingers,cal.opened.fingers))
            self.assertFalse(near.at_neutral(Observation(1,0,"demo",fingers,17,True)))

    def test_bad_endpoint_or_mixed_source_rejected(self):
        cal=calibration()
        with self.assertRaises(CalibrationError): Calibration(cal.neutral,cal.fist,cal.fist)
        with self.assertRaises(CalibrationError): Calibration(cal.neutral,cal.fist,replace(cal.opened,source="camera"))

    def test_stable_capture_and_circular_roll(self):
        samples=[demo_observation(i,i*.04,roll=179 if i%2 else -179) for i in range(26)]
        pose=reference(samples)
        self.assertAlmostEqual(abs(pose.roll_deg),180)
        self.assertEqual(pose.source,"demo")

    def test_discontinuous_short_or_moving_capture_rejected(self):
        samples=[demo_observation(i,i*.04) for i in range(26)]
        bad_sets=[samples[:3],samples[:10]+samples[20:],
                  [replace(x,roll_deg=i*2) for i,x in enumerate(samples)],
                  samples[:5]+[replace(samples[5],valid=False)]+samples[6:]]
        for bad in bad_sets:
            with self.assertRaises(CalibrationError): reference(bad)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.c=Controller()
        self.c.set_calibration(calibration())
        self.i=0
        self.now=100

    def feed(self,gesture="NEUTRAL",roll=17,valid=True,source="demo",age=0):
        self.i+=1
        self.now+=.05
        o=replace(demo_observation(self.i,self.now,gesture,roll),valid=valid,source=source)
        return self.c.update(o,self.now+age)

    def arm(self):
        self.c.enable()
        for i in range(12): command=self.feed()
        self.assertEqual(self.c.state,"ON")
        self.assertEqual(command.forward,0)

    def move(self,gesture="FIST",roll=17):
        for i in range(6): command=self.feed(gesture,roll)
        return command

    def test_fresh_controller_cannot_enable_without_current_calibration(self):
        fresh=Controller()
        self.assertFalse(fresh.enable())
        self.assertEqual(fresh.command(0).forward,0)

    def test_arming_requires_neutral_and_never_moves_on_arm_frame(self):
        self.c.enable()
        self.move("FIST",30)
        self.assertEqual(self.c.state,"ARMING")
        self.arm()

    def test_fist_open_neutral_and_simultaneous_turn(self):
        self.arm()
        self.assertGreater(self.move("FIST",45).forward,0)
        self.assertGreater(self.move("FIST",45).yaw_left,0)
        command=self.move("OPEN",-12)
        self.assertLess(command.forward,0)
        self.assertLess(command.yaw_left,0)
        command=self.feed("NEUTRAL",17)
        self.assertEqual((command.forward,command.yaw_left),(0,0))

    def test_gesture_requires_dwell_and_change_immediately_removes_translation(self):
        self.arm()
        self.assertEqual(self.feed("FIST").forward,0)
        self.assertGreater(self.move().forward,0)
        self.assertEqual(self.feed("OPEN").forward,0)
        self.assertEqual(self.feed("NEUTRAL").forward,0)

    def test_neutral_roll_is_per_calibration_not_absolute_upright(self):
        self.arm()
        self.assertEqual(self.move(roll=17).yaw_left,0)
        self.assertEqual(self.move(roll=24).yaw_left,0)
        self.assertGreater(self.move(roll=47).yaw_left,0)

    def test_missing_stale_or_future_input_latches_off(self):
        for kwargs in ({"valid":False},{"age":.31},{"age":-.01}):
            self.setUp()
            self.arm()
            self.move()
            command=self.feed(**kwargs)
            self.assertFalse(command.enabled)
            self.assertEqual((command.forward,command.yaw_left),(0,0))
            self.move()
            self.assertEqual(self.c.state,"OFF")

    def test_watchdog_stops_without_another_frame_and_needs_reenable(self):
        self.arm()
        self.move()
        command=self.c.tick(self.now+.31)
        self.assertFalse(command.enabled)
        self.assertEqual((command.forward,command.yaw_left),(0,0))
        self.move()
        self.assertEqual(self.c.state,"OFF")

    def test_duplicate_frame_does_not_refresh_sample_or_advance_dwell(self):
        self.arm()
        old=self.c.last
        self.c.update(old,self.now+.1)
        self.assertEqual(self.c.state,"OFF")
        self.assertEqual(self.c.last.sampled_at,old.sampled_at)

    def test_out_of_order_timestamp_or_changed_source_stops(self):
        self.arm()
        old=self.c.last
        command=self.c.update(replace(old,frame_id=old.frame_id+1,sampled_at=old.sampled_at-.1),self.now)
        self.assertFalse(command.enabled)
        self.c.enable()
        self.feed(source="camera")
        self.assertIsNone(self.c.calibration)

    def test_focus_loss_manual_stop_and_excessive_roll(self):
        self.arm()
        self.move()
        self.assertFalse(self.c.tick(self.now,focused=False).enabled)
        self.arm()
        self.assertFalse(self.feed(roll=110).enabled)
        self.c.stop()
        self.assertFalse(self.c.command(self.now).enabled)

    def test_output_bounded_session_sequenced_and_expiring(self):
        self.arm()
        a=self.move(roll=70)
        b=self.c.command(self.now)
        self.assertLessEqual(abs(a.forward),1)
        self.assertLessEqual(abs(a.yaw_left),1)
        self.assertEqual(a.session,b.session)
        self.assertGreater(b.seq,a.seq)
        self.assertEqual(b.valid_for_ms,150)
        self.assertEqual(b.as_dict()["schema"],"left-hand-chassis/1")

    def test_nonfinite_or_incomplete_features_do_not_escape_as_motion(self):
        self.arm()
        self.move()
        bad=replace(self.c.last,frame_id=self.i+1,fingers=(float("nan"),)*5)
        command=self.c.update(bad,self.now)
        self.assertFalse(command.enabled)
        self.assertEqual((command.forward,command.yaw_left),(0,0))


if __name__=="__main__":
    unittest.main()
