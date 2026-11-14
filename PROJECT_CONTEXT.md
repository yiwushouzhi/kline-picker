# 项目上下文（给 AI 协作用）

> 本文件用于把本地开发过程中积累的上下文带到云端开发环境。
> 在新的 Trae 对话里用 `@PROJECT_CONTEXT.md` 引用本文件即可快速恢复背景。

## 项目简介

A 股 K 线形态选股器，Python + PySide6 桌面程序，最终打包为 Windows 单机安装包。
云端只负责源码、开发环境和自动构建，成品软件离线运行。

## 技术栈

- Python 3.13，PySide6 6.12，numpy / pandas / requests，PyInstaller 打包
- 依赖版本固定见 `requirements.txt`
- 行情数据来自东方财富公开接口，DeepSeek 等 AI 通过 OpenAI 兼容协议调用

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `main.py` | 主窗口：筛选面板、结果表、收藏夹、条件管理、AI 交互、数据导入导出 |
| `chart.py` | 自绘 K 线组件：日K/分钟K/分时、均线、叠加指数与板块、画线标注、盈亏比区 |
| `data.py` | 行情获取与本地缓存（东财/腾讯/新浪），板块列表与板块 K 线 |
| `screener.py` | 后台筛选线程，逐只股票拉取并匹配条件 |
| `dsl.py` | 形态条件 DSL 编译与匹配 |
| `deepseek_client.py` | AI 客户端，支持任意 OpenAI 兼容接口 |
| `theme.py` | 主题、强调色、密度与 QSS 生成 |
| `build_installer.py` | 打包脚本，生成单文件安装包 |
| `installer_app.py` | 安装程序界面与注册表卸载项 |

## 运行与打包

```bash
pip install -r requirements.txt
python main.py                 # 本地运行
python build_installer.py      # 生成 dist/KLinePicker_Setup_vX.Y.Z.exe
```

云端（Linux）无法显示桌面界面，界面自检请用离屏模式：

```bash
QT_QPA_PLATFORM=offscreen python -c "from PySide6.QtWidgets import QApplication; from main import MainWindow; app=QApplication([]); w=MainWindow(); w.show(); app.processEvents(); w.close()"
```

Windows 安装包由 GitHub Actions（`.github/workflows/windows-build.yml`）在 `windows-latest` 上构建。

## 数据存储

- 运行期数据在 `data.app_data_dir()`：源码运行时为项目目录，打包后为 `%LOCALAPPDATA%\KLinePicker`
- `favorites.json` 收藏夹、`patterns.json` 已存条件、`drawings/<code>.json` 画线标注、`cache/` 行情缓存
- 这些都是用户个人数据，**不提交到仓库**（见 `.gitignore`）
- 「数据」菜单支持整体导出/导入 JSON

## 已实现功能

1. 自然语言描述形态 → AI 梳理条件 → 生成筛选公式 → 保存
2. 条件成套保存：形态描述 + AI 梳理结果 + 公式一起存，「已存条件」下拉选择后三个框同时回填
3. AI 设置支持 DeepSeek / OpenAI / 通义千问 / 智谱 GLM / Kimi / 自定义接口与模型
4. 筛选结果双击查看 K 线，支持日K、1/5 分钟、分时，可叠加指数与板块日K
5. K 线画线：直线、水平线、射线、百分位线、黄金分割线、文字标注
6. 盈亏比区：止损→止盈→拖动建仓区，三区不同配色，显示价位与盈亏比，可拖动修改
7. 收藏夹与筛选条件关联，收藏直接进入关联夹；多选结果按 Insert 批量收藏
8. 数据导出/导入、主题与均线颜色自定义

## 版本与发布

- 版本号需同时修改 `build_installer.py` 的 `SETUP_NAME` 和 `installer_app.py` 的 `APP_VER`
- 当前版本 1.3.5
- 推送 `v*` 标签会触发 Release 并上传安装包

## 协作约定

- 界面文案用中文，简洁直白
- 修改后至少通过语法检查与主窗口离屏启动检查
- 不要提交个人数据、API Key、构建产物
- 自绘 K 线坐标统一用 `(bar_index, price)` 存储，便于缩放和拖动
