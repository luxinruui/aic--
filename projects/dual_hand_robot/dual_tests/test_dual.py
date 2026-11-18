import ast
from dataclasses import asdict, replace
import json
from pathlib import Path
import socket
import struct
import threading
import time
import unittest
from unittest.mock import Mock, patch

from dual_link.bus import Bus, encode_arms
from dual_link.client import Client
from dual_link.control import Controller
from dual_link.profile import AXES, GROUPS, IDS, load
from dual_link.protocol import APP, VERSION, JsonLines, ProtocolError, validate
from dual_link.server import Server

ROOT = Path(__file__).resolve().parent.parent


class FakeSerial:
    def __init__(self):
        self.frames, self.closed = [], False
        self.short = False
    def write(self, frame):
        self.frames.append(frame)
        return len(frame)-1 if self.short else len(frame)
    def close(self): self.closed = True


def packet(profile, seq=1, enabled=False, stage="off", mode="both", targets=None, groups=None, age_ms=0, frame_id=None):
    return dict(app=APP,v=VERSION,type="intent",profile=profile.signature,session="s"*32,challenge="c"*32,
                seq=seq,generation=1 if enabled else 0,input_session="i"*32,frame_id=seq if frame_id is None else frame_id,
                enabled=enabled,source="camera" if enabled else "none",age_ms=age_ms,stage=stage,mode=mode,
                forward=0.,yaw_left=0.,targets=targets or dict.fromkeys(AXES),groups=groups or [False]*5)


def intent(profile, message, now=100):
    return validate(message,"s"*32,"c"*32,now,0,now,profile.signature)


class WireAndCalibrationTests(unittest.TestCase):
    def setUp(self): self.profile = load(ROOT)

    def test_explicit_baseline_tool_encodes_one_slow_measured_pose_with_waist_hold(self):
        import initialize_arms
        data = initialize_arms.frame(self.profile)
        self.assertEqual(struct.unpack('<11H',data[5:-1]),(926,2048,2256,1569,0,0,3110,2048,2224,2040,5000))
        self.assertEqual(data[:5],bytes.fromhex('f55f075f16'))
        self.assertEqual(sum(data[2:]) & 255,255)
        from contextlib import redirect_stdout
        import io
        with patch('dual_link.bus.Bus') as bus, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(initialize_arms.main([]),0)
            bus.assert_not_called()
            self.assertIn('CHECK ONLY',output.getvalue())

    def test_historical_golden_frame_and_fixed_waist_slots(self):
        positions = {"1":2018,"3":2256,"4":1569,"31":1897,"33":2224,"34":2040}
        evidence = json.loads((ROOT/"docs/reference/aic_six_axis_evidence.json").read_text(encoding="utf-8"))
        frame = encode_arms(positions,include_fixed=True)
        self.assertEqual(frame.hex(), evidence["golden_four_axis_first_frame"])
        self.assertEqual(struct.unpack("<11H",frame[5:-1]),(2018,2048,2256,1569,0,0,1897,2048,2224,2040,50))
        self.assertEqual(sum(frame[2:]) & 255,255)
        hold = struct.unpack("<11H",encode_arms({"1":2018})[5:-1])
        self.assertEqual(hold,(2018,0,0,0,0,0,0,0,0,0,50))

    def test_finger_flexion_reverses_the_old_elbow_internal_angle(self):
        p = self.profile
        for name, values in (("left_elbow",(926,2018,3676)),("right_elbow",(3110,1897,400))):
            for angle, raw in zip((0,90,180),values):
                self.assertEqual(p.position(name,angle),raw)
                self.assertAlmostEqual(p.angle(name,raw),angle)
        self.assertEqual(p.position("left_lift",0),3098)
        self.assertEqual(p.position("right_lift",90),2040)
        self.assertEqual(p.position("left_upper",90),880)
        self.assertEqual(p.position("right_upper",90),3408)

    def test_uart_is_opened_once_8e1_and_startup_never_sends_arm_positions(self):
        serial = FakeSerial()
        factory = Mock(return_value=serial)
        bus = Bus(self.profile,drive=True,serial_factory=factory)
        factory.assert_called_once_with(port="/dev/ttyS0",baudrate=115200,bytesize=8,parity="E",stopbits=1,
                                        timeout=.1,write_timeout=.1,exclusive=True)
        self.assertEqual([f[2] for f in serial.frames],[8,8,8])
        bus.write_arms({"1":1000})
        bus.write_chassis((0,5,4))
        bus.write_arms({"1":1010})
        self.assertEqual(struct.unpack("<11H",serial.frames[3][5:-1])[1],2048)
        self.assertEqual(struct.unpack("<11H",serial.frames[5][5:-1])[1],0)
        bus.close()

    def test_preview_and_import_do_not_load_serial_or_open_uart(self):
        with patch("robot_link.offline.load_serial",side_effect=AssertionError("serial must remain unloaded")):
            bus = Bus(self.profile)
            bus.write_arms({"1":1000})
            bus.close()
        for path in (ROOT/"dual_link").glob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"),feature_version=(3,8))

    def test_partial_uart_write_latches_and_never_retries_motion(self):
        serial = FakeSerial()
        bus = Bus(self.profile,True,lambda **kw: serial)
        serial.short = True
        with self.assertRaises(IOError): bus.write_arms({"1":1000})
        count = len(serial.frames)
        self.assertTrue(bus.faulted and serial.closed)
        with self.assertRaises(RuntimeError): bus.write_chassis((0,0,0))
        bus.close()
        self.assertEqual(len(serial.frames),count)

    def test_protocol_rejects_legacy_asymmetric_partial_or_inactive_targets(self):
        reference = Controller(self.profile,Bus(self.profile)).reference()
        good = packet(self.profile,enabled=True,stage="prepare",targets=reference,groups=[True]*5)
        for change in ({"app":"aic-left-hand-chassis"}, {"targets":dict(reference,left_upper=1)},
                       {"groups":[True,False,True,True,True]}, {"source":"none"},
                       {"forward":.2}, {"targets":dict(reference,left_elbow=float("nan"))}):
            with self.assertRaises(ProtocolError): intent(self.profile,dict(good,**change))
        self.assertEqual(intent(self.profile,good).mode,"both")
        with self.assertRaises(ProtocolError): intent(self.profile,dict(packet(self.profile),targets=reference))


class GateAndMotionTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.profile = load(ROOT)
        self.serial = FakeSerial()
        self.bus = Bus(self.profile,True,lambda **kw:self.serial)
        self.c = Controller(self.profile,self.bus,baseline_confirmed=True,clock=lambda:self.now)
        self.c.new_session("s"*32)
        self.seq = 0
        self.generation = 1
        self.reference = self.c.reference()

    def send(self, enabled=False, stage="off", targets=None, groups=None, mode="both", **changes):
        self.seq += 1
        self.now += .05
        msg = packet(self.profile,self.seq,enabled,stage,mode,targets,groups)
        if enabled:
            msg["generation"] = self.generation
        msg.update(changes)
        accepted = self.c.process(intent(self.profile,msg,self.now),self.now)
        self.c.tick(self.now)
        return accepted

    def arm(self, groups=None, mode="both"):
        groups = [True]*5 if groups is None else groups
        self.send()
        self.generation = max(1,self.c.chassis.blocked+1)
        ref = {name:self.reference[name] if active else None for active,group in zip(groups,GROUPS) for name in group}
        self.send(True,"prepare",ref,groups,mode)
        self.assertEqual(self.c.feedback()["state"],"READY")
        return ref

    def test_default_off_prepare_no_motion_and_target_matching(self):
        before = len(self.serial.frames)
        self.send(True,"active",self.reference,[True]*5)
        self.assertIsNone(self.c.chassis.active)
        self.assertTrue(all(f[2] == 8 for f in self.serial.frames))
        self.arm()
        for _ in range(4): self.send(True,"active",self.reference,[True]*5)
        self.assertTrue(all(f[2] == 8 for f in self.serial.frames))
        self.c.disarm("pause")
        self.send()
        mismatch = dict(self.reference,left_elbow=20.)
        self.send(True,"prepare",mismatch,[True]*5,generation=2)
        self.assertIsNone(self.c.chassis.active)

    def test_hardware_arms_require_baseline_confirmation_and_camera_source(self):
        for baseline, source in ((False,"camera"),(True,"demo")):
            self.setUp()
            self.c.baseline_confirmed = baseline
            self.send()
            self.send(True,"prepare",self.reference,[True]*5,source=source)
            self.assertIsNone(self.c.chassis.active)
            self.assertTrue(all(f[2] == 8 for f in self.serial.frames))

    def test_paired_raw_motion_limits_and_chassis_combination(self):
        self.arm()
        target = dict(self.reference,left_upper=45.,right_upper=45.,left_elbow=80.,left_lift=45.)
        previous = dict(self.c.positions)
        for _ in range(80):
            self.send(True,"active",target,[True]*5,forward=.35,yaw_left=-1.)
            qleft = (2256-self.c.positions["3"])/1376
            qright = (self.c.positions["33"]-2224)/1184
            self.assertAlmostEqual(qleft,qright,delta=.001)
            for key in ("1","3","4","31","33","34"):
                self.assertLessEqual(abs(self.c.positions[key]-previous[key]),61)
            previous = dict(self.c.positions)
        self.assertGreater(self.c.issued_arm_segments,0)
        self.assertGreater(self.c.chassis.command[1],0)
        self.assertLess(self.c.chassis.command[2],0)
        frames = [f for f in self.serial.frames if f[2] == 7]
        self.assertTrue(frames)
        self.assertTrue(all(len(f) == 28 and sum(f[2:]) & 255 == 255 for f in frames))

    def test_timeout_disconnect_off_and_late_old_generation_stop_both(self):
        self.arm()
        target = dict(self.reference,left_elbow=60.)
        self.send(True,"active",target,[True]*5,forward=.35)
        count = self.c.issued_arm_segments
        self.c.tick(self.now+.151)
        self.assertEqual(self.c.chassis.command,(0,0,0))
        self.assertIsNone(self.c.chassis.active)
        self.assertEqual(self.c.issued_arm_segments,count)
        self.send(True,"active",target,[True]*5,forward=.35)
        self.assertIsNone(self.c.chassis.active)
        self.assertEqual(self.c.issued_arm_segments,count)

    def test_raw_elbow_acceleration_remains_bounded_across_middle_calibration_point(self):
        self.arm()
        target = dict(self.reference,left_elbow=150.)
        previous = self.c.positions["1"]
        previous_speed = 0.
        crossed = False
        for _ in range(160):
            self.send(True,"active",target,[True]*5)
            position = self.c.positions["1"]
            speed = (position-previous)/.05
            # Integer UART targets add at most 2 counts of second-difference noise.
            self.assertLessEqual(abs(speed-previous_speed)/.05,4800+2/(.05*.05)+1e-6)
            if position > 2018: crossed = True
            previous,previous_speed = position,speed
        self.assertTrue(crossed)

    def test_only_selected_joint_slots_move_and_mode_change_requires_pause(self):
        groups = [False,True,False,False,False]
        ref = self.arm(groups,mode="arms")
        target = dict(ref,left_elbow=30.)
        for _ in range(10): self.send(True,"active",target,groups,mode="arms")
        frames = [f for f in self.serial.frames if f[2] == 7]
        self.assertTrue(frames)
        fields = struct.unpack("<11H",frames[-1][5:-1])
        self.assertTrue(all(fields[j] == 0 for j in (2,3,4,5,6,8,9)))
        self.send(True,"active",target,groups,mode="both")
        self.assertIsNone(self.c.chassis.active)

    def test_duplicate_camera_frame_cannot_change_arm_goal(self):
        self.arm()
        target = dict(self.reference,left_elbow=30.)
        self.send(True,"active",target,[True]*5)
        last = self.c.chassis.last_frame
        goals = list(self.c.goals)
        self.send(True,"active",dict(target,left_elbow=120.),[True]*5,frame_id=last)
        self.assertEqual(self.c.goals,goals)


