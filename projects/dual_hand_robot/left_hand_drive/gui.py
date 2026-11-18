"""Single-window PC preview, calibration wizard and virtual chassis."""
from dataclasses import asdict
import json
import math
import sys
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

from .capture import CONNECTIONS, Failure, Sample, Worker, demo_observation
from .core import Calibration, CalibrationError, Controller, PoseReference, reference
from .runlog import RunLog

BG, PANEL, TEXT = "#101b2b", "#18283d", "#e7f0fc"
UI_FONT = "Microsoft YaHei UI" if sys.platform == "win32" else "Noto Sans CJK SC"


class App:
    def __init__(self, root, project, demo=False, camera=0, upper=False):
        self.root, self.project = root, Path(project)
        self.upper_preview = None
        self.controller = Controller()
        self.worker = self.log = None
        self.demo = False
        self.demo_started = None
        self.demo_id = 0
        self.image = self.photo = None
        self.hands = ()
        self.pending = None
        self.references = {}
        self.last_frame_at = None
        self.last_loop = time.monotonic()
        self.x = self.y = self.heading = 0.0
        self.status = tk.StringVar(value="OFF · 每次开始前，请校准静止位")
        self.detail = tk.StringVar(value=self.controller.reason)
        self.readout = tk.StringVar(value="前后  +0.00\n旋转  +0.00\n相对倾角  0.0°")
        self.cal_status = tk.StringVar(value="① 自然半握静止位 → ② 五指握拳 → ③ 五指展开")
        self.camera = tk.IntVar(value=camera)
        self.swap = tk.BooleanVar(value=False)
        self.move_gain, self.turn_gain = tk.DoubleVar(value=35), tk.DoubleVar(value=35)
        root.title("双手控制 · 电脑预览 v0.2" if upper else "左手底盘控制 · 电脑端 v1.0")
        root.geometry("%dx%d" % (min(1280, root.winfo_screenwidth()-40), min(860, root.winfo_screenheight()-80))
                      if upper else "1160x790")
        root.minsize(980, 650) if upper else root.minsize(1050, 740)
        root.configure(bg=BG)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT, font=(UI_FONT, 11))
        style.configure("TButton", font=(UI_FONT, 10), padding=(10, 8))
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        main = ttk.Frame(root, padding=16)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="左手控底盘，右手控上肢" if upper else "左手，掌控底盘",
                  font=(UI_FONT, 20 if upper else 23, "bold")).pack(anchor="w")
        self.subtitle = ttk.Label(main, text="电脑软件预览 · 拇指联动双大臂 · 四指对称分组 · 空格全部暂停" if upper else
                                  "握拳前进  /  展开后退  /  静止手势停车  /  像方向盘一样左右倾斜转向")
        self.subtitle.pack(anchor="w", pady=(4, 10))
        toolbar = ttk.Frame(main)
        self.toolbar = toolbar
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="打开摄像头", command=self.start_camera).pack(side="left")
        ttk.Button(toolbar, text="合成演示", command=self.start_demo).pack(side="left", padx=8)
        ttk.Button(toolbar, text="关闭输入", command=self.close_input).pack(side="left")
        ttk.Label(toolbar, text="  相机").pack(side="left")
        ttk.Spinbox(toolbar, from_=0, to=5, width=3, textvariable=self.camera, command=self.input_settings_changed).pack(side="left")
        ttk.Checkbutton(toolbar, text="交换左右手标签", variable=self.swap, command=self.input_settings_changed).pack(side="left", padx=12)
        self.mode_label = ttk.Label(toolbar, text="仅电脑预览 · 无硬件输出" if upper else "电脑预览 · 尚未连接机器人", foreground="#68d7c0")
        self.mode_label.pack(side="right")
        body = ttk.Frame(main)
        body.pack(fill="both", expand=True, pady=14)
        left = ttk.Frame(body)
        if upper:
            body.columnconfigure(0, weight=1)
            body.columnconfigure(1, minsize=460)
            body.rowconfigure(0, weight=1)
            left.grid(row=0, column=0, sticky="nsew")
        else:
            left.pack(side="left", fill="both", expand=True)
        self.video = tk.Canvas(left, width=640, height=340, bg="#0b1421", highlightthickness=0)
        self.video.pack(fill="both", expand=True)
        self.video.create_text(320, 240, text="点击“打开摄像头”\n完整露出左手，掌面朝向镜头", fill=TEXT,
                               font=(UI_FONT, 16), justify="center")
        ttk.Label(left, text="镜像显示 · 绿：左手底盘 / 蓝：右手上肢" if upper else
                  "镜像画面：画面中的手掌向左倾 → 机器人左转；右手不参与控制", font=(UI_FONT, 10)).pack(anchor="w", pady=6)
        right = ttk.Frame(body, padding=(18, 0, 0, 0))
        right.grid(row=0, column=1, sticky="nsew") if upper else right.pack(side="right", fill="y")
        if upper:
            notebook = ttk.Notebook(right, width=420)
            notebook.pack(fill="both", expand=True)
            upper_outer, chassis_page = ttk.Frame(notebook), ttk.Frame(notebook, padding=6)
            notebook.add(upper_outer, text="右手上肢目标")
            notebook.add(chassis_page, text="左手底盘方向")
            self.upper_viewport = tk.Canvas(upper_outer, width=420, bg=BG, highlightthickness=0)
            scrollbar = ttk.Scrollbar(upper_outer, orient="vertical", command=self.upper_viewport.yview)
            scrollbar.pack(side="right", fill="y")
            self.upper_viewport.pack(side="left", fill="both", expand=True)
            self.upper_viewport.configure(yscrollcommand=scrollbar.set)
            upper_page = ttk.Frame(self.upper_viewport, padding=6)
            upper_window = self.upper_viewport.create_window(0, 0, window=upper_page, anchor="nw")
            upper_page.bind("<Configure>", lambda event: self.upper_viewport.configure(scrollregion=self.upper_viewport.bbox("all")))
            self.upper_viewport.bind("<Configure>", lambda event: self.upper_viewport.itemconfigure(upper_window, width=event.width))
            right = chassis_page
        self.map = tk.Canvas(right, width=320, height=190, bg=PANEL, highlightthickness=0)
        self.map.pack()
        ttk.Label(right, textvariable=self.readout, font=("Consolas", 13), justify="left").pack(anchor="w", pady=6)
        ttk.Label(right, text="前后强度（归一化百分比）").pack(anchor="w")
        ttk.Scale(right, from_=10, to=100, variable=self.move_gain, command=self.gains_changed).pack(fill="x")
        ttk.Label(right, text="旋转强度（归一化百分比）").pack(anchor="w", pady=(8,0))
        ttk.Scale(right, from_=10, to=100, variable=self.turn_gain, command=self.gains_changed).pack(fill="x")
        ttk.Button(right, text="重置虚拟底盘位置", command=self.reset_virtual).pack(fill="x", pady=6)
        calibration = ttk.Frame(main)
        self.calibration_row = calibration
        calibration.pack(fill="x")
        if upper:
            ttk.Label(calibration, text="左手底盘", width=9).pack(side="left")
        self.cal_buttons = []
        for stage, title in (("neutral", "① 校准静止位"), ("fist", "② 采集握拳"), ("opened", "③ 采集展开")):
            button = ttk.Button(calibration, text=title, command=lambda s=stage: self.begin_calibration(s))
            button.pack(side="left", padx=(0,8))
            self.cal_buttons.append(button)
        self.enable_button = ttk.Button(calibration, text="启用所选模式" if upper else "启用控制预览", command=self.enable)
        self.enable_button.pack(side="right")
        ttk.Button(calibration, text="暂停 / 空格", command=lambda: self.stop("手动暂停")).pack(side="right", padx=8)
        ttk.Label(main, textvariable=self.cal_status, foreground="#f5c66d", wraplength=1080).pack(anchor="w", pady=(10,4))
        ttk.Label(main, textvariable=self.status, font=(UI_FONT, 12 if upper else 15, "bold")).pack(anchor="w")
        ttk.Label(main, textvariable=self.detail, wraplength=1080).pack(anchor="w", pady=4)
        root.bind("<space>", lambda event: self.stop("空格暂停"))
        root.bind("<Escape>", lambda event: self.stop("Esc暂停"))
        root.bind("<FocusOut>", lambda event: root.after_idle(self.check_focus))
        root.protocol("WM_DELETE_WINDOW", self.close)
        if upper:
            from .upper_ui import UpperPreview
            self.upper_preview = UpperPreview(self, main, upper_page)
            # Allocate footer controls first, so shrinking the window reduces the
            # camera/scrolling panel rather than hiding calibration and STOP.
            packed = main.pack_slaves()
            footers = packed[packed.index(body)+1:]
            body.pack_forget()
            for widget in reversed(footers):
                options = widget.pack_info()
                widget.pack_forget()
                options.pop("in", None)
                options.update(side="bottom", fill="x")
                widget.pack(**options)
            body.pack(fill="both", expand=True, pady=10)
            def scroll(event):
                delta = -1 if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0 else 1
                self.upper_viewport.yview_scroll(delta, "units")
            def bind_scroll(widget):
                for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    widget.bind(event, scroll, add="+")
                for child in widget.winfo_children():
                    bind_scroll(child)
            bind_scroll(upper_page)
        if demo:
            self.start_demo()
        root.after(50, self.loop)

    def gains_changed(self, unused=None):
        self.controller.forward_gain = self.move_gain.get() / 100
        self.controller.turn_gain = self.turn_gain.get() / 100

    def input_settings_changed(self):
        self.close_input()
        self.cal_status.set("输入设置已改变；重新打开摄像头并校准")

    def stop(self, reason):
        self.controller.stop(reason)
        if self.upper_preview:
            self.upper_preview.stop(reason)
        self.pending = None
        try:
            self.write_snapshot(self.controller.command(time.monotonic()))
            if self.upper_preview and self.log:
                self.log.record_upper(self.upper_preview.controller.command(time.monotonic()))
        except OSError as error:
            self.controller.reason = "输出文件不可写，已暂停：" + str(error)
            if self.upper_preview:
                self.upper_preview.controller.reason = self.controller.reason

    def control_window_focused(self):
        try:
            focus = self.root.focus_get()
            return focus is not None and focus.winfo_toplevel() is self.root
        except (KeyError, tk.TclError):
            # Native ttk popdowns can own focus without a Python widget object.
            # Treat that transition as unfocused; keep the polling loop alive.
            return False

    def check_focus(self):
        if not self.control_window_focused():
            self.stop("窗口失去焦点，已暂停")

    def close_input(self):
        self.stop("输入已关闭")
        self.demo = False
        if self.worker:
            self.worker.stop()
            self.worker.join(.5)
            if not self.worker.is_alive():
                self.worker = None
        self.controller.invalidate()
        if self.upper_preview:
            self.upper_preview.close_input()
        self.pending = None
        self.references.clear()
        self.image, self.hands = None, ()
        if self.log:
            self.log.record(self.controller.command(time.monotonic()))
            self.log.close("输入关闭/切换")
            self.log = None
        self.cal_status.set("请重新采集静止位、握拳和展开；上次校准不自动生效")

    def start_camera(self):
        self.close_input()
        if self.worker and self.worker.is_alive():
            self.detail.set("正在释放旧摄像头，请稍后再打开")
            return
        try:
            camera = int(self.camera.get())
            if not 0 <= camera <= 20:
                raise ValueError()
        except (ValueError, tk.TclError):
            self.detail.set("请输入0～20的相机编号")
            return
        self.log = RunLog(self.project, "camera")
        if self.upper_preview:
            self.upper_preview.start_log()
        self.worker = Worker(camera, self.swap.get())
        self.worker.start()
        self.controller.reason = "摄像头启动中；随后依次采集三个参照"

    def start_demo(self):
        self.close_input()
        self.demo, self.demo_started, self.demo_id = True, time.monotonic(), 0
        self.log = RunLog(self.project, "demo")
        refs = [PoseReference(demo_observation(1,0,g).fingers, 0, "demo") for g in ("NEUTRAL","FIST","OPEN")]
        self.controller.set_calibration(Calibration(*refs))
        self.log.calibration(self.controller.calibration)
        self.controller.enable()
        if self.upper_preview:
            self.upper_preview.start_demo()
        self.cal_status.set("合成演示：自动提供虚拟参照；不代表真人校准或识别效果")
        self.root.after(100, self.root.focus_force)

    def begin_calibration(self, stage):
        if self.demo:
            self.cal_status.set("合成演示不采集真人标定；请打开摄像头")
            return
        if not self.worker or not self.worker.is_alive():
            self.cal_status.set("请先打开摄像头")
            return
        if stage != "neutral" and "neutral" not in self.references:
            self.cal_status.set("请先采集①静止位")
            return
        if stage == "opened" and "fist" not in self.references:
            self.cal_status.set("请先采集②握拳")
            return
        self.stop("正在校准，输出为零")
        self.controller.calibration = None
        if stage == "neutral":
            self.references.clear()
        elif stage == "fist":
            self.references.pop("opened", None)
        self.pending = {"stage": stage, "start": time.monotonic() + 3, "samples": []}
        if self.log:
            self.log.event("calibration_begin", stage=stage)

    def capture_reference(self, observation, now):
        p = self.pending
        if not p:
            return
        label = {"neutral":"自然半握静止位（不要握拳或完全展开）", "fist":"五指握拳，拇指也收紧", "opened":"五指完全伸展，掌面朝向镜头"}[p["stage"]]
        if now < p["start"]:
            self.cal_status.set("%s · %d秒后开始采集，随后保持1秒" % (label, math.ceil(p["start"]-now)))
            return
        if not observation.valid or not 0 <= now - observation.sampled_at <= .30:
            p["samples"].clear()
            self.cal_status.set("等待稳定左手：" + observation.reason)
        else:
            p["samples"].append(observation)
            self.cal_status.set("正在采集：" + label)
            if observation.sampled_at - p["samples"][0].sampled_at >= 1:
                try:
                    self.references[p["stage"]] = reference(p["samples"])
                    self.pending = None
                    if p["stage"] == "opened":
                        cal = Calibration(*(self.references[k] for k in ("neutral","fist","opened")))
                        self.controller.set_calibration(cal)
                        self.log.calibration(cal)
                        self.cal_status.set("三步校准完成。点击启用即可从当前手势开始；握拳前进、展开后退" if getattr(self,"start_anywhere",False)
                                            else "三步校准完成。点击启用，再回到静止位；握拳前进、展开后退")
                    else:
                        self.cal_status.set("参照已采集，请继续下一步")
                except CalibrationError as error:
                    self.cal_status.set(str(error))
                    self.pending = None
                    self.controller.stop("校准未完成")
                    self.log.event("calibration_rejected", reason=str(error))
                return
        if now - p["start"] > 15:
            self.pending = None
            self.cal_status.set("采集超时，请重新点击当前步骤")

    def enable(self):
        if self.upper_preview:
            self.upper_preview.enable()
            return
        if self.pending:
            self.cal_status.set("请先完成当前采集")
            return
        self.controller.enable()
        self.root.focus_set()

    def reset_virtual(self):
        self.x = self.y = self.heading = 0.0

    def write_snapshot(self, command):
        folder = self.project / "runtime"
        folder.mkdir(exist_ok=True)
        path = folder / "latest_command.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(command.as_dict(), ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
        if self.upper_preview:
            self.upper_preview.write_snapshot(command.produced_at)

    def draw_video(self):
        from PIL import Image, ImageDraw, ImageTk
        if self.demo:
            image = Image.new("RGB", (640,480), "#0b1421")
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((165,90,475,390), radius=25, outline="#68d7c0", width=4)
            draw.text((225,205), "SYNTHETIC DEMO", fill="#68d7c0")
            draw.text((225,235), self.controller.gesture, fill="white")
            draw.text((225,265), "roll: %+.1f deg" % self.controller.delta, fill="white")
            if self.upper_preview:
                draw.text((195,305), "RIGHT: FIVE FINGERS / SIX SERVOS", fill="#82b6ff")
                draw.text((195,325), "THUMB: BOTH UPPER ARMS", fill="#82b6ff")
        elif self.image is not None:
            image = Image.fromarray(self.image).copy()
            draw = ImageDraw.Draw(image)
            w,h = image.size
            for side, score, points in self.hands:
                color = "#68d7c0" if side == "Left" else "#82b6ff" if self.upper_preview else "#64748b"
                for a,b in CONNECTIONS:
                    draw.line((points[a][0]*w, points[a][1]*h, points[b][0]*w, points[b][1]*h), fill=color, width=3)
                for x,y,z in points:
                    draw.ellipse((x*w-3,y*h-3,x*w+3,y*h+3),fill=color)
                draw.text((points[0][0]*w,points[0][1]*h+10), "%s %.2f" % (side,score), fill=color)
        else:
            self.video.delete("all")
            self.video.create_text(max(320,self.video.winfo_width()/2),240,text="等待新鲜摄像头画面",fill=TEXT,font=(UI_FONT,16))
            return
        width,height = max(1,self.video.winfo_width()),max(1,self.video.winfo_height())
        image.thumbnail((width,height))
        self.photo = ImageTk.PhotoImage(image)
        self.video.delete("all")
        self.video.create_image(width/2,height/2,image=self.photo)

    def draw_chassis(self, command, dt):
        self.heading += command.yaw_left * dt * 1.6
        self.x -= math.sin(self.heading) * command.forward * dt * 60
        self.y -= math.cos(self.heading) * command.forward * dt * 60
        self.x, self.y = max(-110,min(110,self.x)),max(-70,min(70,self.y))
        c=self.map
        c.delete("all")
        for x in range(0,321,32): c.create_line(x,0,x,260,fill="#243952")
        for y in range(0,261,32): c.create_line(0,y,320,y,fill="#243952")
        cx,cy=160+self.x,105+self.y * .60
        points=[]
        for x,y in ((0,-32),(-21,23),(0,13),(21,23)):
            points.extend((cx+x*math.cos(self.heading)+y*math.sin(self.heading),cy-x*math.sin(self.heading)+y*math.cos(self.heading)))
        c.create_polygon(points,fill="#68d7c0" if command.enabled else "#7285a0",outline="white",width=2)
        c.create_text(160,20,text="虚拟底盘 · 俯视",fill=TEXT,font=(UI_FONT,12))
        c.create_text(160,174,text="轨迹仅用于方向预览，不代表实际里程",fill="#afc0d7",font=(UI_FONT,9))

    def loop(self):
        now=time.monotonic()
        dt=min(.1,max(0,now-self.last_loop))
        self.last_loop=now
        observation=None
        if self.demo:
            self.demo_id+=1
            phase=(now-self.demo_started)%14
            gesture="NEUTRAL" if phase<2 or phase>=12 else "FIST" if phase<7 else "OPEN"
            roll=0 if phase<2 or phase>=12 else 30*math.sin((phase-2)*.9)
            observation=demo_observation(self.demo_id,now,gesture,roll)
        elif self.worker:
            item=self.worker.latest()
            if isinstance(item, Failure):
                self.stop(item.message)
                self.controller.last = None
                self.detail.set(item.message)
                self.image,self.hands=None,()
                if self.upper_preview:
                    self.upper_preview.controller.last = None
                if self.log: self.log.event("camera_error",reason=item.message)
            elif isinstance(item, Sample):
                observation=item.observation
                self.image,self.hands=item.image,item.hands
                self.last_frame_at=observation.sampled_at
        if observation:
            self.controller.update(observation,now)
            self.capture_reference(observation,now)
            if self.upper_preview:
                self.upper_preview.observe(observation,now)
        if not self.demo and self.last_frame_at is not None and now-self.last_frame_at>.30:
            self.image=None
        focused = self.control_window_focused()
        command=self.controller.tick(now,focused)
        if self.upper_preview:
            self.upper_preview.tick(now,focused)
            command=self.controller.command(now)
        try:
            self.write_snapshot(command)
            if self.log: self.log.record(command,self.controller.last)
            if self.log and self.upper_preview:
                self.log.record_upper(self.upper_preview.controller.command(now))
        except OSError as error:
            self.controller.stop("日志/输出文件写入失败："+str(error))
            if self.upper_preview:
                self.upper_preview.stop(self.controller.reason)
            command=self.controller.command(now)
        label={"FIST":"握拳 / 前进", "OPEN":"展开 / 后退", "NEUTRAL":"静止 / 过渡手势"}[command.gesture]
        self.status.set("%s%s · %s%s" % ("底盘 " if self.upper_preview else "",self.controller.state,label," · 合成演示" if self.demo else ""))
        self.detail.set(command.reason)
        if self.upper_preview:
            panel = self.upper_preview
            self.status.set("上肢 %s · 底盘 %s%s" % (panel.controller.state, self.controller.state,
                                                    " · 合成演示" if self.demo else " · "+getattr(self,"execution_label","电脑预览")))
            left, right = panel.required
            self.detail.set("左手：%s；右手：%s" % (command.reason, panel.controller.reason)
                            if left and right else panel.controller.reason if right else command.reason)
        self.readout.set("前后  %+.2f\n旋转  %+.2f\n相对倾角  %+.1f°" % (command.forward,command.yaw_left,command.roll_delta_deg))
        self.draw_video()
        self.draw_chassis(command,dt)
        self.root.after(50,self.loop)

    def close(self):
        self.close_input()
        self.root.destroy()
