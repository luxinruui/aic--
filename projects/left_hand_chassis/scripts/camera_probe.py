"""Bounded actual camera/model probe; retain counts/timing, never images."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from left_hand_drive.capture import Failure, Sample, Worker


def main():
    worker=Worker(camera=0)
    frames=[]
    started=time.monotonic()
    worker.start()
    failure=None
    try:
        while time.monotonic()-started<15 and len(frames)<30:
            item=worker.latest()
            if isinstance(item,Failure):
                failure=item.message
                break
            if isinstance(item,Sample):
                frames.append((item.observation.frame_id,item.observation.valid,
                               (time.monotonic()-item.observation.sampled_at)*1000))
            time.sleep(.02)
    finally:
        worker.stop()
        worker.join(3)
    result={"camera":0,"source":"camera","frames":len(frames),
            "unique_frames":len({x[0] for x in frames}),"valid_left_frames":sum(x[1] for x in frames),
            "max_delivery_age_ms":round(max((x[2] for x in frames),default=0),2),
            "worker_released":not worker.is_alive(),"failure":failure,
            "images_saved":False,"robot_connected":False,
            "scope":"camera open/read/mirrored MediaPipe pipeline only; no human calibration or gesture accuracy claim"}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if frames and not failure and not worker.is_alive() else 2


if __name__=="__main__":
    raise SystemExit(main())
