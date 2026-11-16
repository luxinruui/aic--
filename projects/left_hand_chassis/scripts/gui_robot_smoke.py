"""Real Tk + local TCP PREVIEW. Synthetic hand input, no UART/camera."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from left_hand_drive.robot_gui import RobotApp
from robot_link.config import DEFAULT
from robot_link.control import ChassisController
from robot_link.server import Server


def main():
    control=ChassisController(DEFAULT)
    server=Server(control,DEFAULT,"127.0.0.1",0)
    thread=threading.Thread(target=server.serve,daemon=True)
    thread.start()
    assert server.listening.wait(2)
    try:
        with tempfile.TemporaryDirectory() as directory:
            project=Path(directory)
            (project/"robot_config.json").write_text(json.dumps(DEFAULT),encoding="utf-8")
            root=tk.Tk()
            root.withdraw()  # Background regression must not steal live control focus.
            app=RobotApp(root,project,demo=True)
            root.update()
            assert app.turn_gain.get()==100
            assert app.controller.turn_gain==1.0
            app.connect_robot("127.0.0.1",server.port)
            root.after(500,app.enable)
            root.after(1800,root.quit)
            root.mainloop()
            assert app.client.status()["connected"],app.client.status()
            assert "背包预览" in app.mode_label.cget("text")
            assert root.title()=="左手底盘控制 · 机器人接入 v1.2"
            app.connection_dialog()
            root.update()
            assert app.controller.state=="OFF"
            assert app.connection_window.winfo_exists()
            app.connection_window.destroy()
            app.stop("GUI network smoke STOP")
            until=time.monotonic()+.2
            while time.monotonic()<until:
                root.update()
                time.sleep(.02)
            assert control.command==(0,0,0)
            app.close()
        print("Robot Tk controls, connection dialog, TCP PREVIEW feedback and STOP passed; synthetic data only; no UART/camera.")
        return 0
    finally:
        server.stop()
        thread.join(2)
        assert not thread.is_alive()


if __name__=="__main__":
    raise SystemExit(main())
