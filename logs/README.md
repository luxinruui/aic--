# 开发和运行日志

`development/` 记录开发决定、检查结果及待实机验证事项。`verification/` 保存实际执行命令的结果。

`runs/` 由预览程序自动创建，每次运行一个独立目录，包含 UTF-8 JSON、JSONL 以及带 UTF-8 BOM 的 CSV。文件中的 `source: demo` 表示合成演示，`source: camera` 才表示摄像头输入；`robot_connected` 在本阶段始终为 `false`。

CSV 约每秒采样 10 行，`run.json` 的帧数统计包含所有已消费帧；两者数量不同是正常现象。无效目标的数值列留空，不以零度代替。日志不包含原始摄像头图像或视频。

本仓库中的演示日志是软件验证证据，不代表真人识别精度或舵机运动效果。

用户在电脑上结束预览后，可双击仓库根目录 `sync_github.cmd` 同步新日志和标定设置。该脚本依赖 Git 克隆和普通 GitHub 登录，不在应用中存储令牌，不强制推送。

v0.1.1 起，`startup/` 保存启动和安装输出，并可随同步脚本提交。`latest.log` 为最新一次 Python 启动日志，`previous.log` 为上一次；`launcher.log` 用于定位 Python 还没启动时的错误。仅下载 ZIP、尚未配置 Git 的电脑，也能直接把这些文本日志发来排查。
