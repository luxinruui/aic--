"""Read-only Linux input discovery. No GPIO/MMIO, firmware or network changes."""
import argparse
import os
from pathlib import Path
import re
import select
import sys
import time
from mlk_relay.inputs import EVENT


def main():
    p=argparse.ArgumentParser(description="Read-only MLK Linux input-event probe")
    p.add_argument("--device",help="verified /dev/input/eventN; omit to list kernel input devices")
    p.add_argument("--seconds",type=int,default=15)
    a=p.parse_args()
    if sys.platform!="linux": p.error("This probe runs on the Linux board")
    if not a.device:
        path=Path("/proc/bus/input/devices")
        print(path.read_text() if path.is_file() else "Kernel input devices file is absent")
        return 0
    if not re.fullmatch(r"/dev/input/event[0-9]+",a.device) or not 1<=a.seconds<=60:
        p.error("Use a verified eventN path and 1..60 seconds")
    print("Read-only input_event bytes:",EVENT.size,flush=True)
    fd=os.open(a.device,os.O_RDONLY|os.O_NONBLOCK)
    try:
        end=time.monotonic()+a.seconds
        while time.monotonic()<end:
            ready,_,_=select.select([fd],[],[],.2)
            if not ready: continue
            data=os.read(fd,EVENT.size*16)
            if len(data)%EVENT.size: raise ValueError("Unexpected input_event layout")
            for i in range(0,len(data),EVENT.size):
                _,_,kind,code,value=EVENT.unpack_from(data,i)
                print("type=%d code=%d value=%d"%(kind,code,value),flush=True)
    finally: os.close(fd)
    return 0


if __name__=="__main__": raise SystemExit(main())
