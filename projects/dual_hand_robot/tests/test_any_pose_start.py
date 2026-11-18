"""Current-pose enable and ramped takeover; no camera, network or real UART."""
from dataclasses import replace
import unittest

from left_hand_drive.capture import demo_observation
from left_hand_drive.core import Controller, Observation
from left_hand_drive.upper import ArmController, demo_calibration
from test_control import calibration


class CurrentPoseStartTests(unittest.TestCase):
    def right(self, controller, frame, now, values):
        cal = controller.calibration
        fingers = tuple(a+u*(b-a) for u,a,b in zip(values,cal.fist.fingers,cal.opened.fingers))
        return controller.update(Observation(frame,now,'demo',fingers,0,True),now)

    def test_chassis_fist_open_and_tilt_can_enable_without_neutral(self):
        for gesture, tilt, sign in (('FIST',45,1),('OPEN',-12,-1)):
            c = Controller()
            c.set_calibration(calibration())
            c.enable(any_pose=True)
            first = c.update(demo_observation(1,100,gesture,tilt),100)
            self.assertTrue(first.enabled)
            self.assertEqual((first.forward,first.yaw_left),(0,0))
            for frame in range(2,9):
                current = c.update(demo_observation(frame,100+frame*.05,gesture,tilt),100+frame*.05)
            self.assertGreater(sign*current.forward,0)
            self.assertGreater(sign*current.yaw_left,0)

    def test_right_starts_at_robot_reference_then_ramps_to_any_hand(self):
        c = ArmController()
        c.set_calibration(demo_calibration())
        reference = dict(left_upper=20.,right_upper=20.,left_elbow=40.,left_lift=50.,right_lift=60.,right_elbow=30.)
        c.set_reference(reference)
        for values in ((0.,)*5,(1.,)*5,(.15,.83,.2,.7,.4)):
            c.enable(any_pose=True)
            first = self.right(c, c.last_frame+1, 100 if c.last is None else c.last.sampled_at+.05, values)
            self.assertTrue(first.enabled)
            for key,value in reference.items():
                self.assertAlmostEqual(first.targets[key],value)
            previous = first
            for _ in range(100):
                current = self.right(c,c.last_frame+1,c.last.sampled_at+.05,values)
                self.assertTrue(current.enabled)
                for key,value in current.targets.items():
                    self.assertLessEqual(abs(value-previous.targets[key]),c.settings.speed*.05+1e-7)
                previous = current
            for key,value in c.map_targets(values).items():
                self.assertAlmostEqual(current.targets[key],value,places=5)
            c.stop()

    def test_resume_uses_new_last_commanded_pose_instead_of_initial_baseline(self):
        c = ArmController()
        c.set_calibration(demo_calibration())
        c.set_reference(c.map_targets((.9,)*5))
        c.enable(any_pose=True)
        self.right(c,1,100,(.1,)*5)
        for frame in range(2,20): self.right(c,frame,100+frame*.05,(.1,)*5)
        frozen = c.command(c.last.sampled_at).targets
        c.stop()
        c.set_reference(frozen)
        c.enable(any_pose=True)
        resumed = self.right(c,20,c.last.sampled_at+.05,(1.,)*5)
        self.assertTrue(resumed.enabled)
        for key,value in frozen.items(): self.assertAlmostEqual(resumed.targets[key],value)

    def test_any_pose_does_not_allow_missing_stale_or_changed_source(self):
        for bad in ('missing','stale','source'):
            c = ArmController()
            c.set_calibration(demo_calibration())
            c.enable(any_pose=True)
            sample = Observation(1,100,'demo',c.calibration.fist.fingers,0,True)
            if bad == 'missing': sample = replace(sample,valid=False)
            if bad == 'source': sample = replace(sample,source='camera')
            command = c.update(sample,100.31 if bad == 'stale' else 100)
            self.assertFalse(command.enabled)
            self.assertEqual(c.state,'OFF')
            command = self.right(c,2,101,(.5,)*5) if c.calibration else c.command(101)
            self.assertFalse(command.enabled)


if __name__ == '__main__': unittest.main()
