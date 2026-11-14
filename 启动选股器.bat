@echo off
rem 这个 .bat 只是转交给无窗口的 vbs 启动器（.bat 自身仍会闪现一次 cmd）
start "" wscript "%~dp0启动选股器.vbs"
exit
