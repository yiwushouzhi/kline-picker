"""K线形态选股器 安装程序（自解压安装包，tkinter 界面）。

打包命令见 打包安装包.bat。
支持静默安装：KLinePicker_Setup.exe --silent [--dir=路径] [--desktop] [--no-shortcuts]
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox, ttk

APP_NAME = "K线形态选股器"
APP_EXE = "KLinePicker.exe"
APP_VER = "1.3.5"
REG_UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\KLinePicker"
CREATE_NO_WINDOW = 0x08000000


def payload_path() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "app_payload.zip")


def default_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", "KLinePicker")


def start_menu_lnk() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs", APP_NAME + ".lnk")


def desktop_lnk() -> str:
    return os.path.join(os.path.expanduser("~"), "Desktop", APP_NAME + ".lnk")


def _shortcut(lnk: str, target: str, workdir: str) -> None:
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{0}');"
        "$s.TargetPath='{1}';$s.WorkingDirectory='{2}';"
        "$s.IconLocation='{1},0';$s.Save()"
    ).format(lnk, target, workdir)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                    "-WindowStyle", "Hidden", "-Command", script],
                   creationflags=CREATE_NO_WINDOW, check=False)


def _write_uninstaller(target: str) -> None:
    cmd = "\r\n".join([
        "@echo off",
        'del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\'
        + APP_NAME + '.lnk" 2>nul',
        'del "%USERPROFILE%\\Desktop\\' + APP_NAME + '.lnk" 2>nul',
        'reg delete "HKCU\\' + REG_UNINSTALL + '" /f >nul 2>nul',
        'start "" /min cmd /c "timeout /t 2 >nul & rd /s /q ""%~dp0"""',
        "",
    ])
    path = os.path.join(target, "卸载.bat")
    with open(path, "w", encoding="gbk", errors="replace") as f:
        f.write(cmd)


