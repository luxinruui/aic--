"""All hardware interfaces below are fakes. Never opens an actual UART/camera."""
from dataclasses import replace
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from robot_link.client import Client
from robot_link.codec import SerialWriter, encode_mode, encode_move
from robot_link.config import DEFAULT, signature, validate as validate_config
from robot_link.control import ChassisController
from robot_link.protocol import APP, VERSION, Intent, JsonLines, ProtocolError, check_reply, validate
from robot_link.server import Server


class FakeSerial:
    def __init__(self, **options):
        self.options, self.frames = options, []
        self.closed = False
        self.short = False

    def write(self, data):
        self.frames.append(data)
        return len(data) - 1 if self.short else len(data)

    def close(self):
        self.closed = True


class Recorder:
    def __init__(self):
        self.commands = []
        self.failed = self.closed = False

    def write(self, command):
        if self.failed:
            raise IOError("simulated serial failure")
        self.commands.append(command)

    def close(self):
        self.closed = True


def intent(seq, enabled=True, forward=0, yaw=0, generation=1, frame=None, age=0, source="camera"):
    return Intent(seq,generation,"input-session-1234567890",seq if frame is None else frame,
                  enabled,source,forward,yaw,age)


def snapshot(enabled=False, forward=0, yaw=0, frame=1, source="camera", now=None):
    return dict(session="input-session-1234567890",seq=frame,produced_at=time.monotonic() if now is None else now,
                valid_for_ms=150,enabled=enabled,input_valid=True,source=source,frame_id=frame,
                sample_age_ms=0,forward=forward,yaw_left=yaw)


def wait_for(predicate, timeout=2):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate(): return True
        time.sleep(.01)
    return False


