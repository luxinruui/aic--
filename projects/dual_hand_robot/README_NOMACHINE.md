# NoMachine导入背包与首次联调

本包的软件接收端、原六轴标定映射、共享UART调度和电脑联控已实现。2026-11-17已在用户Windows与NoMachine背包现场部署，32文件校验、自检、电脑到背包预览OFF与真实UART OFF检查通过。用户尚未设置上肢基准，因此未使用基准确认参数、未启用运动。现场运行步骤见 [本机运行说明](RUN_THIS_ROBOT.md)；下面保留通用导入和首次联调流程。

## 导入到独立目录

1. 在Windows下载本次对话交付的 `dual_hand_backpack_v1_20261117.zip`，通过现有NoMachine会话的文件传输功能上传到背包桌面。若从GitHub获取源码，可先在电脑项目目录执行 `python scripts/build_packages.py`，生成在本地 `releases/` 的背包包与电脑包。GitHub只保存源码和日志，不保存本次生成的ZIP。原底盘和原六轴工程都保留。
2. 在NoMachine中的背包终端执行：

   ```sh
   cd "$HOME/Desktop"
   python3 -m zipfile -e dual_hand_backpack_v1_20261117.zip dual_hand_import_20261117
   cd dual_hand_import_20261117/aic_dual_hand_robot_20261117
   python3 install_backpack.py
   ```

   导入器逐项检查大小和SHA256，再复制到新目录 `~/Desktop/aic_dual_hand_robot_20261117`。目的目录已存在时拒绝覆盖；不会修改旧底盘目录、旧启动脚本或系统环境。

3. 进入新目录，执行仅软件检查：

   ```sh
   cd "$HOME/Desktop/aic_dual_hand_robot_20261117"
   python3 install_backpack.py --check
   python3 -S verify_backpack.py
   sh start_preview.sh
   ```

   `verify_backpack.py` 核对文件清单、离线依赖、18项自检，再用仅本机可达的临时端口执行5秒OFF链路检查并停止。结果在新目录 `logs/verification/import-check-*/`，包含summary、测试文本与接收端日志。它不占用8770、不停止已有接收端；回环预览检查不等于电脑到背包链路或实机运动验收。

   这些检查和PREVIEW服务不打开UART；离线包带已校验的官方pySerial3.5 wheel，不需要背包联网或pip。`scripts/check_connection.py` 也已包含在背包包内，可在需要时独立检查8770。

4. Windows解压电脑包 `dual_hand_pc_v1_20261117.zip`，双击新文件夹的 `run_robot.cmd`，连接背包IPv4、TCP8770。初始应显示“背包预览 · DISARMED”，没有运动。可用以下命令单独检查OFF链路：

   ```sh
   python scripts/check_connection.py 背包IPv4 --expect preview --seconds 5 --output logs/verification/backpack_preview_off.json
   ```

## 原机器人基准

与AIC原六轴工程保持一致，使用原动作编辑器确认。左右是机器人自身左右；编辑器屏幕左右与机器人左右相反。

| 关节 | 机器人左侧 | 机器人右侧 |
| --- | --- | --- |
| 肘伸直 | ID1 = 926 | ID31 = 3110 |
| 大臂向两侧展开 | ID3 = 2256 | ID33 = 2224 |
| 肩侧平举 | ID4 = 1569 | ID34 = 2040 |
| 固定槽位 | ID2 = 2048 | ID32 = 2048 |

本程序没有实际位置回读。开始前不能把上次日志或软件窗口当作当前姿态；也不会自动移动机器人寻找基准。若现场原工程标定与此表不同，应先核对并同步新项目配置。

## 切换硬件模式

1. 电脑保持暂停；在背包用Ctrl+C退出新PREVIEW服务。正常退出原底盘接收端或其他占UART的控制程序，原项目文件不删除、不改名。
2. 按上表设置基准，确认完成后启动新硬件服务：

   ```sh
   cd "$HOME/Desktop/aic_dual_hand_robot_20261117"
   sh start_robot.sh --confirm-arm-baseline
   ```

   打开UART时只发原底盘STOP/模式0/STOP，不发上肢位置或归位命令。即使底盘旧服务与新服务端口不同，二者也不能同时使用同一个UART；新服务不会绕过独占打开失败。

3. 电脑重新连接8770，先保持OFF。可运行 `check_connection.py --expect hardware` 保存硬件OFF检查；该检查仍不会使能运动。
4. 先选择“仅上肢”，只启用左肘一轴、0～20°弯曲范围。右手完成三步标定后点击启用，按提示匹配背包参考；首次默认通常五指展开，保持0.5秒。
5. 小范围确认食指控制左肘弯曲方向、回到起始目标、空格暂停、丢手暂停，再逐轴加入其余手指。拇指必须使左右大臂同时向内合拢/向两侧展开。
6. 最后再选“双手联合”，完成左手校准，验证原前后/旋转与上肢操作同时运行，并测试统一暂停和断线行为。重新启动接收端后仍需重新确认原基准。

## 日志回传和回退

背包日志在新目录 `logs/backpack/`。通过NoMachine下载这些新日志，与新电脑目录的 `logs/runs/` 一同保存到GitHub新项目路径；不要使用旧底盘目录的日志同步脚本混入新项目。

回退时暂停电脑，Ctrl+C正常停止新接收端，确认UART已释放后，按原方法启动旧 `aic_left_hand_chassis`。旧底盘配置、速度、代码、日志及原main分支均保留。
