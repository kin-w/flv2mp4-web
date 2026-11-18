#!/usr/bin/env python3
"""打包群晖 SPK：把 package/ 打成 package.tgz，再与 INFO、图标组成 .spk。

用法：python build.py
产物：dist/flv2mp4-web_<version>_x86_64.spk
"""
import io
import os
import tarfile

BASE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(BASE, "package")
DIST = os.path.join(BASE, "dist")


def read_info_version():
    with open(os.path.join(BASE, "INFO"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("version="):
                return line.split("=", 1)[1].strip().strip('"')
    return "1.0.0"


def add_tree(tar, src, arc_root=""):
    """按原始权限写入；脚本统一 0755，避免 Windows 打包丢失执行位。"""
    for cur, dirs, files in os.walk(src):
        dirs.sort()
        rel_dir = os.path.relpath(cur, src)
        if rel_dir != ".":
            info = tarfile.TarInfo(os.path.join(arc_root, rel_dir).replace("\\", "/"))
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.mtime = 0
            tar.addfile(info)
        for name in sorted(files):
            full = os.path.join(cur, name)
            rel = os.path.relpath(full, src).replace("\\", "/")
            arc = os.path.join(arc_root, rel).replace("\\", "/")
            with open(full, "rb") as f:
                data = f.read()
            info = tarfile.TarInfo(arc)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o755 if "/scripts/" in "/" + arc or name == "privilege" else 0o644
            tar.addfile(info, io.BytesIO(data))


def build():
    os.makedirs(DIST, exist_ok=True)
    version = read_info_version()
    pkg_tgz = os.path.join(DIST, "package.tgz")
    with tarfile.open(pkg_tgz, "w:gz") as tar:
        add_tree(tar, PKG)

    spk = os.path.join(DIST, f"flv2mp4-web_{version}_x86_64.spk")
    with tarfile.open(spk, "w") as tar:
        for name in ("INFO", "PACKAGE_ICON.PNG", "PACKAGE_ICON_256.PNG"):
            with open(os.path.join(BASE, name), "rb") as f:
                data = f.read()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("package.tgz")
        info.size = os.path.getsize(pkg_tgz)
        info.mtime = 0
        info.mode = 0o644
        with open(pkg_tgz, "rb") as f:
            tar.addfile(info, f)
    os.remove(pkg_tgz)
    print(spk)


if __name__ == "__main__":
    build()
