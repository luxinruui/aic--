"""Produce isolated PC/backpack archives and byte-verified manifests."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def inventory(files):
    return [dict(path=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()) for name,data in sorted(files.items())]


def package(name,folder,files):
    manifest = json.dumps(dict(app="aic-dual-hand-robot",version="1.0.0",files=inventory(files)),
                          ensure_ascii=False,indent=2).encode()
    payload = dict(files,**{"package_manifest.json":manifest})
    output = ROOT/"releases"/name
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
        for path,data in sorted(payload.items()):
            archive.writestr(folder+"/"+path,data)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.infolist()) == len(payload)
        for path,data in payload.items():
            assert archive.read(folder+"/"+path) == data
    return dict(path=output.relative_to(ROOT).as_posix(),bytes=output.stat().st_size,
                sha256=hashlib.sha256(output.read_bytes()).hexdigest(),files=len(payload),crc_readback=True)


def main():
    files = {}
    folders = ("left_hand_drive","robot_link","dual_link","tests","dual_tests","offline_deps","scripts","docs")
    for folder in folders:
        for path in sorted((ROOT/folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
                if path.suffix == ".py":
                    version = (3,8) if folder in ("robot_link","dual_link","dual_tests") else (3,10)
                    ast.parse(path.read_text(encoding="utf-8"),feature_version=version)
    for name in ("README.md","README_NOMACHINE.md","RUN_THIS_ROBOT.md","arm_config.json","robot_config.json","requirements.txt",
                 "requirements_backpack.txt","run_pc.cmd","run_dual_hand.cmd","run_robot.cmd","run_backpack.py",
                 "install_backpack.py","verify_backpack.py","initialize_arms.py","start_preview.sh","start_robot.sh"):
        files[name] = (ROOT/name).read_bytes()
    backpack = {name:data for name,data in files.items() if name.split("/")[0] in
        ("robot_link","dual_link","dual_tests","offline_deps","docs") or name in
        ("README.md","README_NOMACHINE.md","RUN_THIS_ROBOT.md","arm_config.json","robot_config.json","requirements_backpack.txt",
         "run_backpack.py","install_backpack.py","verify_backpack.py","initialize_arms.py","start_preview.sh","start_robot.sh",
         "scripts/check_connection.py")}
    for name in ("run_backpack.py","install_backpack.py","verify_backpack.py","initialize_arms.py","scripts/check_connection.py"):
        ast.parse((ROOT/name).read_text(encoding="utf-8"),feature_version=(3,8))
    results = dict(pc=package("dual_hand_pc_v1_20261117.zip","dual_hand_robot_pc",files),
        backpack=package("dual_hand_backpack_v1_20261117.zip","aic_dual_hand_robot_20261117",backpack))
    (ROOT/"release_manifest.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
    print(json.dumps(results,indent=2))


if __name__ == "__main__":main()
