# 2026-11-17 Windows与NoMachine现场部署

用户要求部署GitHub中新设计的双手底盘/上肢项目，并提供运行方法。定位到独立分支 `dual-hand-robot-20261117`、原提交 `97c5560ef26a94ce7a9a9f5a9e6c456dbc495a76`，路径 `projects/dual_hand_robot`。没有合并main或修改旧底盘目录。

## 现场导入和版本

电脑已存在 `C:\Users\35823\Desktop\dual_hand_pc_v1_20261117\dual_hand_robot_pc`，独立venv可用；所有存在的项目运行源码与指定Git提交逐项Git blob匹配，缺少的主要是未随电脑ZIP附带的历史日志和旧接收端测试，不是版本冲突。原电脑ZIP192584字节，SHA256 `a6b0c9fdafa4c8734c03326950caff96ab86e96f35122d7b3ff3028fed83f6c3`。

NoMachine `172.20.10.3` 已连接。背包原终端停在用户已解压ZIP后的提示符，独立目录 `/home/bcsh/Desktop/aic_dual_hand_robot_20261117` 已存在，因此没有重复覆盖安装。`python3 install_backpack.py --check` 实际返回32文件校验成功，未打开UART；背包ZIP的现场sha256sum与本机一致：136788字节、`8b3975f64e499a7dddaf705932ef33d6c6f04671b23de38c4f8e45897c8d55d4`。

`python3 -S verify_backpack.py` 实际PASS，包含离线pySerial、自检及仅本机回环OFF测试，不打开UART。报告在背包 `logs/verification/import-check-20261117-233620-265104/summary.json`。随后fuser未列出ttyS0占用，pgrep仅见系统Python服务；没有强杀其他控制进程。

## 电脑端焦点修复

已有电脑启动日志包含 `KeyError: 'popdown'`：Tk原生ttk下拉窗有焦点时，其路径可能没有对应Python widget。原focus_displayof/focus_get直接转对象会抛错。本次统一通过control_window_focused查询，捕获KeyError/TclError，将该时刻视为失焦并暂停，继续调度轮询。

先在workspace独立副本修改和回归，再备份、替换桌面项目的gui.py与GUI回归脚本，更新package_manifest中的大小和SHA256。备份后缀 `before_focus_20261117-234028`；原清单也保留。回归注入原生popdown/焦点窗口消失两种错误，确认暂停、不再追加上肢轨迹、GUI序号继续推进。原生Windows现场又实际打开模式下拉框并选中“双手联合”，摄像头和状态更新持续正常。

本机检查：47项电脑测试、18项新接收端测试、实际Tk+本机TCP+FakeSerial联控回归、上肢Tk回归、真实MediaPipe模型空白推理全部通过。详见 `logs/verification/deployment_20261117/`；这些回归未连接真实UART或摄像头。

## 真实网络与UART

先运行 `sh start_preview.sh`，电脑经真实局域网检查5秒OFF，取得73个ACK、无上肢新增轨迹或非零底盘目标。预览中的baseline_confirmed=true只表示软件预览模拟参考，不表示实际机器人基准已确认。

用户明确回答“还没设置，先完成部署”，因此不加 `--confirm-arm-baseline`。Ctrl+C正常结束预览，23:41:27运行 `sh start_robot.sh`，实际独占打开 `/dev/ttyS0`、115200/8E1，启动只写底盘STOP/模式0/STOP共3帧，arm_segments=0。背包日志 `logs/backpack/20261117-234127-654267.jsonl`，状态DISARMED，arm baseline=False。

电脑实机OFF检查5秒取得74个ACK；hardware=true、baseline_confirmed=false、arm_segments_added=0、nonzero_chassis_commands=0。两端配置签名 `4a15394412d259fcba41b64deab3a9e195b82be5ec1fb21fad880e46977541a6` 一致。没有发送本项目上肢运动或非零底盘指令，不能标记新双手实机动作已验收。

## 留给用户的运行状态

启动桌面实际项目 `python -u -m left_hand_drive --dual-robot --host 172.20.10.3 --port 8770`，已开摄像头0、观察实时真人画面与Left骨架、选中双手联合。窗口实机DISARMED，上肢/底盘均OFF，没有代采本次校准。当前数值日志 `camera-20261117-234254-861258` 的快照包含1315行底盘、1338行上肢数据，enabled均0、底盘非零均0；不保存影像。

桌面新增 `启动双手机器人.cmd` 自动连接当前IP/8770。现场运行说明保存为RUN_THIS_ROBOT.md，并复制到桌面项目；首次应人工设置基准、正常重启接收端并显式确认，电脑手动重连、完成双手新校准、从左肘0～20°单轴开始验收。

重新生成并CRC/逐字节回读电脑与背包ZIP，包含焦点修复及运行说明；桌面新副本以 `_deployed.zip` 结尾，原ZIP保留。背包当前运行的原接收源码与新源码相同，本轮修改为电脑焦点和说明/构建，不需要为此重启或覆盖当前背包。新离线包哈希见release_manifest.json；不要把新生成包的哈希当成原现场导入包哈希。