class CodecTests(unittest.TestCase):
    def test_offline_dependency_import_and_hash_guard(self):
        from robot_link import offline
        self.assertEqual(offline.load_serial().VERSION,"3.5")
        with patch.object(offline,"SHA256","0"*64):
            with self.assertRaises(RuntimeError): offline.load_serial()

    def test_default_receiver_import_loads_no_camera_gui_or_serial_stack(self):
        root=Path(__file__).resolve().parent.parent
        result=subprocess.run([sys.executable,"-S","-c",
            "import sys,run_backpack; assert not any(x in sys.modules for x in ('serial','cv2','mediapipe','tkinter')); print('preview imports clean')"],
            cwd=str(root),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_all_saved_vendor_vectors_match_byte_for_byte(self):
        vectors=json.loads((Path(__file__).parent/"vendor_vectors.json").read_text(encoding="utf-8"))
        self.assertEqual(encode_mode().hex(),vectors["mode0_hex"])
        for item in vectors["move_vectors"]:
            self.assertEqual(encode_move(*item["input"]).hex(),item["hex"])

    def test_only_chassis_startup_and_exit_stop_frames(self):
        serial=FakeSerial()
        writer=SerialWriter(DEFAULT,serial_factory=lambda **opts: (setattr(serial,"options",opts) or serial))
        self.assertEqual(serial.frames,[encode_move(0,0,0),encode_mode(),encode_move(0,0,0)])
        self.assertEqual((serial.options["baudrate"],serial.options["bytesize"],serial.options["parity"],serial.options["stopbits"]),(115200,8,"E",1))
        writer.write((180,7,-4))
        writer.close()
        self.assertTrue(serial.closed)
        self.assertTrue(all(f[2]==8 for f in serial.frames))
        self.assertEqual(serial.frames[-1],encode_move(0,0,0))

    def test_short_write_latches_fault_closes_and_never_retries(self):
        serial=FakeSerial()
        writer=SerialWriter(DEFAULT,serial_factory=lambda **_:serial)
        serial.short=True
        with self.assertRaises(IOError): writer.write((0,5,2))
        count=len(serial.frames)
        with self.assertRaises(RuntimeError): writer.write((0,5,2))
        writer.close()
        self.assertEqual(len(serial.frames),count)
        self.assertTrue(writer.faulted and serial.closed)

    def test_initialization_failure_releases_port(self):
        serial=FakeSerial()
        serial.short=True
        with self.assertRaises(IOError): SerialWriter(DEFAULT,serial_factory=lambda **_:serial)
        self.assertTrue(serial.closed)

    def test_invalid_encoder_values_are_rejected(self):
        for values in ((360,1,1),(0,101,0),(0,1,101),(0,True,0),(0,1,0,1000)):
            with self.assertRaises(ValueError): encode_move(*values)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.profile=signature(DEFAULT)
        self.packet=dict(app=APP,v=VERSION,type="intent",profile=self.profile,session="s"*32,challenge="c"*32,
                         seq=1,generation=1,input_session="i"*32,frame_id=1,enabled=True,source="camera",
                         forward=.5,yaw_left=-.3,age_ms=10)

    def check(self, p=None, now=100.02):
        return validate(self.packet if p is None else p,"s"*32,"c"*32,100,0,now,self.profile)

    def test_valid_message_combines_age_without_synchronized_clocks(self):
        self.assertAlmostEqual(self.check().age_ms,30)

    def test_wrong_app_profile_session_challenge_and_replay_rejected(self):
        for field,value in (("app","aic-unified/1"),("profile","x"*64),("session","x"*32),("challenge","x"*32),("seq",0)):
            with self.assertRaises(ProtocolError): self.check(dict(self.packet,**{field:value}))

    def test_types_nonfinite_range_and_disabled_motion_rejected(self):
        for field,value in (("v",True),("frame_id",True),("enabled",1),("forward",float("nan")),
                            ("yaw_left",2),("source","SIMULATED"),("input_session",""),("age_ms",-1)):
            with self.assertRaises(ProtocolError): self.check(dict(self.packet,**{field:value}))
        with self.assertRaises(ProtocolError): self.check(dict(self.packet,enabled=False))

    def test_expired_combined_age_and_challenge_rejected(self):
        with self.assertRaises(ProtocolError): self.check(dict(self.packet,age_ms=290))
        with self.assertRaises(ProtocolError): self.check(now=100.31)

    def test_jsonl_fragmentation_duplicate_keys_and_size_limit(self):
        a,b=socket.socketpair()
        self.addCleanup(a.close);self.addCleanup(b.close)
        wire=JsonLines(a)
        b.sendall(b'{"x":')
        b.sendall(b'1}\n{"x":1,"x":2}\n')
        self.assertEqual(wire.read(),{"x":1})
        with self.assertRaises(ProtocolError): wire.read()
        b.sendall(b'x'*2049)
        with self.assertRaises(ProtocolError): wire.read()

    def test_receiver_requires_explicit_hardware_identity(self):
        with self.assertRaises(ProtocolError): check_reply({},"hello",self.profile)

    def test_config_mismatch_and_wrong_uart_are_rejected(self):
        self.assertNotEqual(signature(DEFAULT),signature(dict(DEFAULT,max_translation=19)))
        with self.assertRaises(ValueError): validate_config(dict(DEFAULT,baudrate=9600))


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.writer=Recorder()
        self.c=ChassisController(DEFAULT,self.writer)
        self.c.new_session("test-session")

    def arm(self):
        self.c.process(intent(1,enabled=False,generation=0),100)
        self.assertTrue(self.c.process(intent(2),100.01))
        self.assertEqual(self.c.state,"READY")

    def test_off_then_zero_enable_required(self):
        self.assertFalse(self.c.process(intent(1,forward=.5),100))
        self.assertEqual(self.c.state,"DISARMED")
        self.c.new_session("new")
        self.arm()

    def test_targets_combine_and_ramp_within_caps(self):
        self.arm()
        self.c.process(intent(3,forward=1,yaw=-1),100.02)
        self.c.tick(100.02)
        self.assertEqual(self.c.command,(0,2,-6))
        for i in range(1,12):
            self.c.process(intent(3+i,forward=1,yaw=-1),100.02+i*.05)
            self.c.tick(100.02+i*.05)
        self.assertEqual(self.c.command,(0,20,-60))

    def test_axis_release_stops_immediately_and_reverse_ramps(self):
        self.arm()
        self.c.process(intent(3,forward=1,yaw=1),100.02);self.c.tick(100.02)
        self.c.process(intent(4,forward=-1,yaw=0),100.07);self.c.tick(100.07)
        self.assertEqual(self.c.command,(0,0,0))
        self.c.process(intent(5,forward=-1,yaw=0),100.12);self.c.tick(100.12)
        self.assertEqual(self.c.command,(180,2,0))

    def test_watchdog_stops_without_a_new_message_and_blocks_generation(self):
        self.arm()
        self.c.process(intent(3,forward=.5),100.02);self.c.tick(100.02)
        self.c.tick(100.18)
        self.assertEqual(self.writer.commands[-1],(0,0,0))
        self.assertFalse(self.c.process(intent(4,forward=.5),100.2))
        self.assertEqual(self.c.state,"DISARMED")

    def test_repeated_frame_cannot_extend_source_lifetime(self):
        self.arm()
        self.c.process(intent(3,forward=.5,age=100),100.02)
        self.c.process(intent(4,forward=1,frame=3,age=0),100.1)
        self.assertEqual(self.c.goal,(10,0))
        self.assertLessEqual(self.c.deadline,100.22)
        self.c.tick(100.23)
        self.assertEqual(self.c.state,"DISARMED")

    def test_demo_input_is_rejected_by_hardware_but_allowed_in_preview(self):
        self.c.process(intent(1,enabled=False,generation=0),100)
        self.assertFalse(self.c.process(intent(2,source="demo"),100.01))
        preview=ChassisController(DEFAULT)
        preview.new_session("preview")
        preview.process(intent(1,enabled=False,generation=0),100)
        self.assertTrue(preview.process(intent(2,source="demo"),100.01))

    def test_uart_fault_latches_and_releases(self):
        self.arm()
        self.c.process(intent(3,forward=.5),100.02)
        self.writer.failed=True
        self.c.tick(100.02)
        self.assertEqual(self.c.state,"FAULT")
        self.assertTrue(self.writer.closed)
        self.c.new_session("new")
        self.assertEqual(self.c.state,"FAULT")

    def test_input_session_or_generation_change_requires_off(self):
        self.arm()
        self.c.process(replace(intent(3),input_session="different-session-12345"),100.02)
        self.assertEqual(self.c.state,"DISARMED")


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.c=Client("127.0.0.1",DEFAULT)
        self.c.info["hardware"]=False

    def test_first_enable_is_zero_then_matching_ready_allows_motion(self):
        self.c.publish(snapshot(True,.5,-.4,now=100))
        first=self.c.packet("s"*32,"c"*32,1,100)
        self.assertTrue(first["enabled"])
        self.assertEqual((first["forward"],first["yaw_left"]),(0,0))
        self.c.accept_ack(dict(state="READY",hardware=False,seq=1,generation=first["generation"],rearm_required=False))
        moving=self.c.packet("s"*32,"c"*32,2,100.05)
        self.assertEqual((moving["forward"],moving["yaw_left"]),(.5,-.4))

    def test_stale_ui_snapshot_latches_until_off(self):
        self.c.publish(snapshot(True,.5,now=100))
        p=self.c.packet("s"*32,"c"*32,1,100.2)
        self.assertFalse(p["enabled"])
        self.c.publish(snapshot(True,.5,now=100.3))
        self.assertFalse(self.c.packet("s"*32,"c"*32,2,100.3)["enabled"])
        self.c.publish(snapshot(False,now=100.4))
        self.c.publish(snapshot(True,.5,now=100.5))
        self.assertTrue(self.c.packet("s"*32,"c"*32,3,100.5)["enabled"])

    def test_hardware_client_rejects_demo_and_invalid_host(self):
        self.c.info["hardware"]=True
        self.c.publish(snapshot(True,.5,source="demo",now=100))
        self.assertFalse(self.c.packet("s"*32,"c"*32,1,100)["enabled"])
        with self.assertRaises(ValueError): Client("172.20.10",DEFAULT)


class TcpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fake=FakeSerial()
        writer=SerialWriter(DEFAULT,serial_factory=lambda **_:self.fake)
        self.control=ChassisController(DEFAULT,writer)
        self.server=Server(self.control,DEFAULT,"127.0.0.1",0)
        self.thread=threading.Thread(target=self.server.serve,daemon=True)
        self.thread.start()
        self.assertTrue(self.server.listening.wait(2))
        self.client=Client("127.0.0.1",DEFAULT,self.server.port)
        self.client.start()
        self.assertTrue(wait_for(lambda:self.client.status()["connected"]))
        self.counter=0

    def tearDown(self):
        self.client.close()
        self.server.stop()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def send_for(self,seconds,enabled=False,forward=0,yaw=0):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            self.counter+=1
            self.client.publish(snapshot(enabled,forward,yaw,self.counter))
            time.sleep(.02)

    def test_real_tcp_to_real_encoder_fake_uart_move_reverse_and_stop(self):
        self.send_for(.15)
        self.send_for(.75,True,.7,-.5)
        self.assertTrue(any(struct.unpack("<HHhH",f[5:-1])[1]>0 and struct.unpack("<HHhH",f[5:-1])[2]<0
                            for f in self.fake.frames if len(f)==14))
        self.send_for(.9,True,-.5,.4)
        self.assertTrue(any(struct.unpack("<HHhH",f[5:-1])[0]==180 for f in self.fake.frames if len(f)==14))
        self.send_for(.15)
        self.assertTrue(wait_for(lambda:self.control.command==(0,0,0)))
        self.assertTrue(all(f[2]==8 for f in self.fake.frames))

    def test_stalled_pc_stops_and_resumed_old_enable_cannot_restart(self):
        self.send_for(.15)
        self.send_for(.4,True,.5)
        time.sleep(.4)
        self.assertTrue(wait_for(lambda:self.control.command==(0,0,0)))
        self.assertTrue(self.client.status()["inhibit"])
        self.send_for(.25,True,.5)
        self.assertEqual(self.control.command,(0,0,0))
        self.send_for(.15)
        self.send_for(.4,True,.5)
        self.assertTrue(self.control.command[1]>0)

    def test_disconnect_sends_stop(self):
        self.send_for(.15)
        self.send_for(.4,True,.5)
        self.client.close()
        self.assertTrue(wait_for(lambda:self.control.command==(0,0,0)))

    def test_config_mismatch_refuses_connection_before_motion(self):
        self.client.close()
        bad=Client("127.0.0.1",dict(DEFAULT,max_translation=19),self.server.port)
        bad.start()
        self.assertTrue(wait_for(lambda:bool(bad.status()["error"])))
        self.assertFalse(bad.status()["connected"])
        bad.close()
        self.assertTrue(all(struct.unpack("<HHhH",f[5:-1])[1:3]==(0,0)
                            for f in self.fake.frames if len(f)==14))

    def test_independent_watchdog_runs_while_tcp_waits_on_partial_line(self):
        self.client.close()
        connection=socket.create_connection(("127.0.0.1",self.server.port),timeout=1)
        self.addCleanup(connection.close)
        wire=JsonLines(connection)
        hello=wire.read()
        session,challenge=hello["session"],hello["challenge"]
        for seq,enabled,forward in ((1,False,0),(2,True,0),(3,True,.5)):
            wire.send(dict(app=APP,v=VERSION,type="intent",profile=signature(DEFAULT),session=session,
                challenge=challenge,seq=seq,generation=0 if not enabled else 1,
                input_session="input-session-1234567890",frame_id=seq,source="camera",enabled=enabled,
                forward=forward,yaw_left=0,age_ms=0))
            challenge=wire.read()["challenge"]
        self.assertTrue(wait_for(lambda:self.control.command[1]>0))
        connection.sendall(b'{')
        self.assertTrue(wait_for(lambda:self.control.state=="DISARMED",timeout=.25))
        self.assertEqual(self.control.command,(0,0,0))
        self.assertIn("超时",self.control.reason)


if __name__=="__main__":
    unittest.main(verbosity=2)
