"""Optional Linux evdev adapter. Device and keycodes MUST be measured on-board."""
import os
import re
import select
import struct
import threading

EVENT = struct.Struct("@llHHi")


def decode(data, arm_key, stop_key):
    actions = []
    if len(data) % EVENT.size:
        raise ValueError("Incomplete native Linux input_event")
    for offset in range(0,len(data),EVENT.size):
        _,_,kind,code,value = EVENT.unpack_from(data,offset)
        if kind == 1 and value == 1:  # EV_KEY, press; ignore release/repeat.
            if code == stop_key:
                actions.append("stop")
            elif code == arm_key:
                actions.append("arm")
    return actions


class InputWorker(threading.Thread):
    def __init__(self, device, arm_key, stop_key, callback):
        super().__init__(name="mlk-input-events",daemon=True)
        if not re.fullmatch(r"/dev/input/event[0-9]+",device):
            raise ValueError("Use the verified /dev/input/eventN device")
        if type(arm_key) is not int or type(stop_key) is not int or not (0 <= arm_key <= 767 and 0 <= stop_key <= 767) or arm_key == stop_key:
            raise ValueError("Use distinct measured ARM and STOP keycodes")
        self.device,self.arm_key,self.stop_key,self.callback = device,arm_key,stop_key,callback
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()

    def run(self):
        descriptor = None
        try:
            descriptor = os.open(self.device,os.O_RDONLY|os.O_NONBLOCK)
            while not self.stopping.is_set():
                ready,_,_ = select.select([descriptor],[],[],.1)
                if not ready:
                    continue
                data = os.read(descriptor,EVENT.size*16)
                if not data:
                    raise OSError("Input device disconnected")
                actions = decode(data,self.arm_key,self.stop_key)
                # STOP takes priority if both keys appear in one read batch.
                if "stop" in actions:
                    self.callback("stop")
                elif "arm" in actions:
                    self.callback("arm")
        except Exception as error:
            self.callback("io_fault:"+str(error))
        finally:
            if descriptor is not None:
                os.close(descriptor)