class TcpTests(unittest.TestCase):
    def test_actual_client_server_prepare_motion_and_off(self):
        profile = load(ROOT)
        c = Controller(profile,Bus(profile))
        server = Server(c,profile,"127.0.0.1",0)
        thread = threading.Thread(target=server.serve,daemon=True)
        thread.start()
        self.assertTrue(server.listening.wait(2))
        client = Client("127.0.0.1",profile,server.port)
        def publish(enabled=False, angles=None):
            now = time.monotonic()
            publish.frame += 1
            client.publish(dict(session="x"*32,produced_at=now,sample_age_ms=0.,enabled=enabled,input_valid=True,
                                frame_id=publish.frame,source="demo",mode="both",forward=.3 if enabled else 0.,
                                yaw_left=.4 if enabled else 0.,targets=angles or dict.fromkeys(AXES),groups=[True]*5))
        publish.frame = 0
        publish()
        client.start()
        try:
            deadline = time.monotonic()+2
            while not client.status()["connected"] and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(client.status()["connected"],client.status())
            for _ in range(5): publish();time.sleep(.03)
            ref = c.reference()
            for _ in range(6): publish(True,ref);time.sleep(.04)
            self.assertEqual(client.phase,"ACTIVE",client.status())
            target = dict(ref,left_elbow=30.,left_upper=30.,right_upper=30.)
            for _ in range(10): publish(True,target);time.sleep(.04)
            self.assertGreater(c.issued_arm_segments,0)
            self.assertGreater(c.chassis.command[1],0)
            for _ in range(3): publish();time.sleep(.04)
            self.assertIsNone(c.chassis.active)
            self.assertEqual(c.chassis.command,(0,0,0))
            count = c.issued_arm_segments
            time.sleep(.2)
            self.assertEqual(c.issued_arm_segments,count)
        finally:
            client.close();server.stop();thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_partial_tcp_message_cannot_block_independent_stop(self):
        profile = load(ROOT)
        c = Controller(profile,Bus(profile))
        server = Server(c,profile,"127.0.0.1",0)
        thread = threading.Thread(target=server.serve,daemon=True)
        thread.start(); self.assertTrue(server.listening.wait(2))
        try:
            with socket.create_connection(("127.0.0.1",server.port)) as sock:
                wire = JsonLines(sock)
                hello = wire.read()
                challenge = hello["challenge"]
                for seq, enabled, stage in ((1,False,"off"),(2,True,"prepare"),(3,True,"active")):
                    m = packet(profile,seq,enabled,stage,targets=c.reference() if enabled else None,groups=[True]*5)
                    m.update(session=hello["session"],challenge=challenge)
                    wire.send(m);challenge = wire.read()["challenge"]
                sock.sendall(b'{"unfinished":')
                time.sleep(.22)
                self.assertIsNone(c.chassis.active)
                self.assertEqual(c.chassis.command,(0,0,0))
        finally:
            server.stop();thread.join(2)


if __name__ == "__main__": unittest.main()
