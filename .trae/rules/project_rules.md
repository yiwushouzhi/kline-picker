# 项目规则（Trae 自动加载）

## 开工前

先读 `PROJECT_CONTEXT.md`，其中记录了项目定位、文件职责、数据存储、已实现功能和版本发布规则。

## 项目

A 股 K 线形态选股器，Python 3.13 + PySide6 桌面程序，最终打包为 Windows 单机安装包。云端只负责改代码和自动构建，成品离线运行。

## 关键约定

- 界面文案用中文，简洁直白，不加 emoji。
- 自绘 K 线坐标统一用 `(bar_index, price)` 存储，便于缩放和拖动。
- 用户个人数据（`favorites.json`、`patterns.json`、`drawings/`、`cache/`、API Key）不进仓库。
- 版本号需同时改 `build_installer.py` 的 `SETUP_NAME` 与 `installer_app.py` 的 `APP_VER`。
- 改动后至少执行：`python -m compileall -q -x "(build|dist|\.venv)" .`

## 环境

- 云端是 Linux 容器，界面自检用离屏模式：`QT_QPA_PLATFORM=offscreen`
- 安装包由 `.github/workflows/windows-build.yml` 在 Windows 构建机生成，推 `v*` 标签自动发 Release
