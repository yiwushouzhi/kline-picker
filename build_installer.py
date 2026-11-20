"""构建 Windows 安装包（PyInstaller）。

流程：
  1) 打包主程序 → dist/KLinePicker/（onedir，启动快）
  2) 将主程序目录压成 build/app_payload.zip
  3) 打包安装器 → dist/KLinePicker_Setup_v1.3.5.exe（单文件）

用法：双击 打包安装包.bat，或 python build_installer.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
APP_DIR = os.path.join(HERE, "dist", "KLinePicker")
PAYLOAD = os.path.join(HERE, "build", "app_payload.zip")
SETUP_NAME = "KLinePicker_Setup_v1.3.5"


def run(args: list[str]) -> None:
    print(">>", " ".join(args))
    subprocess.run(args, cwd=HERE, check=True)


def build_app() -> None:
    run([PY, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--noconsole", "--name", "KLinePicker", "--onedir",
         "--icon", "app.ico", "--add-data", "app.ico;.",
         "--exclude-module", "tkinter", "--exclude-module", "matplotlib",
         "--exclude-module", "pytest", "main.py"])


def zip_payload() -> None:
    os.makedirs(os.path.dirname(PAYLOAD), exist_ok=True)
    if os.path.exists(PAYLOAD):
        os.remove(PAYLOAD)
    count = 0
    with zipfile.ZipFile(PAYLOAD, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, _dirs, files in os.walk(APP_DIR):
            for name in files:
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, APP_DIR))
                count += 1
    size = os.path.getsize(PAYLOAD) / 1024 / 1024
    print(f"载荷 {count} 个文件, {size:.1f} MB -> {PAYLOAD}")


def build_installer() -> None:
    run([PY, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--onefile", "--noconsole", "--name", SETUP_NAME,
         "--icon", "app.ico",
         "--add-data", f"{PAYLOAD};.", "installer_app.py"])
    exe = os.path.join(HERE, "dist", SETUP_NAME + ".exe")
    if not os.path.exists(exe):
        raise SystemExit("安装器打包失败：未生成 " + exe)
    print(f"完成: {exe}  ({os.path.getsize(exe) / 1024 / 1024:.1f} MB)")


def main() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("未安装 PyInstaller：先执行 "
                         "python -m pip install -r requirements.txt")
    build_app()
    zip_payload()
    build_installer()


if __name__ == "__main__":
    main()
