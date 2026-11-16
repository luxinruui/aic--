"""Optional MLK Linux PC->relay->backpack control gate. Default locally stopped."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import queue
import signal
import sys
import threading
import time

from robot_link.client import Client
from robot_link.config import load
from robot_link.server import Server
from mlk_relay.core import RelayController

ROOT=Path(__file__).resolve().parent


class Journal:
    def __init__(self):
        folder=ROOT/"logs/mlk"
        folder.mkdir(parents=True,exist_ok=True)
        self.file=(folder/(datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".jsonl")).open("w",encoding="utf-8")
        self.lock=threading.Lock()

    def __call__(self,item):
        text=json.dumps(dict(at=datetime.now().astimezone().isoformat(),**item),ensure_ascii=False)
        with self.lock:
            self.file.write(text+"\n")
            self.file.flush()
            print(text,flush=True)

    def close(self): self.file.close()


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8",errors="backslashreplace")
    p=argparse.ArgumentParser(description="MLK local enable/stop relay; no camera, model or UART on this board")
    p.add_argument("--backpack",help="backpack IPv4, required for relay operation")
    p.add_argument("--backpack-port",type=int,default=8768)
    p.add_argument("--bind",default="0.0.0.0")
    p.add_argument("--port",type=int,default=8769,help="PC connects to the MLK board on this port")
    p.add_argument("--config",type=Path,default=ROOT/"robot_config.json")
    p.add_argument("--input-device",help="optional VERIFIED /dev/input/eventN; never guessed")
    p.add_argument("--arm-keycode",type=int)
    p.add_argument("--stop-keycode",type=int)
    p.add_argument("--check-runtime",action="store_true")
    p.add_argument("--self-test",action="store_true",help="local TCP and simulated UART only")
    a=p.parse_args(argv)
    if a.check_runtime:
        print(json.dumps(dict(python=platform.python_version(),system=platform.system(),machine=platform.machine(),
                              python38_or_newer=sys.version_info>=(3,8),camera_or_uart_required=False,
                              input_devices_file=Path('/proc/bus/input/devices').is_file()),indent=2))
        return 0 if sys.version_info>=(3,8) else 2
    if a.self_test:
        import unittest
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT/"board_tests")))
        return 0 if result.wasSuccessful() else 1
    if not a.backpack:
        p.error("--backpack IPv4 is required")
    if not 1<=a.port<=65535 or not 1<=a.backpack_port<=65535:
        p.error("Invalid TCP port")
    input_args=(a.input_device,a.arm_keycode,a.stop_keycode)
    if any(x is not None for x in input_args) and any(x is None for x in input_args):
        p.error("Input device and both measured keycodes must be supplied together")
    commands=queue.SimpleQueue()
    client=relay=server=journal=inputs=None
    worker=None
    io_fault=False
    try:
        config=load(a.config)
        journal=Journal()
        client=Client(a.backpack,config,a.backpack_port)
        client.start()
        relay=RelayController(config,client)
        server=Server(relay,config,a.bind,a.port,journal)
        worker=threading.Thread(target=server.serve,name="mlk-upstream-server",daemon=True)
        worker.start()
        if a.input_device:
            from mlk_relay.inputs import InputWorker
            inputs=InputWorker(a.input_device,a.arm_keycode,a.stop_keycode,commands.put)
            inputs.start()
        if os.name=="posix":
            signal.signal(signal.SIGUSR1,lambda *_:commands.put("arm"))
            signal.signal(signal.SIGUSR2,lambda *_:commands.put("stop"))
        signal.signal(signal.SIGTERM,lambda *_:commands.put("quit"))

        def console():
            for line in sys.stdin:
                action={"a":"arm","s":"stop","r":"reconnect","q":"quit"}.get(line.strip().lower())
                if action: commands.put(action)
        threading.Thread(target=console,name="mlk-console",daemon=True).start()
        journal(dict(event="local_stopped",pid=os.getpid(),listen_port=a.port,
                     instructions="a+Enter: enable MLK; s+Enter: stop; r: reconnect backpack; q: quit"))
        while worker.is_alive():
            try: action=commands.get(timeout=.1)
            except queue.Empty: continue
            if action=="quit": break
            if action.startswith("io_fault:"):
                io_fault=True
                relay.stop_local("按键输入失效，需修复并重启："+action[9:])
            elif action=="arm":
                if io_fault: journal(dict(event="arm_refused",reason="input device fault"))
                else: journal(dict(event="local_arm",accepted=relay.arm_local()))
            elif action=="stop": relay.stop_local()
            elif action=="reconnect":
                relay.stop_local("下游重新连接，等待本地和电脑再次启用")
                client.close()
                client=Client(a.backpack,config,a.backpack_port)
                relay.downstream=client
                client.start()
            server.report()
        if server.error: raise RuntimeError(server.error)
    except KeyboardInterrupt:
        pass
    except (ValueError,OSError,RuntimeError) as error:
        print("Cannot start MLK relay: "+str(error),file=sys.stderr,flush=True)
        return 1
    finally:
        if relay: relay.stop_local("米联客程序退出")
        if inputs: inputs.stop(); inputs.join(1)
        if server: server.stop()
        if worker: worker.join(2)
        if relay: relay.close()
        elif client: client.close()
        if journal: journal.close()
    return 0


if __name__=="__main__": raise SystemExit(main())
