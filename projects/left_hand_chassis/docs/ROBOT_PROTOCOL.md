# 独立底盘接入协议 v1

应用标识 `aic-left-hand-chassis`，默认TCP8768，UTF-8 JSON Lines，每条最多2048字节。电脑为客户端，背包为单客户端服务端。此为本项目专用协议，旧上肢/键盘协议不可混用。

接收端为每个连接生成随机session和一次性challenge，HELLO声明硬件模式与配置SHA256；每次ACK给出新challenge，下一条命令必须携带该challenge、递增seq和相同profile。两台设备不比较系统墙钟；采样年龄加上接收端challenge已存活时长作为保守的新鲜度判断。

命令包含启用generation、输入会话、原摄像头frame_id、来源camera/demo、归一化forward/yaw_left及age_ms。重复摄像头帧不能刷新原始采样寿命或修改运动目标。disabled报文必须为零目标。实机拒绝demo来源；来源是本项目的模式隔离字段，不是第三方身份认证。

启用顺序：

1. 新连接必须发送OFF并获得ACK。
2. 电脑本地校准完成、操作者回静止位且明确启用后，发送新的generation与零目标。
3. 背包READY确认同一generation后，发送手势目标。
4. 停止/超时会封锁旧generation。恢复须OFF和新的启用代次；TCP不会自动重连恢复。

电脑只维护一份最新快照，GUI停止更新超过150ms时网络线程自行发OFF。背包独立20Hz线程限制加速、处理超时并写唯一UART；TCP读取阻塞不会阻止停车检查。两端同时保留原始采样年龄上限300ms。

UART复用原AIC底盘编码：帧头F5 5F、设备08、命令01模式0或命令02移动、长度、little-endian的方向/速度/有符号转速/毫秒时长，末尾按厂商规则求反校验。已有89组厂商运动报文向量和模式帧用于逐字节回归。UART是115200、8数据位、偶校验、1停止位；Linux exclusive。

`robot_link`只依赖Python3.8标准库，硬件模式另需随包pySerial。它不导入MediaPipe、OpenCV、Tk或上肢控制模块。`run_backpack.py`默认预览；`--drive`才构造串口对象。`--self-test`和`--check-deps`与`--drive`互斥，避免检查入口意外打开硬件。

当前验证使用真实本机TCP、真实编码器和显式FakeSerial；没有访问真实机器人UART，不能据此声称实机精度、方向和停止时延已通过。
