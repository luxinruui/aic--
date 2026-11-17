"""Independent PC robot UI: existing hand detection + new combined TCP8770."""
import time
import tkinter as tk
from tkinter import ttk
import uuid

from dual_link.client import Client
from dual_link.profile import AXES, load
from dual_link.protocol import PORT
from .gui import App

MODE_NAMES = {"仅上肢":"arms", "仅底盘":"chassis", "双手联合":"both"}


class RobotApp(App):
    def __init__(self, root, project, demo=False, camera=0):
        self.client = None
        self.input_session = uuid.uuid4().hex
        self.profile = load(project)
        self.connection_window = None
        self.network_state = None
        super().__init__(root,project,demo,camera,upper=True)
        self.turn_gain.set(100)
        self.gains_changed()
        root.title("双手机器人联控 · 独立项目 v1.0")
        self.subtitle.configure(text="独立背包联控 · 拇指联动双大臂 · 四指对称分组 · 空格全部暂停")
        self.mode_label.configure(text="未连接双手背包服务 · 8770")
        ttk.Button(self.toolbar,text="连接背包…",command=self.connection_dialog).pack(side="right",padx=4)
        root.after(50,self.poll_network)

    def write_snapshot(self, command):
        super().write_snapshot(command)
        if not self.client:
            return
        upper = self.upper_preview.controller.command(command.produced_at)
        mode = MODE_NAMES[self.upper_preview.mode.get()]
        branches = ([command] if mode == "chassis" else [upper] if mode == "arms" else [command,upper])
        valid = all(x.input_valid for x in branches)
        enabled = valid and all(x.enabled for x in branches)
        source = branches[0].source
        valid = valid and all(x.source == source and x.frame_id == branches[0].frame_id for x in branches)
        self.client.publish(dict(session=self.input_session, produced_at=command.produced_at,
            enabled=enabled and valid, input_valid=valid, sample_age_ms=max(x.sample_age_ms for x in branches),
            frame_id=branches[0].frame_id, source=source, mode=mode,
            forward=command.forward if enabled and mode != "arms" else 0.,
            yaw_left=command.yaw_left if enabled and mode != "arms" else 0.,
            targets=upper.targets if enabled and mode != "chassis" else dict.fromkeys(AXES),
            groups=list(upper.active_groups) if mode != "chassis" else [False]*5))

    def close_input(self):
        super().close_input()
        self.input_session = uuid.uuid4().hex

    def start_demo(self):
        if self.client and self.client.status().get("hardware"):
            self.stop("已连接实机，不能运行合成演示；请断开后再演示")
            return
        super().start_demo()

    def enable(self):
        if self.client:
            status = self.client.status()
            if not status["connected"]:
                self.stop("请先连接双手背包服务，或断开后使用本地预览")
                return
            if status["hardware"] and self.demo:
                self.stop("实机拒绝合成演示，请使用摄像头")
                return
            if self.upper_preview.required[1]:
                if not status["baseline_confirmed"]:
                    self.stop("背包尚未确认原工程的机器人基准")
                    return
                try:
                    self.upper_preview.controller.set_reference(status["arm_reference"])
                except ValueError as error:
                    self.stop(str(error))
                    return
        else:
            self.upper_preview.controller.set_reference(None)
        super().enable()

    def connect_robot(self, host, port=PORT):
        self.disconnect_robot()
        self.profile = load(self.project)
        self.client = Client(host,self.profile,port)
        self.write_snapshot(self.controller.command(time.monotonic()))
        self.client.start()
        if self.log: self.log.event("dual_robot_connect",port=port)

    def disconnect_robot(self):
        self.stop("双手连接已关闭，全部暂停")
        if self.client:
            self.client.close()
            self.client = None
        if self.upper_preview:
            self.upper_preview.controller.set_reference(None)

    def connection_dialog(self):
        self.stop("正在设置背包连接")
        if self.connection_window and self.connection_window.winfo_exists():
            self.connection_window.lift()
            return
        window = self.connection_window = tk.Toplevel(self.root)
        window.title("连接独立双手背包服务")
        box = ttk.Frame(window,padding=16)
        box.pack(fill="both",expand=True)
        ttk.Label(box,text="新项目使用TCP8770；旧底盘8768保持独立。").grid(row=0,column=0,columnspan=3,pady=8)
        host = tk.StringVar(value=self.client.host if self.client else "")
        port = tk.StringVar(value=str(self.client.port if self.client else PORT))
        ttk.Label(box,text="背包IPv4").grid(row=1,column=0)
        ttk.Entry(box,textvariable=host,width=20).grid(row=1,column=1,padx=6)
        ttk.Entry(box,textvariable=port,width=6).grid(row=1,column=2)
        message = tk.StringVar(value="默认接收端只预览；确认基准后才可启用实机上肢。")
        ttk.Label(box,textvariable=message,wraplength=460).grid(row=2,column=0,columnspan=3,pady=12)
        def connect():
            try:
                self.connect_robot(host.get(),int(port.get()))
                message.set("正在连接；关闭此设置窗口后，再手动启用")
            except (ValueError,OSError) as error:
                message.set(str(error))
        ttk.Button(box,text="连接",command=connect).grid(row=3,column=0)
        ttk.Button(box,text="断开并暂停",command=self.disconnect_robot).grid(row=3,column=1)
        ttk.Button(box,text="返回",command=window.destroy).grid(row=3,column=2)

    def poll_network(self):
        if self.client:
            status = self.client.status()
            if status["connected"]:
                self.mode_label.configure(text=("实机" if status["hardware"] else "背包预览")+" · "+status["state"])
                if status["inhibit"] or status["state"] in ("FAULT","CLOSED"):
                    if self.controller.state != "OFF" or self.upper_preview.controller.state != "OFF":
                        self.stop(status.get("error") or status.get("reason") or "背包暂停")
            else:
                self.mode_label.configure(text="连接中…" if status["state"] == "CONNECTING" else "双手背包已断开")
                if self.controller.state != "OFF" or self.upper_preview.controller.state != "OFF":
                    self.stop(status.get("error") or "连接断开，请手动重新连接")
            if self.log:
                hardware = bool(status["connected"] and status["hardware"])
                self.log.info["hardware_connected"] |= hardware
                if hardware: self.log.info["upper_preview_only"] = False
                key = (status["connected"],status["hardware"],status["state"],status.get("error",""))
                if key != self.network_state:
                    self.log.event("dual_robot_state",connected=status["connected"],hardware=status["hardware"],
                                   state=status["state"],reason=status.get("reason",""),error=status.get("error",""))
                    self.network_state = key
        self.root.after(50,self.poll_network)

    def close(self):
        self.disconnect_robot()
        super().close()
