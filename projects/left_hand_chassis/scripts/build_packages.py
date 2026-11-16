"""Create the full PC release and minimal NoMachine backpack ZIP; verify bytes."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

PROJECT=Path(__file__).resolve().parent.parent
OUT=PROJECT/"deliverables"


def source_files():
    result=[]
    for p in PROJECT.rglob("*"):
        if not p.is_file(): continue
        r=p.relative_to(PROJECT)
        if any(x in ("runtime","deliverables","__pycache__",".venv",".git") or x.startswith(".chassis-venv") for x in r.parts): continue
        if p.name.startswith(".local-") or p.name in ("release_manifest.json","backpack_manifest.json","mlk_manifest.json") or p.suffix in (".pyc",".tmp",".zip"): continue
        # A running camera session is still being written. Export it after close.
        if r.parts[:2] == ("logs", "runs") and len(r.parts) > 3:
            if not (PROJECT / "logs" / "runs" / r.parts[2] / "run.json").is_file(): continue
        result.append(p)
    return sorted(result)


def info(p):
    data=p.read_bytes()
    return dict(path=p.relative_to(PROJECT).as_posix(),bytes=len(data),sha256=hashlib.sha256(data).hexdigest())


def package(name,folder,files):
    destination=OUT/name
    with zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p,folder+"/"+p.relative_to(PROJECT).as_posix())
    with zipfile.ZipFile(destination) as z:
        assert z.testzip() is None
        assert len(z.infolist())==len(files)
        for p in files:
            assert z.read(folder+"/"+p.relative_to(PROJECT).as_posix())==p.read_bytes()
    return dict(zip=destination.name,bytes=destination.stat().st_size,files=len(files),
                sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),crc_and_readback=True)


def main():
    OUT.mkdir(exist_ok=True)
    for p in PROJECT.glob("*.cmd"):
        p.write_bytes(p.read_text(encoding="ascii").replace("\r\n","\n").replace("\n","\r\n").encode("ascii"))
    for p in PROJECT.glob("*.sh"):
        p.write_bytes(p.read_text(encoding="utf-8").replace("\r\n","\n").encode("utf-8"))
    files=source_files()
    python=[]
    for p in files:
        if p.suffix==".py":
            r=p.relative_to(PROJECT)
            version=(3,8) if r.parts[0] in ("robot_link","robot_tests","mlk_relay","board_tests") or p.name in ("run_backpack.py","run_mlk_relay.py","board_input_probe.py") else (3,10)
            ast.parse(p.read_text(encoding="utf-8"),feature_version=version)
            python.append(r.as_posix())
    direct={"run_backpack.py","robot_config.json","requirements_backpack.txt","README_BACKPACK.md",
            "install_backpack_offline.sh","start_backpack_preview.sh","start_backpack_robot.sh"}
    backpack=[p for p in files if p.relative_to(PROJECT).parts[0] in ("robot_link","robot_tests","offline_deps")
              or p.relative_to(PROJECT).as_posix() in direct
              or p.relative_to(PROJECT).as_posix() in ("docs/ROBOT_PROTOCOL.md", "logs/development/2026-11-16-robot-link.md",
                  "logs/development/2026-11-16-deployment.md",
                  "logs/verification/robot_link_summary.json","logs/verification/robot_link_tests_20261116.txt")]
    # A package-specific inventory avoids suggesting that omitted PC files exist.
    inventory=PROJECT/"backpack_manifest.json"
    inventory.write_bytes(json.dumps(dict(version="1.2.0",files=[info(p) for p in backpack]),
                                    ensure_ascii=False,indent=2).encode("utf-8"))
    backpack=backpack+[inventory]
    files.append(inventory)
    board_names={"run_mlk_relay.py","board_input_probe.py","robot_config.json","README_MLK.md","README_BACKPACK.md",
                 "robot_link/__init__.py","robot_link/client.py","robot_link/config.py","robot_link/control.py",
                 "robot_link/protocol.py","robot_link/server.py","docs/ROBOT_PROTOCOL.md",
                 "logs/development/2026-11-16-mlk-relay.md","logs/verification/mlk_relay_summary.json",
                 "logs/verification/mlk_relay_tests_20261116.txt"}
    board=[p for p in files if p.relative_to(PROJECT).parts[0] in ("mlk_relay","board_tests")
           or p.relative_to(PROJECT).as_posix() in board_names]
    board_manifest=PROJECT/"mlk_manifest.json"
    board_manifest.write_bytes(json.dumps(dict(version="1.2.0",files=[info(p) for p in board]),ensure_ascii=False,indent=2).encode("utf-8"))
    board.append(board_manifest)
    files.append(board_manifest)
    m=PROJECT/"release_manifest.json"
    m.write_bytes(json.dumps(dict(version="1.2.0",date="2026-11-16",python_syntax_files=len(python),
        real_hardware_tested=False,files=[info(p) for p in files]),ensure_ascii=False,indent=2).encode("utf-8"))
    files.append(m)
    result=dict(pc=package("left_hand_chassis_robot_v1_2_20261116.zip","left_hand_chassis",files),
                backpack=package("left_hand_chassis_backpack_v1_2_20261116.zip","aic_left_hand_chassis",backpack),
                mlk=package("left_hand_chassis_mlk_v1_2_20261116.zip","aic_mlk_relay",board),
                python_syntax_files=len(python))
    print(json.dumps(result,indent=2))


if __name__=="__main__": main()
