"""MLK relay validation on host. No physical board/GPIO/UART is used."""
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest

from mlk_relay.core import RelayController
from mlk_relay.inputs import EVENT, InputWorker, decode
from robot_link.client import Client
from robot_link.config import DEFAULT
from robot_link.control import ChassisController
from robot_link.protocol import Intent
from robot_link.server import Server


def intent(seq,enabled=True,forward=0,yaw=0,generation=1,frame=None,age=0,source="camera"):
    return Intent(seq,generation,"pc-input-session-123456",seq if frame is None else frame,enabled,source,forward,yaw,age)


class Downstream:
    def __init__(self,hardware=False):
        self.info=dict(connected=True,state="READY",hardware=hardware,inhibit=False)
        self.snapshots=[]
        self.closed=False
    def status(self): return dict(self.info)
    def publish(self,value): self.snapshots.append(dict(value))
    def close(self): self.closed=True


class GateTests(unittest.TestCase):
    def setUp(self):
        self.down=Downstream()
        self.r=RelayController(DEFAULT,self.down)
        self.r.new_session("pc-session")

    def arm(self):
        self.assertTrue(self.r.arm_local())
        self.r.process(intent(1,False,generation=0),100)
        self.assertTrue(self.r.process(intent(2),100.01))

    def test_startup_and_pc_enable_alone_cannot_move(self):
        self.r.process(intent(1,False,generation=0),100)
        self.assertFalse(self.r.process(intent(2,forward=.8),100.01))
        self.assertFalse(self.r.armed)
        self.assertFalse(self.down.snapshots[-1]["enabled"])

    def test_board_enable_alone_does_not_forward_an_old_pc_target(self):
        self.r.process(intent(1,forward=.7),100)
        self.r.arm_local()
        self.assertFalse(self.down.snapshots[-1]["enabled"])
        self.assertTrue(self.r.feedback()["rearm_required"])

    def test_original_session_frame_and_age_are_preserved(self):
        self.arm()
        self.r.process(intent(3,forward=.6,yaw=-.3,age=72),100.02)
        s=self.down.snapshots[-1]
        self.assertEqual((s["session"],s["frame_id"],s["sample_age_ms"]),("pc-input-session-123456",3,72))
        self.assertEqual((s["forward"],s["yaw_left"]),(.6,-.3))
        client=Client("127.0.0.1",DEFAULT)
        client.info["hardware"]=False
        client.publish(s)
        packet=client.packet("s"*32,"c"*32,1,100.10)
        self.assertAlmostEqual(packet["age_ms"],152)

    def test_repeated_input_does_not_get_a_new_timestamp_or_target(self):
        self.arm()
        self.r.process(intent(3,forward=.5,age=70),100.02)
        count=len(self.down.snapshots)
        self.r.process(intent(4,forward=1,frame=3,age=0),100.05)
        self.assertEqual(len(self.down.snapshots),count)
        self.assertEqual(self.down.snapshots[-1]["produced_at"],100.02)

    def test_local_stop_revokes_enable_and_requires_pc_off_new_generation(self):
        self.arm()
        self.r.process(intent(3,forward=.5),100.02)
        self.r.stop_local()
        self.assertFalse(self.r.armed)
        self.assertFalse(self.r.process(intent(4,forward=.5),100.03))
        self.r.arm_local()
        self.assertFalse(self.r.process(intent(5,forward=.5),100.04))
        self.r.process(intent(6,False,generation=1),100.05)
        self.assertTrue(self.r.process(intent(7,generation=2),100.06))

    def test_upstream_timeout_sends_off_and_revokes_local_enable(self):
        self.arm()
        self.r.process(intent(3,forward=.5),100.02)
        self.r.tick(100.2)
        self.assertFalse(self.r.armed)
        self.assertFalse(self.down.snapshots[-1]["enabled"])

    def test_downstream_disconnect_or_rejection_stops(self):
        self.arm()
        self.down.info["connected"]=False
        self.r.tick(100.02)
        self.assertFalse(self.r.armed)
        self.assertFalse(self.r.arm_local())

    def test_hardware_relay_does_not_accept_demo(self):
        self.down.info["hardware"]=True
        self.r.arm_local()
        self.r.process(intent(1,False,generation=0),100)
        self.assertFalse(self.r.process(intent(2,source="demo"),100.01))
        self.assertFalse(self.down.snapshots[-1]["enabled"])

    def test_close_and_pc_disconnect_send_off(self):
        self.arm()
        self.r.disarm("PC disconnected",force=True)
        self.assertFalse(self.r.armed)
        self.r.close()
        self.assertTrue(self.down.closed)
        self.assertFalse(self.down.snapshots[-1]["enabled"])