def _register_uninstall(exe: str, target: str) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_UNINSTALL) as key:
        for name, val in (("DisplayName", APP_NAME),
                          ("DisplayVersion", APP_VER),
                          ("Publisher", "KLinePicker"),
                          ("InstallLocation", target),
                          ("DisplayIcon", exe),
                          ("UninstallString",
                           os.path.join(target, "卸载.bat"))):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def do_install(target: str, desktop: bool, shortcuts: bool = True,
               progress=None) -> str:
    zip_path = payload_path()
    if not os.path.exists(zip_path):
        raise RuntimeError("安装包数据缺失（app_payload.zip 未找到）")
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for i, name in enumerate(names):
            zf.extract(name, target)
            if progress and i % 50 == 0:
                progress(i / max(len(names), 1))
    exe = os.path.join(target, APP_EXE)
    if not os.path.exists(exe):
        raise RuntimeError("解压完成但未找到主程序：" + exe)
    if shortcuts:
        _shortcut(start_menu_lnk(), exe, target)
        if desktop:
            _shortcut(desktop_lnk(), exe, target)
    _write_uninstaller(target)
    _register_uninstall(exe, target)
    if progress:
        progress(1.0)
    return exe


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} 安装程序")
        self.resizable(False, False)
        self.configure(bg="#f2f4f8")
        self._exe = ""
        self._build()

    def _build(self):
        pad = {"padx": 18}
        tk.Label(self, text=APP_NAME, font=("Microsoft YaHei UI", 17, "bold"),
                 bg="#f2f4f8", fg="#1f2329").pack(anchor="w", pady=(18, 2),
                                                  **pad)
        tk.Label(self, text=f"版本 {APP_VER}    K线形态选股 / 分时查看工具",
                 font=("Microsoft YaHei UI", 9), bg="#f2f4f8",
                 fg="#6b7280").pack(anchor="w", **pad)

        box = tk.Frame(self, bg="#ffffff", highlightbackground="#d8dde5",
                       highlightthickness=1)
        box.pack(fill="x", pady=14, **pad)

        tk.Label(box, text="安装位置", bg="#ffffff", fg="#374151",
                 font=("Microsoft YaHei UI", 9)).grid(row=0, column=0,
                                                      padx=12, pady=(12, 4),
                                                      sticky="w")
        self.var_dir = tk.StringVar(value=default_dir())
        tk.Entry(box, textvariable=self.var_dir, width=48,
                 font=("Microsoft YaHei UI", 9)).grid(row=1, column=0,
                                                      padx=12, pady=(0, 12),
                                                      sticky="we")
        tk.Button(box, text="浏览…", command=self._browse,
                  width=7).grid(row=1, column=1, padx=(0, 12), pady=(0, 12))

        self.var_desktop = tk.BooleanVar(value=True)
        tk.Checkbutton(box, text="创建桌面快捷方式",
                       variable=self.var_desktop, bg="#ffffff").grid(
            row=2, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="w")
        box.columnconfigure(0, weight=1)

        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.bar.pack(fill="x", **pad)
        self.lbl = tk.Label(self, text="点击「开始安装」将程序安装到本机",
                            bg="#f2f4f8", fg="#6b7280",
                            font=("Microsoft YaHei UI", 9))
        self.lbl.pack(anchor="w", pady=(6, 0), **pad)

        row = tk.Frame(self, bg="#f2f4f8")
        row.pack(fill="x", padx=18, pady=(12, 16))
        self.btn_install = tk.Button(row, text="开始安装", width=12,
                                     command=self._start,
                                     bg="#e5484d", fg="#ffffff",
                                     activebackground="#c93c41",
                                     activeforeground="#ffffff",
                                     relief="flat", cursor="hand2",
                                     font=("Microsoft YaHei UI", 9, "bold"))
        self.btn_install.pack(side="right")
        self.btn_cancel = tk.Button(row, text="取消", width=8,
                                    command=self.destroy, relief="flat")
        self.btn_cancel.pack(side="right", padx=(0, 10))

    def _browse(self):
        path = filedialog.askdirectory(title="选择安装位置",
                                       initialdir=self.var_dir.get())
        if path:
            self.var_dir.set(os.path.normpath(path))

    def _start(self):
        target = self.var_dir.get().strip()
        if not target:
            messagebox.showwarning("提示", "请填写安装位置")
            return
        self.btn_install.config(state="disabled")
        self.btn_cancel.config(state="disabled")
        self.lbl.config(text="正在安装…")

        def prog(v):
            self.bar["value"] = max(0, min(100, v * 100))
            self.update_idletasks()

        def work():
            try:
                exe = do_install(target, self.var_desktop.get(), True, prog)
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: self._fail(str(e)))
                return
            self.after(0, lambda: self._done(exe))

        threading.Thread(target=work, daemon=True).start()

    def _fail(self, msg):
        self.lbl.config(text="安装失败")
        self.btn_install.config(state="normal")
        self.btn_cancel.config(state="normal")
        messagebox.showerror("安装失败", msg)

    def _done(self, exe):
        self._exe = exe
        self.bar["value"] = 100
        self.lbl.config(text="安装完成，可立即启动")
        self.btn_install.config(text="立即启动", state="normal",
                                command=self._launch)
        self.btn_cancel.config(text="关闭", state="normal")
        if messagebox.askyesno("安装完成",
                               f"{APP_NAME} 已安装完成。\n\n是否立即启动？"):
            self._launch()

    def _launch(self):
        if self._exe and os.path.exists(self._exe):
            subprocess.Popen([self._exe], cwd=os.path.dirname(self._exe))
        self.destroy()


def main():
    args = sys.argv[1:]
    if "--silent" in args:
        target = default_dir()
        for a in args:
            if a.startswith("--dir="):
                target = a.split("=", 1)[1]
        do_install(target, "--desktop" in args,
                   "--no-shortcuts" not in args)
        return
    app = Installer()
    app.eval("tk::PlaceWindow . center")
    app.mainloop()


if __name__ == "__main__":
    main()
