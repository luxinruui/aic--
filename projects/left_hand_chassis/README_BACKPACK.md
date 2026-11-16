# 左手底盘控制：智慧背包离线接入 v1.2

此包面向现有AIC智元素AI背包（资料对应RK3588S、Ubuntu20.04、Python3.8）。电脑仍负责摄像头手势识别；背包只接收控制数据并输出底盘UART报文。

**控制链路：电脑摄像头 → 左手识别与本次校准 → 局域网TCP 8768 → 智慧背包 → `/dev/ttyS0`、115200/8E1 → 机器人底盘。**

NoMachine用于传文件、打开背包终端；运行时由本项目自己的TCP连接传目标，不经NoMachine转发控制数据。2026-11-16已通过NoMachine部署到实际背包，Python3.8.10下30项自检通过，实际UART模式及电脑到背包的OFF确认链路通过。实际运动验收进度见 `logs/development/2026-11-16-deployment.md`。

## 1. 用NoMachine拷入背包

1. 将 `left_hand_chassis_backpack_v1_2_20261116.zip` 通过NoMachine传至背包桌面。
2. 完整解压，得到 `~/Desktop/aic_left_hand_chassis/`。文件夹里应能直接看到 `run_backpack.py`、`robot_link`、`robot_config.json`、`offline_deps`。
3. 电脑与背包连接同一可互通的Wi-Fi/手机热点。通过NoMachine打开背包终端，执行 `hostname -I` 查看背包当前IPv4。不要沿用历史IP，也不要填电脑自己的IP。
4. 关闭巡线、键盘底盘、IMU体感及其他可能占用机器人 `/dev/ttyS0` 的控制程序；只运行一个机器人接收端。本功能不使用灰度CH340或IMU CP2104串口。

## 2. 离线检查，不驱动

```bash
cd ~/Desktop/aic_left_hand_chassis
python3 run_backpack.py --check-deps
python3 run_backpack.py --self-test
```

`--check-deps` 校验随包官方pySerial3.5 wheel并直接导入，不打开串口；不要求pip、不访问互联网、不安装或更改系统依赖。`--self-test`只用本机回环TCP和模拟串口，打印测试结果，不驱动机器人。

先启动预览接收端：

```bash
python3 -u run_backpack.py
```

看到 `PREVIEW / NO UART | default OFF`。此模式完全不打开UART。也可运行 `sh start_backpack_preview.sh`。

## 3. 电脑连接并检查手势

1. 电脑解压完整新版项目，双击 **`run_robot.cmd`**。原 `run_pc.cmd` 仍可只看本地预览。
2. 点击“连接机器人…”，输入刚查询的背包IPv4，端口保持 **8768**，点击“连接”。双方 `robot_config.json` 必须一致，否则拒绝连接。
3. 窗口显示“背包预览”后，返回主控制窗口，打开摄像头，依次采集①静止位、②握拳、③展开。静止位用自然半握，三个参照必须能区分。
4. 点击“启用背包预览”，回到校准静止位保持0.4秒。握拳前进、五指展开后退；像方向盘一样左右倾斜转向。可叠加前后和旋转。
5. 背包终端及日志显示 `READY/MOVING` 和底盘软件指令。松手回静止、空格、失焦和断开连接时检查目标归零。ACK和MOVING是软件状态，不是编码器或实际轮速反馈。

也可在电脑命令行指定本次查到的地址启动：`run_robot.cmd --host <背包IPv4>`，可加 `--port 8768`。启动仅连接，仍为OFF，必须重新完成本次校准并手动启用。

排查网络时，可在电脑执行 `python scripts/check_connection.py <背包IPv4> --expect preview`；实机接收端则用 `--expect hardware`。此工具只发OFF和零目标，不采集摄像头、不进入使能状态；至少需要20个逐包确认才报告成功。可用 `--output logs/verification/check.json` 保存结果。检查前先断开GUI连接，背包每次只接受一个电脑连接。

合成演示只允许在预览接收端上测试；实机模式在电脑端与背包端都会拒绝 `demo` 输入。

## 4. 机器人到手后，切换实机

先在电脑暂停并断开，在背包预览终端按Ctrl+C退出。保留活动空间，使用当前默认低强度，启动：

```bash
python3 -u run_backpack.py --drive
```

或 `sh start_backpack_robot.sh`。此时才独占打开 `/dev/ttyS0`，发送底盘STOP、模式0、STOP；没有手臂、回零或舵机力矩命令，不需要上肢T形标定。

电脑重新点击连接，确认显示“实机”，按本次校准流程手动启用。接收端不会因旧连接、旧ON或重连自动恢复运动。第一次依次短测前进、后退、原地左转、原地右转，再测试前进叠加旋转；实际方向和停止效果必须现场确认。

## 5. 输出与停止

- 新版专用TCP **8768**，不兼容旧8765/8766/8767接收端。
- 厂商底盘设备0x08、模式0；前进方向0°、后退180°；左转为正、右转为负，沿用原AIC键盘Q/E符号。平移和旋转写入同一个有限时长帧，避免相互清零。
- 默认速度上限：平移20、旋转15；电脑初始强度35%对应约7、5，属于厂商控制量，不是物理单位。背包分别限制加速40/60单位每秒；前后反转经过零，轴释放和停止绕过缓加速直接清零。
- 独立20Hz控制线程；数据停更150ms或原始采样与传输合计过期300ms时解除使能并发STOP。常规线程调度下还需计入约一个50ms调度周期；这是普通Linux/Python软件保护，实际制动时延需实测。
- 所有运动帧执行时间默认150ms。串口短写/异常锁定FAULT、关闭端口、不自动重试；不能仅凭写入成功保证机器人机械停止或到位。
- 每次连接先确认OFF，再以新代次和零目标启用。断线、失焦、丢手、旧数据及异常之后，都要手动重新启用。
- `robot_config.json`在电脑和背包保持完全相同。修改速度上限后两边一起更新并重启连接；首次保留默认值。

## 6. 日志与常见问题

背包日志在 `logs/backpack/<时间>.jsonl`，包含启动模式、连接结束原因、状态和输出命令，不保存影像。实测后可通过NoMachine拷回电脑同一项目的 `logs/backpack/`，在Git克隆中用 `sync_logs.cmd` 提交；背包ZIP本身无Git登录或自动上传。

- **连接失败**：检查当前IP、同一热点、8768接收端是否启动；热点可能启用客户端隔离。不要把NoMachine能连当作此TCP端口已通。
- **配置不匹配**：使用同一发布包的 `robot_config.json`，核对两端文件。
- **串口不存在/占用**：机器人UART应为 `/dev/ttyS0`；先退出旧控制进程，检查 `ls -l /dev/ttyS0`。不要改成灰度或IMU的ttyUSB口。
- **pySerial校验失败**：重新完整复制已核验的离线包，不绕过wheel哈希检查。
- **窗口有手势但没动作**：确认连接标记是“实机”，接收端带了 `--drive`，已完成三步校准并明确启用。预览和合成演示不会驱动实机。

协议与复用接口见 [ROBOT_PROTOCOL.md](docs/ROBOT_PROTOCOL.md)。

需要插入米联客控制中继时，背包入口和8768端口保持不变；PC改连板卡8769。详见完整版项目的README_MLK.md。