class InputTests(unittest.TestCase):
    def test_press_release_repeat_and_stop_codes(self):
        data=b"".join(EVENT.pack(0,0,1,code,value) for code,value in ((10,1),(10,0),(10,2),(11,1)))
        self.assertEqual(decode(data,10,11),["arm","stop"])
        self.assertEqual(decode(EVENT.pack(0,0,0,10,1),10,11),[])

    def test_incomplete_event_and_unknown_device_are_rejected(self):
        with self.assertRaises(ValueError): decode(b"x",10,11)
        with self.assertRaises(ValueError): InputWorker("/dev/mem",10,11,lambda _:None)
        with self.assertRaises(ValueError): InputWorker("/dev/input/event0",10,10,lambda _:None)

    def test_runtime_probe_is_stdlib_only_and_does_not_connect(self):
        root=Path(__file__).resolve().parent.parent
        result=subprocess.run([sys.executable,"-S","run_mlk_relay.py","--check-runtime"],cwd=str(root),
                              capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('"camera_or_uart_required": false',result.stdout)


class Writer:
    def __init__(self): self.commands=[]
    def write(self,value): self.commands.append(value)
    def close(self): pass


def wait_for(predicate,timeout=2):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if predicate(): return True
        time.sleep(.01)
    return False


class TwoHopTests(unittest.TestCase):
    def setUp(self):
        self.writer=Writer()
        self.backpack=ChassisController(DEFAULT,self.writer)
        self.back_server=Server(self.backpack,DEFAULT,"127.0.0.1",0)
        self.back_thread=threading.Thread(target=self.back_server.serve,daemon=True)
        self.back_thread.start()
        self.assertTrue(self.back_server.listening.wait(2))
        self.down=Client("127.0.0.1",DEFAULT,self.back_server.port)
        self.down.start()
        self.assertTrue(wait_for(lambda:self.down.status()["connected"]))
        self.relay=RelayController(DEFAULT,self.down)
        self.front=Server(self.relay,DEFAULT,"127.0.0.1",0)
        self.front_thread=threading.Thread(target=self.front.serve,daemon=True)
        self.front_thread.start()
        self.assertTrue(self.front.listening.wait(2))
        self.pc=Client("127.0.0.1",DEFAULT,self.front.port)
        self.pc.start()
        self.assertTrue(wait_for(lambda:self.pc.status()["connected"]))
        self.n=0

    def tearDown(self):
        self.pc.close()
        self.front.stop();self.front_thread.join(2)
        self.back_server.stop();self.back_thread.join(2)
        self.assertFalse(self.front_thread.is_alive() or self.back_thread.is_alive())

    def publish(self,seconds,enabled=False,forward=0,yaw=0):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            self.n+=1
            self.pc.publish(dict(session="pc-camera-session-123456",seq=self.n,produced_at=time.monotonic(),
                valid_for_ms=150,enabled=enabled,input_valid=True,source="camera",frame_id=self.n,
                sample_age_ms=0,forward=forward,yaw_left=yaw))
            time.sleep(.02)

    def start_moving(self):
        self.assertTrue(self.relay.arm_local())
        self.publish(.18)
        self.publish(.7,True,.6,.3)
        self.assertTrue(any(x[1]>0 and x[2]>0 for x in self.writer.commands))

    def test_two_hops_board_stop_cannot_be_overridden_by_old_pc_on(self):
        self.start_moving()
        self.assertEqual(self.pc.status()["via"],"mlk-relay")
        self.relay.stop_local()
        self.publish(.3,True,.6,.3)
        self.assertEqual(self.backpack.command,(0,0,0))
        self.assertFalse(self.relay.armed)
        self.assertTrue(self.relay.arm_local())
        self.publish(.15)
        self.publish(.5,True,.6,0)
        self.assertGreater(self.backpack.command[1],0)

    def test_backpack_link_loss_stops_and_revokes_board_enable(self):
        self.start_moving()
        self.down.close()
        self.assertTrue(wait_for(lambda:not self.relay.armed))
        self.assertTrue(wait_for(lambda:self.backpack.command==(0,0,0)))
        self.publish(.2,True,.6,.3)
        self.assertEqual(self.backpack.command,(0,0,0))

    def test_pc_disconnect_stops_both_hops(self):
        self.start_moving()
        self.pc.close()
        self.assertTrue(wait_for(lambda:not self.relay.armed))
        self.assertTrue(wait_for(lambda:self.backpack.command==(0,0,0)))


if __name__=="__main__": unittest.main(verbosity=2)
