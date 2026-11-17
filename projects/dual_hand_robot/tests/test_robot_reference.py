from dataclasses import replace
from pathlib import Path
import unittest

from dual_link.client import Client
from dual_link.profile import AXES, load
from left_hand_drive.core import Observation
from left_hand_drive.upper import ArmController, demo_calibration

ROOT = Path(__file__).resolve().parent.parent


class ReferenceTests(unittest.TestCase):
    def test_first_arm_enable_matches_receiver_pose_instead_of_preview_neutral(self):
        c = ArmController()
        cal = demo_calibration()
        c.set_calibration(cal)
        reference = {name:90. if name.endswith("lift") else 0. for name in AXES}
        c.set_reference(reference)
        c.enable()
        for i in range(15):
            command = c.update(Observation(i+1,100+i*.05,"demo",cal.neutral.fingers,0,True),100+i*.05)
        self.assertFalse(command.enabled)
        for i in range(15,30):
            command = c.update(Observation(i+1,100+i*.05,"demo",cal.opened.fingers,70,True),100+i*.05)
            if command.enabled:
                self.assertEqual(command.targets,reference)
                break
        self.assertTrue(command.enabled)

    def test_unreachable_reference_or_unpaired_reference_cannot_enable(self):
        c = ArmController()
        ref = dict.fromkeys(AXES,0.)
        ref["left_elbow"] = 150.
        with self.assertRaises(ValueError):c.set_reference(ref)
        ref["left_elbow"] = 0.
        ref["right_upper"] = 20.
        with self.assertRaises(ValueError):c.set_reference(ref)

    def test_network_preparation_uses_reference_and_stale_ui_latches_off(self):
        p = load(ROOT)
        client = Client("127.0.0.1",p)
        ref = {name:90. if name.endswith("lift") else 0. for name in AXES}
        client.info.update(hardware=True,baseline_confirmed=True,arm_reference=ref)
        snap = dict(session="s"*32,produced_at=100.,sample_age_ms=0.,enabled=True,input_valid=True,
                    frame_id=1,source="camera",mode="both",forward=.35,yaw_left=1.,
                    targets=dict(ref,left_elbow=30.),groups=[True]*5)
        client.publish(snap)
        packet = client.packet("a"*32,"b"*32,1,100.)
        self.assertEqual(packet["stage"],"prepare")
        self.assertEqual((packet["forward"],packet["yaw_left"]),(0.,0.))
        self.assertEqual(packet["targets"],ref)
        client.phase = "ACTIVE"
        self.assertFalse(client.packet("a"*32,"b"*32,2,100.151)["enabled"])
        client.publish(dict(snap,produced_at=100.2,frame_id=2))
        self.assertFalse(client.packet("a"*32,"b"*32,3,100.2)["enabled"])
        client.publish(dict(snap,enabled=False,produced_at=100.25))
        client.publish(dict(snap,produced_at=100.3,frame_id=3))
        self.assertTrue(client.packet("a"*32,"b"*32,4,100.3)["enabled"])

    def test_hardware_network_rejects_demo_and_unconfirmed_arm_baseline(self):
        p = load(ROOT)
        for source,baseline in (("demo",True),("camera",False)):
            c = Client("127.0.0.1",p)
            c.info.update(hardware=True,baseline_confirmed=baseline)
            c.publish(dict(session="s"*32,produced_at=100.,sample_age_ms=0.,enabled=True,input_valid=True,
                frame_id=1,source=source,mode="arms",forward=0.,yaw_left=0.,targets=dict.fromkeys(AXES),groups=[True]*5))
            self.assertFalse(c.packet("a"*32,"b"*32,1,100.)["enabled"])


if __name__ == "__main__":unittest.main()
