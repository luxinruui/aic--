"""Optional PC-only upper-body panel sharing the chassis camera and event loop."""
from dataclasses import asdict, replace
import json
import math
import time
import tkinter as tk
from tkinter import ttk

from .gui import UI_FONT
from .core import CalibrationError, PoseReference, extract_hand, reference
from .upper import (AXES, AXIS_NAMES, FINGER_NAMES, GROUP_NAMES, SERVO_IDS,
                    ArmCalibration, ArmController, Settings, demo_calibration, demo_right)

MODES = {"仅上肢": (False, True), "仅底盘": (True, False), "双手联合": (True, True)}


class UpperPreview:
    def __init__(self, app, main, page):
        self.app = app
        self.controller = ArmController()
        self.pending = None
        self.references = {}
        self.settings_window = None
        self.mode = tk.StringVar(value="仅上肢")
        self.selected_finger = tk.StringVar(value="全部手指")
        self.cal_status = tk.StringVar(value="右手：①自然半握起始位 → ②握拳 → ③展开")
        self.status = tk.StringVar(value="上肢 OFF · 仅软件目标，不连接机器人")
        ttk.Label(app.toolbar, text="预览模式").pack(side="left", padx=(6, 2))
        combo = ttk.Combobox(app.toolbar, textvariable=self.mode, values=tuple(MODES), state="readonly", width=9)
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda event: app.stop("模式已改变，请重新启用"))
        top = ttk.Frame(page)
        top.pack(fill="x", pady=(0, 4))
        ttk.Label(top, text="右手：左肘、左肩｜右肩、右肘", font=(UI_FONT, 10)).pack(side="left")
        ttk.Button(top, text="范围…", command=self.open_settings).pack(side="right")
        self.pose = tk.Canvas(page, width=400, height=82, bg="#18283d", highlightthickness=0)
        self.pose.pack(fill="x")
        self.bars, self.percent = [], []
        for name, group in zip(FINGER_NAMES, GROUP_NAMES):
            row = ttk.Frame(page)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=name, width=6, font=(UI_FONT, 9)).pack(side="left")
            bar = ttk.Progressbar(row, maximum=100, length=90)
            bar.pack(side="left", fill="x", expand=True)
            percent = ttk.Label(row, text="—", width=5, font=("Consolas", 9))
            percent.pack(side="left", padx=4)
            ttk.Label(row, text=group, width=18, font=(UI_FONT, 9)).pack(side="right")
            self.bars.append(bar)
            self.percent.append(percent)
        self.table = ttk.Treeview(page, columns=("id", "joint", "preview", "target"), show="headings", height=6)
        for key, title, width in (("id", "ID", 35), ("joint", "关节", 115),
                                  ("preview", "待启用 / °", 95), ("target", "有效目标 / °", 100)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, anchor="center", stretch=key == "joint")
        for key, name, servo in zip(AXES, AXIS_NAMES, SERVO_IDS):
            self.table.insert("", "end", iid=key, values=(servo, name, "—", "—"))
        self.table.pack(fill="both", expand=True, pady=4)
        ttk.Label(page, text="待启用列用于姿态匹配；有效目标仅在ON时显示。", font=(UI_FONT, 9)).pack(anchor="w")
        calibration = ttk.Frame(main)
        calibration.pack(fill="x", before=app.calibration_row, pady=(0, 4))
        ttk.Label(calibration, text="右手上肢", width=9).pack(side="left")
        self.cal_buttons = []
        for stage, title in (("neutral", "① 起始位"), ("fist", "② 握拳"), ("opened", "③ 展开")):
            button = ttk.Button(calibration, text=title, command=lambda s=stage: self.begin_calibration(s))
            button.pack(side="left", padx=(0, 5))
            self.cal_buttons.append(button)
        ttk.Label(calibration, text="补采").pack(side="left", padx=(8, 3))
        ttk.Combobox(calibration, textvariable=self.selected_finger, values=("全部手指", *FINGER_NAMES),
                     width=9, state="readonly").pack(side="left")
        ttk.Label(main, textvariable=self.cal_status, foreground="#82b6ff", wraplength=1150,
                  font=(UI_FONT, 10)).pack(anchor="w", before=app.calibration_row, pady=(0, 4))

    @property
    def required(self):
        return MODES[self.mode.get()]

    def stop(self, reason):
        self.controller.stop(reason)
        self.pending = None

    def close_input(self):
        self.controller.invalidate()
        self.references.clear()
        self.pending = None
        self.cal_status.set("右手需重新采集起始位、握拳、展开；不自动加载历史校准")

    def start_log(self):
        if self.app.log:
            self.app.log.enable_upper(asdict(self.controller.settings))

    def start_demo(self):
        self.start_log()
        cal = demo_calibration()
        self.controller.set_calibration(cal)
        if self.required[1]:
            self.controller.enable()
        if not self.required[0]:
            self.app.controller.stop("当前模式仅预览上肢")
        self.app.log.upper_calibration(cal)
        self.cal_status.set("右手合成演示：逐指变化，拇指联动双大臂；不代表真人识别")

    def begin_calibration(self, stage):
        app = self.app
        if app.demo or not app.worker or not app.worker.is_alive():
            self.cal_status.set("请先打开真实摄像头；演示不能用于真人标定")
            return
        app.stop("正在校准右手，全部预览已暂停")
        self.controller.calibration = None
        finger = self.selected_finger.get()
        index = None if finger == "全部手指" else FINGER_NAMES.index(finger)
        if stage == "neutral" and index is None:
            self.references.clear()
        self.pending = dict(stage=stage, index=index, start=time.monotonic()+3, samples=[])
        app.log.event("upper_calibration_begin", stage=stage, finger=finger)

    def capture_reference(self, observation, now):
        p = self.pending
        if not p:
            return
        title = {"neutral": "右手自然半握起始位", "fist": "右手握拳，拇指收拢", "opened": "右手五指展开"}[p["stage"]]
        if now < p["start"]:
            self.cal_status.set("%s · %d秒后采集，随后保持1秒" % (title, math.ceil(p["start"]-now)))
            return
        if not observation.valid or not 0 <= now-observation.sampled_at <= .30:
            p["samples"].clear()
            self.cal_status.set("等待稳定右手："+observation.reason)
        else:
            p["samples"].append(observation)
            self.cal_status.set("正在采集："+title)
            if observation.sampled_at-p["samples"][0].sampled_at >= 1:
                try:
                    # Holding still during capture is required; wrist is never an input axis.
                    pose = reference(p["samples"])
                    if p["index"] is not None:
                        previous = self.references.get(p["stage"])
                        if previous is None:
                            raise CalibrationError("请先选择全部手指采集该步骤，再补采单指")
                        fingers = list(previous.fingers)
                        fingers[p["index"]] = pose.fingers[p["index"]]
                        pose = replace(pose, fingers=tuple(fingers))
                    self.references[p["stage"]] = pose
                    self.pending = None
                    if all(k in self.references for k in ("neutral", "fist", "opened")):
                        cal = ArmCalibration(*(self.references[k] for k in ("neutral", "fist", "opened")))
                        self.controller.set_calibration(cal)
                        self.app.log.upper_calibration(cal)
                        self.cal_status.set("右手校准完成；点击启用，回到①起始位保持0.5秒")
                    else:
                        self.cal_status.set("右手参照已采集，请继续其余步骤")
                except CalibrationError as error:
                    self.pending = None
                    self.cal_status.set(str(error).replace("左手", "右手"))
                    self.app.log.event("upper_calibration_rejected", reason=str(error))
                return
        if now-p["start"] > 15:
            self.pending = None
            self.cal_status.set("右手采集超时，请重新点击当前步骤")

    def observe(self, left, now):
        if self.app.demo:
            observation = demo_right(left.frame_id, left.sampled_at, now-self.app.demo_started)
        else:
            image = self.app.image
            h, w = image.shape[:2] if image is not None else (480, 640)
            observation = extract_hand(self.app.hands, w, h, left.frame_id, left.sampled_at, left.source, "Right")
        self.controller.update(observation, now)
        self.capture_reference(observation, now)

    def enable(self):
        app = self.app
        if self.pending or app.pending:
            self.cal_status.set("请先完成当前采集")
            return
        left, right = self.required
        if (left and app.controller.calibration is None) or (right and self.controller.calibration is None):
            app.stop("所选模式尚未完成校准")
            self.cal_status.set("请先完成所选模式需要的左手 / 右手校准")
            return
        if left:
            app.controller.enable()
        else:
            app.controller.stop("当前模式仅预览上肢")
        if right:
            self.controller.enable()
        else:
            self.controller.stop("当前模式仅预览底盘")
        app.root.focus_set()

    def tick(self, now, focused):
        self.controller.tick(now, focused)
        if self.required == (True, True):
            left, right = self.app.controller, self.controller
            if (left.state != "OFF" or right.state != "OFF") and (left.state == "OFF" or right.state == "OFF"):
                self.app.stop("双手联合暂停："+(left.reason if left.state == "OFF" else right.reason))
        command = self.controller.command(now)
        for i, (bar, label) in enumerate(zip(self.bars, self.percent)):
            valid = command.input_valid and command.active_groups[i]
            bar["value"] = command.fingers[i]*100 if valid else 0
            label.configure(text="%d%%" % round(command.fingers[i]*100) if valid else "—")
        for key, name, servo in zip(AXES, AXIS_NAMES, SERVO_IDS):
            preview, target = command.preview_targets[key], command.targets[key]
            self.table.item(key, values=(servo, name, "—" if preview is None else "%.1f" % preview,
                                         "—" if target is None else "%.1f" % target))
        self.status.set("上肢 %s · %s" % (self.controller.state, command.reason))
        self.draw_pose(command)

    def draw_pose(self, command):
        c = self.pose
        c.delete("all")
        c.create_text(200, 12, text="双臂示意 · 软件目标", fill="#e7f0fc", font=(UI_FONT, 10))
        c.create_oval(188, 23, 212, 45, outline="#afc0d7", width=2)
        c.create_line(200, 45, 200, 95, fill="#afc0d7", width=3)
        for side, sign, x in (("left", 1, 222), ("right", -1, 178)):
            lift, elbow = command.targets[side+"_lift"], command.targets[side+"_elbow"]
            color = "#82b6ff" if lift is not None or elbow is not None else "#7285a0"
            a = math.radians(lift if lift is not None else 0)
            b = math.radians(elbow if elbow is not None else 0)
            ex, ey = x+sign*math.sin(a)*35, 48+math.cos(a)*35
            hx, hy = ex+sign*math.sin(a+b)*28, ey+math.cos(a+b)*28
            c.create_line(200, 48, x, 48, ex, ey, hx, hy, fill=color, width=4)
        c.create_text(70, 47, text="机器人右臂", fill="#afc0d7", font=(UI_FONT, 9))
        c.create_text(330, 47, text="机器人左臂", fill="#afc0d7", font=(UI_FONT, 9))
        c.create_text(200, 92, text="合拢 / 展开见下表双侧目标", fill="#afc0d7", font=(UI_FONT, 8))
        c.scale("all", 0, 0, 1, .82)

    def write_snapshot(self, now):
        command = self.controller.command(now)
        path = self.app.project / "runtime" / "latest_upper_preview.json"
        temporary = path.with_suffix(".tmp")
        data = command.as_dict()
        client = getattr(self.app, "client", None)
        status = client.status() if client else {}
        data["hardware_connected"] = bool(status.get("connected") and status.get("hardware"))
        data["preview_only"] = not data["hardware_connected"]
        temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temporary.replace(path)

    def open_settings(self):
        app = self.app
        app.stop("正在调整上肢预览范围")
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        window = self.settings_window = tk.Toplevel(app.root)
        window.title("上肢软件预览设置")
        box = ttk.Frame(window, padding=12)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="这些是软件预览范围，尚未作为实机机械限位验收。").grid(row=0, column=0, columnspan=4, pady=(0, 8))
        variables = []
        settings = self.controller.settings
        for i, (name, pair) in enumerate(zip(AXIS_NAMES, settings.ranges), 1):
            ttk.Label(box, text=name).grid(row=i, column=0, sticky="w")
            row = []
            for j, value in enumerate(pair, 1):
                variable = tk.StringVar(value=str(value))
                ttk.Entry(box, textvariable=variable, width=9).grid(row=i, column=j, padx=6, pady=3)
                row.append(variable)
            variables.append(row)
        ttk.Label(box, text="每行：下限 / 上限（度）").grid(row=7, column=0, columnspan=4)
        active, reverse = [], []
        for i, name in enumerate(GROUP_NAMES, 8):
            a, r = tk.BooleanVar(value=settings.active[i-8]), tk.BooleanVar(value=settings.reverse[i-8])
            ttk.Checkbutton(box, text=name, variable=a).grid(row=i, column=0, sticky="w")
            ttk.Checkbutton(box, text="反向", variable=r).grid(row=i, column=1)
            active.append(a)
            reverse.append(r)
        speed = tk.StringVar(value=str(settings.speed))
        ttk.Label(box, text="目标速度上限（°/秒）").grid(row=13, column=0)
        ttk.Entry(box, textvariable=speed, width=9).grid(row=13, column=1)
        message = tk.StringVar(value="双大臂作为同一组启停，使用同一个控制进度。")
        ttk.Label(box, textvariable=message, wraplength=440).grid(row=14, column=0, columnspan=4, pady=8)
        def save():
            try:
                value = Settings(tuple(tuple(float(v.get()) for v in row) for row in variables),
                                 tuple(v.get() for v in active), tuple(v.get() for v in reverse), float(speed.get()))
                self.controller.set_settings(value)
                if app.log:
                    app.log.event("upper_settings", settings=asdict(value))
                self.cal_status.set("上肢预览设置已更新；点击启用并重新匹配起始位")
                window.destroy()
            except (ValueError, tk.TclError, OSError) as error:
                message.set(str(error))
        ttk.Button(box, text="应用并保持暂停", command=save).grid(row=15, column=0, columnspan=4)
