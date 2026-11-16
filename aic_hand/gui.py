"""Tk preview. Nothing in this application connects to a robot."""
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from .capture import CONNECTIONS, Failure, Worker
from .config import AXES, AXIS_LABELS, SERVO_IDS, copy_config, finite, save, validate
from .core import Mapper
from .runlog import RunLog

MODE_NAMES = {"单关节：掌心上下控制": "single", "双手：六轴连续控制": "six"}
AXIS_NAMES = {"%s · ID %s" % (AXIS_LABELS[a], SERVO_IDS[a]): a for a in AXES}


class PreviewApp:
    def __init__(self, root, config, config_path, demo=False, log_directory=None):
        self.root, self.config, self.config_path = root, copy_config(config), config_path
        self.log_directory = log_directory
        self.worker = self.log = self.latest_sample = None
        self.mapper = Mapper(config)
        self.mapping = self.mapper.invalid("等待开始预览")
        self.last_received = None
        self.photo = None
        self.closed = False
        self.poll_id = None
        root.title("AIC 手势控制 · 电脑端预览")
        root.geometry("1180x760")
        root.minsize(1000, 720)
        root.configure(bg="#edf2f7")
        families = set(tkfont.families(root))
        self.font_family = next((name for name in ("Microsoft YaHei", "Noto Sans CJK SC", "PingFang SC", "Arial")
                                 if name in families), "sans-serif")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family=self.font_family, size=10)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#edf2f7")
        style.configure("TLabel", background="#edf2f7", font=(self.font_family, 10))
        style.configure("Title.TLabel", font=(self.font_family, 21, "bold"), foreground="#14283f")
        style.configure("Hint.TLabel", foreground="#52657c")
        style.configure("TButton", padding=(9, 5))
        style.configure("Treeview", rowheight=24, font=(self.font_family, 10))
        style.configure("Treeview.Heading", font=(self.font_family, 10, "bold"))
        shell = ttk.Frame(root, padding=18)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="用双手，预览机器人的动作", style="Title.TLabel").pack(anchor="w")
        ttk.Label(shell, text="阶段 1 / 电脑预览     尚未连接机器人，数值为软件目标角", style="Hint.TLabel").pack(anchor="w", pady=(5, 16))
        body = ttk.Frame(shell)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=370)
        body.rowconfigure(0, weight=1)
        video = ttk.Frame(body)
        video.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        self.canvas = tk.Canvas(video, background="#122034", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.render())
        ttk.Label(video, text="单关节：选中的那只手上下移动\n六轴：上下抬肩 · 向中间移动合拢 · 拇指食指开合弯肘",
                  style="Hint.TLabel").pack(anchor="w", pady=(10, 0))
        panel = ttk.Frame(body)
        panel.grid(row=0, column=1, sticky="nsew")
        self.mode_var = tk.StringVar(value=next(k for k, v in MODE_NAMES.items() if v == config["mode"]))
        self.axis_var = tk.StringVar(value=next(k for k, v in AXIS_NAMES.items() if v == config["single_axis"]))
        ttk.Label(panel, text="预览模式").pack(anchor="w")
        self.mode_combo = ttk.Combobox(panel, state="readonly", values=list(MODE_NAMES), textvariable=self.mode_var)
        self.mode_combo.pack(fill="x", pady=(4, 6))
        self.mode_combo.bind("<<ComboboxSelected>>", self.change_mode)
        self.axis_combo = ttk.Combobox(panel, state="readonly", values=list(AXIS_NAMES), textvariable=self.axis_var)
        self.axis_combo.pack(fill="x", pady=(0, 12))
        self.axis_combo.bind("<<ComboboxSelected>>", self.change_mode)
        camera_row = ttk.Frame(panel)
        camera_row.pack(fill="x")
        ttk.Label(camera_row, text="摄像头编号").pack(side="left")
        self.camera_var = tk.StringVar(value=str(config["camera"]))
        self.camera_combo = ttk.Combobox(camera_row, width=5, state="readonly", values=list(range(8)), textvariable=self.camera_var)
        self.camera_combo.pack(side="left", padx=(8, 12))
        self.mirror_var = tk.BooleanVar(value=config["mirror"])
        ttk.Checkbutton(camera_row, text="镜像画面", variable=self.mirror_var, command=self.render).pack(side="left")
        self.swap_var = tk.BooleanVar(value=config["swap_hands"])
        ttk.Checkbutton(panel, text="交换左右手标记（停止后设置）", variable=self.swap_var).pack(anchor="w", pady=(6, 8))
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="开始摄像头预览", command=lambda: self.start(False))
        self.start_button.pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(buttons, text="暂停 / 空格", command=self.stop)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.demo_button = ttk.Button(panel, text="没有相机？查看演示数据", command=lambda: self.start(True))
        self.demo_button.pack(fill="x", pady=(8, 12))
        self.table = ttk.Treeview(panel, columns=("name", "angle", "status"), show="headings", height=6)
        for key, label, width in (("name", "关节", 115), ("angle", "目标角", 80), ("status", "状态", 150)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, stretch=key == "status")
        for axis in AXES:
            self.table.insert("", "end", iid=axis, values=(AXIS_LABELS[axis], "—", "等待预览"))
        self.table.pack(fill="x")
        self.table.tag_configure("valid", foreground="#09785d")
        self.table.tag_configure("invalid", foreground="#65758a")
        ttk.Label(panel, text="默认参数可直接预览。左右以操作者自身为准。", style="Hint.TLabel").pack(anchor="w", pady=(12, 5))
        self.side_var = tk.StringVar(value="左手")
        self.calibration_window = None
        ttk.Button(panel, text="调整手部标定…", command=self.open_calibration).pack(fill="x", pady=(5, 0))
        ttk.Button(panel, text="保存当前设置", command=self.save_settings).pack(fill="x", pady=(6, 0))
        self.status_var = tk.StringVar(value="选择模式后开始预览；首次建议只预览左肘。")
        ttk.Label(shell, textvariable=self.status_var, wraplength=1100).pack(anchor="w", pady=(12, 3))
        self.log_var = tk.StringVar(value="运行日志将在开始预览后自动创建。")
        ttk.Label(shell, textvariable=self.log_var, style="Hint.TLabel", wraplength=1100).pack(anchor="w")
        self.root.bind("<space>", lambda event: self.stop())
        self.root.bind("<Escape>", lambda event: self.stop())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.change_mode()
        self.poll_id = root.after(30, self.poll)
        if demo:
            root.after(100, lambda: self.start(True))

    def change_mode(self, event=None):
        self.config["mode"] = MODE_NAMES[self.mode_var.get()]
        self.config["single_axis"] = AXIS_NAMES[self.axis_var.get()]
        self.axis_combo.configure(state="readonly" if self.config["mode"] == "single" else "disabled")
        self.mapper = Mapper(self.config)
        self.mapping = self.mapper.invalid("等待新采样")
        if self.log:
            self.log.event("mode_change", mode=self.config["mode"], single_axis=self.config["single_axis"])
        self.update_table()

    def start(self, demo=False):
        if self.worker and self.worker.is_alive():
            self.status_var.set("摄像头仍在运行或退出中，请先暂停并稍等。")
            return
        if self.log:
            self.log.close()
        self.config.update(camera=int(self.camera_var.get()), mirror=self.mirror_var.get(), swap_hands=self.swap_var.get())
        self.mapper = Mapper(self.config)
        self.mapping = self.mapper.invalid("正在启动")
        self.latest_sample = None
        self.last_received = time.monotonic()
        try:
            self.log = RunLog(copy_config(self.config), "demo" if demo else "camera", self.log_directory)
        except OSError as error:
            self.log = None
            self.status_var.set("无法创建日志目录：" + str(error))
            return
        self.log_directory = None  # An explicit output directory is used once, never overwritten.
        self.worker = Worker(self.config, demo)
        self.worker.start()
        self.start_button.configure(state="disabled")
        self.demo_button.configure(state="disabled")
        self.camera_combo.configure(state="disabled")
        self.status_var.set("演示数据：合成手部，不代表摄像头识别效果。" if demo else "正在打开摄像头，请把手完整放入画面。")
        self.log_var.set("日志：" + str(self.log.directory))
        self.update_table()

    def stop(self, reason="用户暂停"):
        if self.worker:
            self.worker.stop()
        if self.log:
            self.log.close(reason)
            self.log = None
        self.mapping = self.mapper.invalid(reason)
        self.latest_sample = None
        self.last_received = None
        self.update_table()
        self.render()
        self.status_var.set("已暂停。目标已清空；再次开始会使用新的采样。")

    def poll(self):
        if self.closed:
            return
        if self.worker:
            item = self.worker.latest()
            if isinstance(item, Failure):
                if self.log:
                    self.log.event("error", message=item.message)
                self.stop("摄像头或识别异常")
                self.status_var.set(item.message)
            elif item and self.log and not self.worker.stop_event.is_set():
                now = time.monotonic()
                self.latest_sample = item
                self.last_received = now
                self.mapping = self.mapper.process(item.observation, now)
                self.log.frame(item.observation, self.mapping, now, self.config["mode"])
                valid = sum(axis.valid for axis in self.mapping.axes.values())
                if item.observation.source == "demo":
                    self.status_var.set("演示数据（非摄像头） · 有效目标 %s · 不会驱动机器人" % valid)
                else:
                    self.status_var.set("摄像头预览 · 识别到 %s 只手 · 有效目标 %s · 推理 %.0f ms" % (
                        len(item.observation.hands), valid, item.observation.inference_ms))
                self.update_table()
                self.render()
            if self.log and self.last_received is not None and time.monotonic() - self.last_received > self.config["max_age_seconds"]:
                if any(axis.valid for axis in self.mapping.axes.values()):
                    self.log.event("stale", message="没有及时收到新的有效采样")
                self.mapping = self.mapper.invalid("采样超时，目标已清空")
                self.latest_sample = None
                self.update_table()
                self.render()
                self.status_var.set("采样超时，目标已清空；等待新画面。")
            if not self.worker.is_alive():
                self.start_button.configure(state="normal")
                self.demo_button.configure(state="normal")
                self.camera_combo.configure(state="readonly")
        self.poll_id = self.root.after(30, self.poll)

    def update_table(self):
        for axis in AXES:
            state = self.mapping.axes[axis]
            value = "%.1f°" % state.target if state.valid else "—"
            self.table.item(axis, values=(AXIS_LABELS[axis], value, state.reason), tags=("valid" if state.valid else "invalid",))

    def render(self):
        from PIL import Image, ImageDraw, ImageOps, ImageTk
        width, height = max(10, self.canvas.winfo_width()), max(10, self.canvas.winfo_height())
        self.canvas.delete("all")
        if self.latest_sample is None:
            self.canvas.create_text(width / 2, height / 2, text="AIC 手势预览\n\n让手部完整进入画面", fill="#c7d8ea",
                                    font=(self.font_family, 19), justify="center")
            return
        sample = self.latest_sample
        image = sample.image.copy() if isinstance(sample.image, Image.Image) else Image.fromarray(sample.image)
        if self.mirror_var.get():
            image = ImageOps.mirror(image)
        draw = ImageDraw.Draw(image)
        iw, ih = image.size
        for hand in sample.observation.hands:
            if len(hand.points) != 21 or not all(finite(p.x) and finite(p.y) for p in hand.points):
                continue
            color = "#55e6bf" if hand.side == "Left" else "#82b6ff"
            points = [((1 - p.x if self.mirror_var.get() else p.x) * iw, p.y * ih) for p in hand.points]
            if len(points) != 21:
                continue
            for a, b in CONNECTIONS:
                draw.line((points[a], points[b]), fill=color, width=3)
            for x, y in points:
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=color)
            x, y = points[0]
            draw.text((max(0, x - 25), min(ih - 16, y + 12)), hand.side, fill=color)
        draw.rectangle((8, 8, 370, 35), fill="#122034")
        draw.text((15, 15), "DEMO / SYNTHETIC DATA" if sample.observation.source == "demo" else "CAMERA / PREVIEW ONLY", fill="white")
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.create_image(width / 2, height / 2, image=self.photo)

    def calibrate(self, key):
        side = "Left" if self.side_var.get() == "左手" else "Right"
        feature = self.mapping.features.get(side)
        if (self.latest_sample is None or self.latest_sample.observation.source != "camera" or feature is None or
                time.monotonic() - self.mapping.sampled_at > self.config["max_age_seconds"]):
            self.status_var.set("请先用真实摄像头识别所选手，再记录标定；演示数据不能用于标定。")
            return
        candidate = copy_config(self.config)
        value = feature.y if key.startswith("y_") else feature.x if key.startswith("x_") else feature.pinch
        candidate["calibration"][side][key] = value
        try:
            validate(candidate)
        except ValueError as error:
            self.status_var.set("未采用该标定：" + str(error))
            return
        self.config = candidate
        self.mapper = Mapper(self.config)
        self.mapping = self.mapper.invalid("标定改变，等待新采样")
        self.update_table()
        if self.log:
            self.log.event("calibration", side=side, key=key, value=value)
        self.status_var.set("已记录%s的%s；完成后点击保存当前设置。" % (self.side_var.get(), key))

    def open_calibration(self):
        if self.calibration_window and self.calibration_window.winfo_exists():
            self.calibration_window.lift()
            return
        window = self.calibration_window = tk.Toplevel(self.root)
        window.title("手部控制范围标定")
        window.transient(self.root)
        content = ttk.Frame(window, padding=18)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="保持真实摄像头预览，选择要标定的手。\n把手移到对应位置，再点记录；默认设置也可直接使用。",
                  wraplength=390).pack(anchor="w", pady=(0, 12))
        ttk.Combobox(content, state="readonly", values=("左手", "右手"), textvariable=self.side_var).pack(fill="x")
        grid = ttk.Frame(content)
        grid.pack(fill="x", pady=8)
        for index, (label, key) in enumerate((("记录上边界", "y_top"), ("记录下边界", "y_bottom"),
                                              ("记录外侧", "x_out"), ("记录内侧", "x_in"),
                                              ("记录捏合", "pinch_closed"), ("记录张开", "pinch_open"))):
            ttk.Button(grid, text=label, command=lambda key=key: self.calibrate(key)).grid(
                row=index // 2, column=index % 2, sticky="ew", padx=3, pady=3)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        ttk.Label(content, textvariable=self.status_var, wraplength=390).pack(fill="x", pady=8)
        ttk.Button(content, text="保存设置", command=self.save_settings).pack(fill="x")

    def save_settings(self):
        candidate = copy_config(self.config)
        candidate.update(camera=int(self.camera_var.get()), mirror=self.mirror_var.get(), swap_hands=self.swap_var.get())
        try:
            save(candidate, self.config_path)
            self.config = candidate
        except (OSError, ValueError) as error:
            self.status_var.set("保存失败：" + str(error))
            return
        self.status_var.set("设置已保存；左右手标记设置在下次开始时生效。")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.poll_id:
            self.root.after_cancel(self.poll_id)
        self.stop("关闭窗口")
        if self.worker:
            self.worker.join(timeout=.5)
        self.root.destroy()


def launch(config, config_path, demo=False, log_directory=None):
    root = tk.Tk()
    PreviewApp(root, config, config_path, demo, log_directory)
    root.mainloop()
