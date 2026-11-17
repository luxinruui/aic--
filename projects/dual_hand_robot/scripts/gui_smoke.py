"""Actual Tk/Pillow rendering, synthetic input only. No camera opened."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from left_hand_drive.gui import App


def main():
    with tempfile.TemporaryDirectory() as directory:
        root=tk.Tk()
        app=App(root,directory,demo=True)
        root.update()
        app.demo_started=time.monotonic()-3
        root.after(900,root.quit)
        root.mainloop()
        root.update()
        assert app.photo is not None
        assert root.title()=="左手底盘控制 · 电脑端 v1.0"
        assert len(app.cal_buttons)==3
        command=json.loads((Path(directory)/"runtime/latest_command.json").read_text(encoding="utf-8"))
        assert command["source"]=="demo"
        app.stop("GUI smoke stop")
        command=json.loads((Path(directory)/"runtime/latest_command.json").read_text(encoding="utf-8"))
        assert not command["enabled"] and command["forward"]==command["yaw_left"]==0
        app.close()
        print("Actual Tk controls, CJK labels, Pillow preview and STOP snapshot passed (synthetic input only).")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
