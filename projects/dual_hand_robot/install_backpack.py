"""Verify a transferred package and import into a NEW backpack directory."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil

ROOT = Path(__file__).resolve().parent


def verify(source):
    manifest = json.loads((source/"package_manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = PurePosixPath(entry["path"])
        if path.is_absolute() or ".." in path.parts or "\\" in entry["path"]:
            raise ValueError("Unsafe package path")
        file = source.joinpath(*path.parts)
        if file.is_symlink() or not file.is_file():
            raise ValueError("Missing file or link: "+entry["path"])
        data = file.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Transferred file does not match manifest: "+entry["path"])
    return manifest


def install(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    manifest = verify(source)
    if source == destination:
        return "Already at the requested NEW location; verified without overwriting"
    if destination.exists():
        raise ValueError("Destination exists; refusing to overwrite: "+str(destination))
    destination.mkdir(parents=True)
    for entry in manifest["files"]:
        target = destination/entry["path"]
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source/entry["path"],target)
    shutil.copy2(source/"package_manifest.json",destination/"package_manifest.json")
    verify(destination)
    return "Imported and verified NEW directory: "+str(destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify NoMachine transfer; never overwrite old chassis project or open UART")
    parser.add_argument("--dest",type=Path,default=Path.home()/"Desktop/aic_dual_hand_robot_20261117")
    parser.add_argument("--check",action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.check:
            print("Verified transferred files:",len(verify(ROOT)["files"]),"UART NOT OPENED")
        else:
            print(install(ROOT,args.dest))
            print("No service started; UART NOT OPENED")
        return 0
    except (OSError,ValueError,KeyError) as error:
        print("Import stopped:",error)
        return 1


if __name__ == "__main__":raise SystemExit(main())
