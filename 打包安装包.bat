@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   K线形态选股器 - 打包安装包
echo ============================================
".venv\Scripts\python.exe" build_installer.py
if errorlevel 1 (
  echo.
  echo 打包失败，请查看上方错误信息。
  pause
  exit /b 1
)
echo.
echo 安装包已生成：dist\KLinePicker_Setup_v1.0.0.exe
pause
