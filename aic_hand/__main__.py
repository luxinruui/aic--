"""Portable entry point: python -m aic_hand [--demo | --check]."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from . import __version__
from .config import DEFAULT_CONFIG, load


def check_environment():
    import cv2
    import mediapipe as mp
    import numpy as np
    import PIL
    if mp.__version__ != "0.10.21" or not hasattr(mp, "solutions"):
        raise RuntimeError("请通过 setup_preview.cmd 安装 requirements.txt 中的独立环境。")
    package = Path(mp.__file__).parent
    models = {}
    for relative in ("modules/palm_detection/palm_detection_full.tflite", "modules/hand_landmark/hand_landmark_full.tflite"):
        path = package / relative
        if not path.is_file():
            raise RuntimeError("安装包缺少内置手部模型：" + relative)
        models[relative] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with mp.solutions.hands.Hands(static_image_mode=True, model_complexity=1, max_num_hands=2) as detector:
        blank = detector.process(np.zeros((480, 640, 3), dtype=np.uint8))
    report = {"app_version": __version__, "python": sys.version.split()[0], "mediapipe": mp.__version__,
              "opencv": cv2.__version__, "numpy": np.__version__, "pillow": PIL.__version__,
              "models": models, "blank_frame_hands": len(blank.multi_hand_landmarks or ()),
              "model_inference": "passed", "camera_tested": False, "robot_connected": False}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def headless(config, demo, frames, log_directory):
    from .capture import Failure, Worker
    from .core import Mapper
    from .runlog import RunLog
    worker, mapper = Worker(config, demo), Mapper(config)
    log = RunLog(config, "demo" if demo else "camera", log_directory)
    status, count, reason = 0, 0, "完成指定帧数"
    deadline = time.monotonic() + 20
    worker.start()
    try:
        while count < frames:
            item = worker.latest()
            if isinstance(item, Failure):
                raise RuntimeError(item.message)
            if item:
                now = time.monotonic()
                mapping = mapper.process(item.observation, now)
                log.frame(item.observation, mapping, now, config["mode"])
                count += 1
                deadline = now + 10
            elif time.monotonic() > deadline:
                raise RuntimeError("长时间没有新采样，预览已结束。")
            time.sleep(.005)
    except KeyboardInterrupt:
        reason = "用户中断"
    except Exception as error:
        status, reason = 2, str(error)
        log.event("error", message=reason)
        print(reason, file=sys.stderr)
    finally:
        worker.stop()
        worker.join(timeout=1)
        log.close(reason)
    print(json.dumps({"status": status, "source": "demo" if demo else "camera", "frames": count,
                      "log_directory": str(log.directory), "robot_connected": False}, ensure_ascii=False))
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description="AIC 电脑端手势预览；没有机器人通信或舵机输出。")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--camera", type=int, help="摄像头编号，默认从配置读取")
    parser.add_argument("--demo", action="store_true", help="使用合成手部演示，不访问相机")
    parser.add_argument("--headless", action="store_true", help="不打开窗口，记录指定帧数")
    parser.add_argument("--frames", type=int, default=150)
    parser.add_argument("--mode", choices=("single", "six"))
    parser.add_argument("--log-dir", type=Path, help="新建日志目录；必须尚不存在")
    parser.add_argument("--check", action="store_true", help="检查依赖与内置模型，并对空白图做真实推理")
    args = parser.parse_args(argv)
    try:
        if args.check:
            return check_environment()
        from .config import validate
        config = load(args.config)
        if args.camera is not None:
            config["camera"] = args.camera
        if args.mode:
            config["mode"] = args.mode
        validate(config)
        if args.frames <= 0:
            parser.error("--frames 必须大于 0")
        if args.headless:
            return headless(config, args.demo, args.frames, args.log_dir)
        from .gui import launch
        launch(config, args.config, args.demo, args.log_dir)
        return 0
    except Exception as error:
        print("启动失败：%s\n请查看 README，或重新运行 setup_preview.cmd。" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
