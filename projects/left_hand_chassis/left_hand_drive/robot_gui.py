"""Optional robot connection UI; existing camera/calibration behavior is reused."""
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from .gui import App
from robot_link.client import Client
from robot_link.config import load
from robot_link.protocol import PORT


class RobotApp(App):
    def __init__(self, root, project, demo=False, camera=0):
        self.client = None
        self.connection_window = None
        self.network_message = None
        self.last_network_log = None
        self.robot_config = load(Path(project) / "robot_config.json")
        super().__init__(root, project, demo, camera)
        root.title("左手底盘控制 · 机器人接入 v1.2")
        self.mode_label.configure(text="未连接背包")
        ttk.Button(self.toolbar, text="连接机器人…", command=self.connection_dialog).pack(side="right", padx=8)
        root.after(50, self.poll_network)

    def write_snapshot(self, command):
        super().write_snapshot(command)
        if self.client:
            self.client.publish(command)

    def connection_dialog(self):
        self.stop("设置连接，已暂停")
        if self.connection_window and self.connection_window.winfo_exists():
            self.connection_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.connection_window = window
        window.title("控制端连接：背包或米联客")
        window.geometry("520x270")
        window.resizable(False, False)
        box = ttk.Frame(window, padding=18)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="电脑与背包连接同一可互通局域网/手机热点").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=14)
        ttk.Label(row, text="目标IPv4").pack(side="left")
        host = tk.StringVar(value=self.client.host if self.client else "")
        entry = ttk.Entry(row, textvariable=host, width=19)
        entry.pack(side="left", padx=8)
        ttk.Label(row, text="端口").pack(side="left")
        port = tk.StringVar(value=str(self.client.port if self.client else PORT))
        ttk.Entry(row, textvariable=port, width=6).pack(side="left", padx=8)
        message = tk.StringVar(value="背包默认运行预览；--drive 才是实机模式")
        self.network_message = message
        ttk.Label(box, textvariable=message, wraplength=470).pack(anchor="w", pady=10)
        buttons = ttk.Frame(box)
        buttons.pack(fill="x", pady=8)

        def connect():
            try:
                self.connect_robot(host.get(), int(port.get()))
                message.set("连接中；关闭此设置窗口后再手动启用")
            except (ValueError, OSError) as error:
                message.set(str(error))
        ttk.Button(buttons, text="连接", command=connect).pack(side="left")
        ttk.Button(buttons, text="断开并停车", command=self.disconnect_robot).pack(side="left", padx=8)
        ttk.Button(buttons, text="返回控制窗口", command=window.destroy).pack(side="right")
        entry.focus_set()

    def connect_robot(self, host, port=PORT):
        self.disconnect_robot()
        self.robot_config = load(self.project / "robot_config.json")
        self.client = Client(host, self.robot_config, port)
        self.client.publish(self.controller.command(time.monotonic()))
        self.client.start()
        if self.log:
            self.log.event("robot_connect_requested", port=port)

    def disconnect_robot(self):
        self.stop("连接已关闭，已停车")
        if self.client:
            self.client.close()
            self.client = None
        self.mode_label.configure(text="未连接背包")
        self.enable_button.configure(text="启用控制预览")

    def enable(self):
        if self.client:
            status = self.client.status()
            if not status["connected"]:
                self.stop("背包未连接，请先连接或断开后使用本地预览")
                return
            if status["hardware"] and self.demo:
                self.stop("实机拒绝合成演示；请用摄像头完成本次校准")
                return
        super().enable()

    def poll_network(self):
        if self.client:
            status = self.client.status()
            hardware = status["hardware"]
            if self.log:
                self.log.info["hardware_connected"] |= bool(status["connected"] and hardware)
                key = (id(self.log), status["connected"], hardware, status["state"], status.get("error", ""))
                if key != self.last_network_log:
                    self.log.event("robot_connection", connected=status["connected"],
                                   hardware=hardware, state=status["state"],
                                   error=status.get("error", ""))
                    self.last_network_log = key
            if status["connected"]:
                label = ("板卡/" if status.get("via")=="mlk-relay" else "") + ("实机" if hardware else "背包预览") + " · " + status["state"]
                self.mode_label.configure(text=label)
                self.enable_button.configure(text="启用底盘控制" if hardware else "启用背包预览")
                if status["inhibit"] or status["state"] in ("FAULT", "CLOSED"):
                    if self.controller.state != "OFF":
                        self.stop(status.get("error") or status.get("reason") or "背包已暂停")
            else:
                self.mode_label.configure(text="背包连接中" if status["state"] == "CONNECTING" else "背包已断开")
                if self.controller.state != "OFF":
                    self.stop(status.get("error") or "背包已断开，请重新连接并手动启用")
            if self.network_message is not None:
                self.network_message.set(status.get("error") or
                    (status.get("reason") if status.get("via")=="mlk-relay" and not status.get("relay_armed") else None)
                    or self.mode_label.cget("text"))
        self.root.after(50, self.poll_network)

    def close(self):
        self.disconnect_robot()
        super().close()
