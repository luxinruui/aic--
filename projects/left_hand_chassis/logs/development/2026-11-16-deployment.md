# 2026-11-16 智慧背包现场部署

## 已完成

- 用户已连接NoMachine，授权直接配置机器人；现场确认机器人开机、有活动空间、能够配合低速测试。
- NoMachine原生传输背包离线包，界面显示119.4KB、Completed。背包回读SHA256为 `dac0ea084de269794049b4fec66110f60162b4d7a7d567d2a9356f1186177606`，与本机包一致；新建独立目录 `~/Desktop/aic_left_hand_chassis/`，原项目未覆盖。
- 背包Python3.8.10；当前用户属于dialout，`/dev/ttyS0`存在，fuser未列出占用进程，Python进程列表未见旧巡线、IMU或底盘控制程序。
- 随包pySerial3.5哈希校验、直接导入成功，不安装系统依赖。背包本机30项软件自检全部通过，用时5.475秒，未访问真实UART。
- 先启动PREVIEW/NO UART，PC经真实局域网连接，5秒取得89个OFF确认，接收端持续DISARMED/零输出；检查报告为 `logs/verification/deployment_preview_off_20261116.json`。
- 正常退出预览后，运行 `python3 run_backpack.py --drive`。真实UART以115200/8E1独占打开，按源码发送底盘STOP/模式0/STOP；启动与检查期间无串口异常。实机OFF检查5秒取得79个确认，持续DISARMED/零输出，报告为 `logs/verification/deployment_hardware_off_20261116.json`。
- 两端配置SHA256均为 `8f2dafbe66103b273ecea4144c56b1ad63ed8326032f0931c15b82021fcc32a4`。平移/旋转上限20/15，PC默认强度35%，对应最大约7/5厂商控制量；不是物理速度单位。
- PC启动并连接实际背包，界面显示“实机 · DISARMED”；摄像头0可正常显示真人与手部骨架。未用合成数据冒充实机手势。

## 本轮代码改动

- PC支持 `run_robot.cmd --host <背包IPv4> [--port 8768]`，仅连接，仍从OFF开始；三步校准和手动启用保持有效。
- 新增 `scripts/check_connection.py`，只发送OFF/零目标，检查实际接收端模式、配置、至少20个确认及持续DISARMED；可保存JSON报告。不会打开相机或使能运动。
- 运行日志现在记录实际接收端连接/硬件模式，并在曾连接实机时记录 `hardware_connected=true`。历史旧日志的该字段恒false，不能据此否认本次已完成的UART连接。
- 更新后的真实Tk+本机TCP预览回归通过；该回归使用合成手势和模拟执行端，没有接触机器人串口。

## 现场状态与待完成事项

接收端正在前台运行；日志在背包 `logs/backpack/20261116-224623-957522.jsonl`。电脑端当前保持OFF，等待操作人完成本次自然半握/握拳/展开三步校准，然后进行低速前、后、左、右及暂停验证。

当前证据证明实际部署、相机画面、TCP确认和UART写入链路可用。尚未发送非零目标，不能称机器人已经实际跟随手势；方向、轮速和制动效果仍需现场观察。UART写入成功及软件ACK不代表电机反馈。
