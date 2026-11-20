"""K线形态选股器 主界面。

用法：
  1. 用自然语言描述形态，点「① 条件梳理（AI）」；或直接在下方编辑条件 JSON。
  2. 选择日期范围和股票池，点「开始筛选」。
  3. 双击结果行，或用 ↑/↓/PgUp/PgDn 切换个股；右侧显示截至命中日的K线。
  4. 滚轮缩放、左键拖动平移；空格=揭示下一日，Shift+空格=回退一日。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

from PySide6.QtCore import (QBuffer, QByteArray, QDate, QEvent, QIODevice,
                            QObject, QPointF, QSettings, Qt, QThread,
                            QTimer, Signal)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QFont, QIcon,
                           QKeyEvent, QKeySequence, QPainter, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog,
                               QComboBox, QDateEdit, QDialog,
                               QDialogButtonBox, QFileDialog, QFontComboBox,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QDoubleSpinBox, QProgressBar, QPushButton,
                               QScrollArea, QSpinBox, QSplitter,
                               QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import data
from chart import DRAW_COLORS, KLineChart
from deepseek_client import (AI_PRESETS, API_URL, DEFAULT_MODEL, fix_pattern,
                             generate_pattern, interpret_description,
                             normalize_url, sanitize_key)
from dsl import CompiledPattern, PatternError
from screener import ScreenerThread
from theme import (ACCENTS, DEFAULT_ACCENT, DEFAULT_DENSITY, DEFAULT_THEME,
                   DENSITY, THEMES, build_qss)

PATTERNS_FILE = os.path.join(data.app_data_dir(), "patterns.json")
FAVORITES_FILE = os.path.join(data.app_data_dir(), "favorites.json")
NOTES_FILE = os.path.join(data.app_data_dir(), "notes.json")


def _load_notes_file() -> dict:
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return {str(k): str(v) for k, v in d.items()
                if str(k).strip() and isinstance(v, str)}
    except Exception:  # noqa: BLE001
        return {}


def _write_notes_file(notes: dict):
    try:
        with open(NOTES_FILE, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass

# 可叠加的指数（代码, 名称, 东财 secid）
INDEX_LIST = [
    ("000001", "上证指数", "1.000001"),
    ("399001", "深证成指", "0.399001"),
    ("399006", "创业板指", "0.399006"),
    ("000300", "沪深300", "1.000300"),
    ("000905", "中证500", "1.000905"),
    ("000688", "科创50", "1.000688"),
]

DEFAULT_FAV_FOLDER = "默认收藏夹"
PALETTE_VERSION = "2"   # 配色版本：升级时一次性套用新的柔和默认色


def _drawings_dir() -> str:
    d = os.path.join(data.app_data_dir(), "drawings")
    os.makedirs(d, exist_ok=True)
    return d


def _drawings_path(code: str) -> str:
    return os.path.join(_drawings_dir(), f"{code}.json")


def _load_drawings(code: str) -> list:
    try:
        with open(_drawings_path(code), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return []


def _save_drawings(code: str, drawings: list):
    try:
        with open(_drawings_path(code), "w", encoding="utf-8") as f:
            json.dump(drawings, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _read_fav_data():
    """返回 (folders, items)。兼容旧的纯列表格式。"""
    try:
        with open(FAVORITES_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:  # noqa: BLE001
        d = None
    if isinstance(d, list):  # 旧格式：只有条目
        items = [dict(x, folder=DEFAULT_FAV_FOLDER) for x in d
                 if isinstance(x, dict)]
        return [DEFAULT_FAV_FOLDER], items
    if isinstance(d, dict):
        folders = [str(x) for x in (d.get("folders") or [])]
        items = [x for x in (d.get("items") or []) if isinstance(x, dict)]
        for it in items:
            it.setdefault("folder", DEFAULT_FAV_FOLDER)
            if it["folder"] not in folders:
                folders.append(it["folder"])
        if not folders:
            folders = [DEFAULT_FAV_FOLDER]
        return folders, items
    return [DEFAULT_FAV_FOLDER], []


def _write_fav_data(folders: list, items: list):
    try:
        with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
            json.dump({"folders": folders, "items": items}, f,
                      ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


def _load_patterns_file() -> dict:
    try:
        with open(PATTERNS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_patterns_file(d: dict):
    try:
        with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


DEFAULT_DSL = {
    "name": "阳包阴",
    "rules": [
        {"offset": -1, "expr": "close < open"},
        {"offset": 0, "expr": "close > open and close >= ref(open,-1) and open <= ref(close,-1)"},
    ],
}


def _ensure_check_png() -> str:
    """生成白色对勾图标（16×16），供 QSS 的勾选框使用。"""
    path = os.path.join(data.app_data_dir(), "check16.png")
    if os.path.exists(path):
        return path.replace("\\", "/")
    try:
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor("#ffffff"))
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(3.6, 8.3), QPointF(6.8, 11.5),
                                  QPointF(12.4, 4.4)]))
        p.end()
        if not pm.save(path, "PNG"):
            return ""
    except Exception:  # noqa: BLE001
        return ""
    return path.replace("\\", "/")


class FoldGroup(QGroupBox):
    """点击标题即可折叠/展开正文的分组框（▶ 折叠 / ▼ 展开）。"""

    def __init__(self, title: str, settings=None, key: str = "", parent=None):
        super().__init__(title, parent)
        self._base = title
        self._folded = False
        self._restore = None    # 展开后恢复特殊控件的可见性
        self._on_fold = None    # 折叠状态变化回调（用于调整布局 stretch）
        self._settings, self._key = settings, key
        self.setToolTip("点击标题可折叠 / 展开此区域")

    def mousePressEvent(self, e):
        # 标题区（顶部窄条）点击切换折叠，正文区域的点击不受影响
        if e.button() == Qt.LeftButton and e.position().y() <= 26:
            self.set_folded(not self._folded)
        super().mousePressEvent(e)

    def set_folded(self, folded: bool):
        self._folded = bool(folded)
        self.setTitle(("▶ " if folded else "▼ ") + self._base)
        lay = self.layout()
        if lay is not None:
            for i in range(lay.count()):
                self._show_item(lay.itemAt(i), not folded)
        if not folded and self._restore:
            self._restore()
        if self._on_fold:
            self._on_fold(self._folded)
        if self._settings is not None and self._key:
            self._settings.setValue(self._key, 1 if folded else 0)

    @staticmethod
    def _show_item(item, on: bool):
        w = item.widget()
        if w is not None:
            w.setVisible(on)
        else:
            lay = item.layout()
            if lay is not None:
                for j in range(lay.count()):
                    FoldGroup._show_item(lay.itemAt(j), on)


class FavFolderDialog(QDialog):
    """收藏前选择目标收藏夹，可直接新建。"""

    def __init__(self, folders: list, default: str, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择收藏夹")
        self.setMinimumWidth(320)
        self._folders = list(folders)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(title))
        row = QHBoxLayout()
        self.cb = QComboBox()
        self.cb.addItems(self._folders)
        if default in self._folders:
            self.cb.setCurrentText(default)
        btn_new = QPushButton("新建…")
        btn_new.clicked.connect(self._new_folder)
        row.addWidget(self.cb, stretch=1)
        row.addWidget(btn_new)
        v.addLayout(row)
        self.ck_remember = QCheckBox("记住选择，下次不再询问")
        v.addWidget(self.ck_remember)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("收藏到此")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _new_folder(self):
        name, ok = QInputDialog.getText(self, "新建收藏夹", "收藏夹名称：")
        name = (name or "").strip()
        if not ok or not name:
            return
        if name not in self._folders:
            self._folders.append(name)
            self.cb.addItem(name)
        self.cb.setCurrentText(name)

    def folder(self) -> str:
        return self.cb.currentText()


class ColorSchemeDialog(QDialog):
    """K线配色：涨跌色、均线色、各类划线色、盈亏比区三区色。"""

    def __init__(self, up: str, down: str, ma_pairs: list,
                 draw_colors: dict, rr_colors: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("K线配色")
        self.setMinimumWidth(400)
        self._colors: dict = {"up": up, "down": down}
        self._buttons: dict = {}
        v = QVBoxLayout(self)
        tip = QLabel("点击右侧色块修改颜色。新画的线立即使用新颜色；"
                     "勾选下方选项可同步修改当前选中的标注。")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self._add_section(v, "涨跌颜色",
                          [("up", "上涨 K 线", up), ("down", "下跌 K 线", down)])
        self._add_section(v, "均线颜色",
                          [(f"ma:{p}", f"MA{p}", c) for p, c in ma_pairs])
        self._add_section(v, "划线颜色", [
            ("line", "直线", draw_colors.get("line", "#d99a5e")),
            ("hline", "水平线", draw_colors.get("hline", "#94c47a")),
            ("ray", "射线", draw_colors.get("ray", "#59b8c4")),
            ("percent", "百分位线", draw_colors.get("percent", "#c58fd6")),
            ("fib", "黄金分割线", draw_colors.get("fib", "#e6c07b")),
            ("rr", "盈亏比区外框", draw_colors.get("rr", "#aeb6c4")),
            ("text", "文字标注", draw_colors.get("text", DRAW_COLORS["text"])),
        ])
        self._add_section(v, "盈亏比区颜色", [
            ("profit_color", "盈利区域",
             rr_colors.get("profit_color", "#35a583")),
            ("loss_color", "亏损区域",
             rr_colors.get("loss_color", "#d1646d")),
            ("entry_color", "建仓区域",
             rr_colors.get("entry_color", "#d6a62e")),
        ])

        self.ck_apply = QCheckBox("同时应用到当前选中的标注")
        self.ck_apply.setChecked(True)
        v.addWidget(self.ck_apply)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("应用")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add_section(self, parent_layout, title: str,
                     items: list[tuple[str, str, str]]):
        box = QGroupBox(title)
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for row, (key, label, color) in enumerate(items):
            grid.addWidget(QLabel(label), row, 0)
            btn = QPushButton()
            btn.setFixedSize(64, 22)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, k=key: self._pick(k))
            grid.addWidget(btn, row, 1)
            grid.setColumnStretch(2, 1)
            self._colors[key] = color
            self._buttons[key] = btn
            self._update_button(key)
        parent_layout.addWidget(box)

    def _update_button(self, key: str):
        btn = self._buttons.get(key)
        color = self._colors.get(key, "#888888")
        if btn is not None:
            btn.setStyleSheet(
                f"background:{color};border:1px solid #6b7280;"
                f"border-radius:4px;")

    def _pick(self, key: str):
        color = QColorDialog.getColor(QColor(self._colors.get(key, "#888888")),
                                      self, "选择颜色")
        if color.isValid():
            self._colors[key] = color.name()
            self._update_button(key)

    def result_colors(self) -> dict:
        return dict(self._colors)

    def apply_selected(self) -> bool:
        return self.ck_apply.isChecked()


class MaConfigDialog(QDialog):
    """均线周期与颜色设置。"""

    DEFAULT_COLORS = ["#d09a3c", "#3f86c4", "#9254b5", "#2a8a96",
                      "#b5504f", "#4d8f4f", "#b5732a", "#55606e"]

    def __init__(self, periods: list, colors: list, width: float = 1.2,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("均线设置")
        self.setMinimumWidth(360)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("设置均线周期（日线），可增删多条；颜色可点击修改。"))
        row_w = QHBoxLayout()
        row_w.addWidget(QLabel("均线线宽"))
        self.sp_width = QDoubleSpinBox()
        self.sp_width.setRange(0.8, 6.0)
        self.sp_width.setSingleStep(0.5)
        self.sp_width.setDecimals(1)
        self.sp_width.setValue(float(width))
        row_w.addWidget(self.sp_width)
        row_w.addStretch(1)
        v.addLayout(row_w)
        self._rows: list = []   # (spin, color_btn, del_btn)
        self._grid = QGridLayout()
        self._grid.addWidget(QLabel("周期"), 0, 0)
        self._grid.addWidget(QLabel("颜色"), 0, 1)
        v.addLayout(self._grid)
        # 用现有配置填充，不足默认补一条
        items = list(zip(periods, colors)) if periods else [(5, self.DEFAULT_COLORS[0])]
        for p, c in items:
            self._add_row(p, c)
        if not items:
            self._add_row(5, self.DEFAULT_COLORS[0])
        row = QHBoxLayout()
        btn_add = QPushButton("＋ 新增均线")
        btn_add.clicked.connect(lambda: self._add_row(
            5, self.DEFAULT_COLORS[len(self._rows) % len(self.DEFAULT_COLORS)]))
        row.addWidget(btn_add)
        row.addStretch(1)
        v.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add_row(self, period: int, color: str):
        r = len(self._rows) + 1
        sp = QSpinBox()
        sp.setRange(1, 500)
        sp.setValue(int(period))
        cb = QPushButton(color)
        cb.setStyleSheet(f"background:{color};color:#fff;")
        cb.clicked.connect(lambda _=False, b=cb: self._pick_color(b))
        db = QPushButton("删除")
        db.clicked.connect(lambda _=False, b=db: self._del_row(b))
        self._grid.addWidget(sp, r, 0)
        self._grid.addWidget(cb, r, 1)
        self._grid.addWidget(db, r, 2)
        self._rows.append((sp, cb, db))

    def _pick_color(self, btn: QPushButton):
        c = QColorDialog.getColor(QColor(btn.text()), self, "均线颜色")
        if c.isValid():
            btn.setText(c.name())
            btn.setStyleSheet(f"background:{c.name()};color:#fff;")

    def _del_row(self, btn: QPushButton):
        for i, (sp, cb, db) in enumerate(self._rows):
            if db is btn:
                sp.deleteLater()
                cb.deleteLater()
                db.deleteLater()
                self._rows.pop(i)
                return

    def result_config(self):
        periods, colors = [], []
        for sp, cb, _ in self._rows:
            periods.append(sp.value())
            colors.append(cb.text())
        return periods, colors

    def result_width(self) -> float:
        return self.sp_width.value()


class AiConfigDialog(QDialog):
    """AI 服务设置：服务商预设、接口地址、模型、API Key。"""

    def __init__(self, conf: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 设置")
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            "支持任何兼容 OpenAI chat/completions 协议的服务；"
            "选择预设可自动填入地址与模型，也可选「自定义」手动填写。"))
        grid = QGridLayout()
        self.cb_provider = QComboBox()
        self.cb_provider.addItems(list(AI_PRESETS.keys()))
        provider = conf.get("provider") or "DeepSeek"
        if provider in AI_PRESETS:
            self.cb_provider.setCurrentText(provider)
        else:
            self.cb_provider.setCurrentText("自定义")
        self.cb_provider.currentTextChanged.connect(self._apply_preset)
        self.ed_base = QLineEdit(conf.get("base_url") or API_URL)
        self.ed_base.setPlaceholderText(
            "https://api.deepseek.com/chat/completions（可只填到 /v1）")
        self.ed_model = QLineEdit(conf.get("model") or DEFAULT_MODEL)
        self.ed_model.setPlaceholderText("模型名称，如 deepseek-chat")
        self.ed_key = QLineEdit(conf.get("key") or "")
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.setPlaceholderText("API Key，仅保存在本机")
        grid.addWidget(QLabel("服务商"), 0, 0)
        grid.addWidget(self.cb_provider, 0, 1)
        grid.addWidget(QLabel("接口地址"), 1, 0)
        grid.addWidget(self.ed_base, 1, 1)
        grid.addWidget(QLabel("模型"), 2, 0)
        grid.addWidget(self.ed_model, 2, 1)
        grid.addWidget(QLabel("API Key"), 3, 0)
        grid.addWidget(self.ed_key, 3, 1)
        v.addLayout(grid)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _apply_preset(self, name: str):
        """切换服务商时填入对应默认地址与模型（自定义不覆盖）。"""
        base, model = AI_PRESETS.get(name, ("", ""))
        if not base:
            return
        self.ed_base.setText(base)
        self.ed_model.setText(model)

    def result_conf(self) -> dict:
        return {
            "provider": self.cb_provider.currentText(),
            "base_url": normalize_url(self.ed_base.text()),
            "model": self.ed_model.text().strip() or DEFAULT_MODEL,
            "key": sanitize_key(self.ed_key.text()),
        }


class DeepSeekThread(QThread):
    """mode: interpret=梳理条件(纯文本) / generate=生成公式(JSON)。"""
    done = Signal(dict)
    done_text = Signal(str)
    failed = Signal(str)

    def __init__(self, api_key: str, desc: str, mode: str = "interpret",
                 confirmed: str = "", image_b64: str = "",
                 bad: str = "", error: str = "", base_url: str = "",
                 model: str = "", parent=None):
        super().__init__(parent)
        self._key, self._desc = api_key, desc
        self._mode, self._confirmed = mode, confirmed
        self._image_b64 = image_b64
        self._bad, self._error = bad, error
        self._base_url, self._model = base_url, model

    def run(self):
        try:
            if self._mode == "interpret":
                self.done_text.emit(interpret_description(
                    self._key, self._desc, self._image_b64,
                    model=self._model, base_url=self._base_url))
            elif self._mode == "fix":
                self.done.emit(fix_pattern(
                    self._key, self._desc, self._bad, self._error,
                    model=self._model, base_url=self._base_url))
            else:
                self.done.emit(generate_pattern(
                    self._key, self._desc, self._confirmed, self._image_b64,
                    model=self._model, base_url=self._base_url))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class MinFetchThread(QThread):
    """后台拉取分钟K线。mode: min1 / min5 / trend。"""
    done = Signal(str, object, str, str, float)  # mode, df, code, date_str, prev_close
    failed = Signal(str)

    def __init__(self, code: str, mode: str, beg: str, end: str,
                 date_str: str = "", prev_close: float = 0.0, parent=None):
        super().__init__(parent)
        self._code, self._mode = code, mode
        self._beg, self._end = beg, end
        self._date_str, self._prev_close = date_str, prev_close

    def run(self):
        klt = 5 if self._mode == "min5" else 1
        try:
            # 5分钟尽量拉历史（命中日前后各 60 天），1分钟两边都只有当日
            df = data.fetch_kline_min(self._code, klt, self._beg, self._end,
                                      extend_hist=(klt >= 5))
            mode = self._mode
            if df.empty and klt == 1:  # 1分钟仅有当日，回退5分钟
                df = data.fetch_kline_min(self._code, 5, self._beg, self._end,
                                          extend_hist=True)
                if mode == "min1":
                    mode = "min5"
            if df.empty:
                self.failed.emit("分钟数据暂不可用")
                return
            self.done.emit(mode, df, self._code,
                           self._date_str, self._prev_close)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class DayFetchThread(QThread):
    """后台拉取日K全历史，避免点击个股时阻塞界面。"""
    done = Signal(str, str, str, object)   # code, name, date, df
    failed = Signal(str, str)              # name, error

    def __init__(self, code: str, name: str, date: str, parent=None):
        super().__init__(parent)
        self._code, self._name, self._date = code, name, date

    def run(self):
        try:
            end = datetime.now().strftime("%Y%m%d")
            df = data.fetch_kline(self._code, "19900101", end)
            df = data.ensure_today_bar(df, self._code, end)
            self.done.emit(self._code, self._name, self._date, df)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(self._name, str(e))


class TradeDayProbe(QThread):
    """用上证指数日K判断某日是否开市（周末已在主线程判定，无需探测）。

    缓存文件名用 IDX000001，避免与平安银行 000001 的日K缓存互相覆盖。
    """
    result = Signal(str, bool)   # 日期(YYYY-MM-DD), 是否开市

    def __init__(self, date_str: str, parent=None):
        super().__init__(parent)
        self._date = date_str

    def run(self):
        try:
            day = datetime.strptime(self._date, "%Y-%m-%d")
            beg = (day - timedelta(days=20)).strftime("%Y%m%d")
            end = (day + timedelta(days=7)).strftime("%Y%m%d")
            df = data.fetch_kline("IDX000001", beg, end, secid="1.000001")
            hit = any(str(x)[:10] == self._date for x in df["date"])
            self.result.emit(self._date, hit)
        except Exception:  # noqa: BLE001
            pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("K线形态选股器")
        icon_path = os.path.join(
            getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
            "app.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1280, 800)
        self.settings = QSettings("StockPicker", "StockPicker")
        self._screener: ScreenerThread | None = None
        self._ds_thread: DeepSeekThread | None = None
        self._min_thread: MinFetchThread | None = None
        self._day_thread: DayFetchThread | None = None
        self._day_pending = None  # 等待中的最新日K请求 (code, name, date)
        self._day_want = ""       # 当前希望显示的日K代码
        self._day_shown = None    # 已显示的日K数据标识，避免重复刷新打断缩放
        # 鼠标“按下-拖动-松开”跟踪：拖选文字/拖动平移后短暂屏蔽 Insert，
        # 防止松手瞬间的 Insert（含鼠标侧键映射）误触收藏
        self._drag_active = False
        self._drag_moved = False
        self._drag_start = None
        self._insert_block_until = 0.0
        self._overlay_cache: dict = {}   # 指数/板块日K，避免每次切股重复拉取
        self._ref_date = ""      # 离开日K模式时记住的参考日（揭示日）
        self._ref_pc = 0.0       # 参考日前一交易日收盘价
        self._trade_day_cache: dict = {}   # 日期 -> 是否开市
        self._probe: TradeDayProbe | None = None
        self._probe_want = ""    # 待探测日期（用户连续改日期时只保留最后一次）
        self._probe_cur = ""     # 正在探测的日期
        self._note_date = ""     # 当前标注对应的日期
        # 笔记：右侧竖排标签栏，正文编辑区点开后出现，按名字存进 notes.json
        self._notes: dict[str, str] = {}
        self._cur_note = ""
        self._note_tag_btns: list[QPushButton] = []
        self._note_prev_width = 170      # 收起编辑区时恢复的右栏宽度
        self._note_save_timer = QTimer(self)
        self._note_save_timer.setSingleShot(True)
        self._note_save_timer.setInterval(1500)
        self._note_save_timer.timeout.connect(self._save_current_note)
        self._notes = _load_notes_file()
        # 旧版本保存过自定义K线背景的，继续视为手动颜色，不被主题覆盖
        if (self.settings.value("chart_bg")
                and self.settings.value("chart_bg_auto") is None):
            self.settings.setValue("chart_bg_auto", "0")

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(7)      # 加宽分割条，方便鼠标拖动调整侧栏宽度
        self.setCentralWidget(splitter)
        self._splitter = splitter
        self._left_panel = self._build_left()
        splitter.addWidget(self._left_panel)
        splitter.addWidget(self._build_right())
        self._note_panel = self._build_note_panel()
        splitter.addWidget(self._note_panel)
        splitter.setSizes([420, 860, 170])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(2, False)
        self._migrate_palette()
        self._apply_ma_config()
        self.chart.set_draw_width(
            float(self.settings.value("draw_width", 1.5)))
        self._apply_text_style()
        self._apply_chart_colors()
        self._apply_rr_colors()
        self._cur_draw_code = ""   # 当前已加载标注的股票代码

        QApplication.instance().installEventFilter(self)
        self._build_appearance_menu()
        geo = self.settings.value("win_geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        sp = self.settings.value("splitter_state")
        if sp is not None:
            splitter.restoreState(sp)
        # restoreState 会一并恢复旧版本保存的手柄宽度，需在其后重设
        splitter.setHandleWidth(7)
        if str(self.settings.value("note_panel_visible", "1")) == "0":
            self._set_note_panel_visible(False)
        self._apply_theme()
        self.statusBar().showMessage("就绪")

    # ---------------- 外观 ----------------
    def _build_appearance_menu(self):
        menu = self.menuBar().addMenu("外观")
        mt = menu.addMenu("主题")
        self._theme_acts = {}
        grp = QActionGroup(self)
        for key, theme in THEMES.items():
            act = mt.addAction(theme["label"])
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, k=key: self._set_theme(k))
            grp.addAction(act)
            self._theme_acts[key] = act
        ma = menu.addMenu("强调色")
        self._accent_acts = {}
        grp2 = QActionGroup(self)
        for label, hexv in ACCENTS:
            act = ma.addAction(label)
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, h=hexv: self._set_accent(h))
            grp2.addAction(act)
            self._accent_acts[hexv] = act
        mf = menu.addMenu("字号")
        self._font_acts = {}
        grp3 = QActionGroup(self)
        for pt in (8, 9, 10, 11, 12, 13):
            act = mf.addAction(f"{pt} 号")
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, p=pt: self._set_font_size(p))
            grp3.addAction(act)
            self._font_acts[pt] = act
        md = menu.addMenu("界面密度")
        self._density_acts = {}
        grp4 = QActionGroup(self)
        for key, d in DENSITY.items():
            act = md.addAction(d["label"])
            act.setCheckable(True)
            act.triggered.connect(lambda _c=False, k=key: self._set_density(k))
            grp4.addAction(act)
            self._density_acts[key] = act
        menu.addSeparator()
        self.act_note_panel = menu.addAction("显示笔记栏")
        self.act_note_panel.setCheckable(True)
        self.act_note_panel.setChecked(True)
        self.act_note_panel.triggered.connect(self._set_note_panel_visible)
        menu.addAction("K线配色…", self._open_color_dialog)
        menu.addAction("K线背景颜色…", self._pick_bg)
        menu.addAction("恢复默认外观", self._reset_appearance)

        mdata = self.menuBar().addMenu("数据")
        mdata.addAction("导出数据…", self._export_data)
        mdata.addAction("导入数据…", self._import_data)

    def _sync_appearance(self):
        name = str(self.settings.value("ui_theme", DEFAULT_THEME))
        if name in self._theme_acts:
            self._theme_acts[name].setChecked(True)
        accent = str(self.settings.value("ui_accent", DEFAULT_ACCENT))
        if accent in self._accent_acts:
            self._accent_acts[accent].setChecked(True)
        try:
            pt = int(self.settings.value("ui_font_size", 9))
        except (TypeError, ValueError):
            pt = 9
        if pt in self._font_acts:
            self._font_acts[pt].setChecked(True)
        density = str(self.settings.value("ui_density", DEFAULT_DENSITY))
        if density in self._density_acts:
            self._density_acts[density].setChecked(True)

    def _apply_theme(self):
        name = str(self.settings.value("ui_theme", DEFAULT_THEME))
        theme = THEMES.get(name, THEMES[DEFAULT_THEME])
        accent = str(self.settings.value("ui_accent", DEFAULT_ACCENT))
        density = str(self.settings.value("ui_density", DEFAULT_DENSITY))
        if density not in DENSITY:
            density = DEFAULT_DENSITY
        app = QApplication.instance()
        app.setStyleSheet(build_qss(theme, accent, density,
                                    _ensure_check_png()))
        row_h = DENSITY[density]["row"]
        if hasattr(self, "table"):
            self.table.verticalHeader().setDefaultSectionSize(row_h)
        for _folder, tbl in getattr(self, "_fav_pages", []):
            tbl.verticalHeader().setDefaultSectionSize(row_h)
        for b in getattr(self, "_chart_btns", []):
            b.setFixedHeight(DENSITY[density]["ctrl_h"])
        try:
            pt = int(self.settings.value("ui_font_size", 9))
        except (TypeError, ValueError):
            pt = 9
        font = app.font()
        font.setPointSize(pt)
        app.setFont(font)
        if str(self.settings.value("chart_bg_auto", "1")) in ("1", "True", "true"):
            self.chart.set_bg(theme["chart_bg"])
            self.settings.setValue("chart_bg", theme["chart_bg"])
        else:
            self.chart.set_bg(self.settings.value("chart_bg", theme["chart_bg"]))
        # 文字默认色会随背景明暗自动切换，这里同步到工具栏色块
        if hasattr(self, "btn_font_color"):
            self._text_color_hex = self.chart.text_color()
            self._update_color_btn()
        self._sync_appearance()

    def _set_theme(self, name: str):
        self.settings.setValue("ui_theme", name)
        self._apply_theme()
        self.statusBar().showMessage(f"主题：{THEMES.get(name, {}).get('label', name)}")

    def _set_accent(self, hex_color: str):
        self.settings.setValue("ui_accent", hex_color)
        self._apply_theme()

    def _set_font_size(self, pt: int):
        self.settings.setValue("ui_font_size", pt)
        self._apply_theme()

    def _set_density(self, key: str):
        self.settings.setValue("ui_density", key)
        self._apply_theme()

    def _reset_appearance(self):
        for key in ("ui_theme", "ui_accent", "ui_font_size", "ui_density",
                    "chart_bg", "chart_bg_auto", "ma_colors", "chart_up",
                    "chart_down", "chart_draw_colors", "text_color",
                    "rr_profit_color", "rr_loss_color", "rr_entry_color",
                    "palette_v"):
            self.settings.remove(key)
        self._apply_theme()
        self._apply_ma_config()
        self._apply_text_style()
        self._apply_chart_colors()
        self._apply_rr_colors()
        self.statusBar().showMessage("已恢复默认外观与配色")

    # ---------------- UI ----------------
    def _build_left(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        grp_ai = FoldGroup("形态描述（自然语言）", self.settings, "fold_ai")
        v = QVBoxLayout(grp_ai)
        v.setContentsMargins(0, 0, 0, 0)   # 缩进交给 QSS padding，折叠盒更薄
        self.ed_desc = QPlainTextEdit()
        self.ed_desc.setPlaceholderText("例如：阳包阴 / 早晨之星 / 放量突破20日均线 …（也可 Ctrl+V 粘贴K线截图，按图生成）")
        self.ed_desc.setMaximumHeight(48)
        v.addWidget(self.ed_desc)
        self._image_b64 = ""
        self.w_img = QWidget()
        irow = QHBoxLayout(self.w_img)
        irow.setContentsMargins(0, 0, 0, 0)
        self.lb_img = QLabel()
        btn_clr_img = QPushButton("✕")
        btn_clr_img.setFixedSize(20, 20)
        btn_clr_img.setToolTip("移除截图")
        btn_clr_img.clicked.connect(self._clear_image)
        irow.addWidget(self.lb_img)
        irow.addWidget(btn_clr_img, 0, Qt.AlignTop)
        irow.addStretch(1)
        self.w_img.setVisible(False)
        v.addWidget(self.w_img)
        self.ed_interpret = QPlainTextEdit()
        self.ed_interpret.setPlaceholderText("点「① 条件梳理（AI）」后，AI 的条件理解显示在这里；可直接修改，再点「② 确认生成公式」")
        self.ed_interpret.setMaximumHeight(80)
        v.addWidget(self.ed_interpret)
        row = QHBoxLayout()
        self.btn_gen = QPushButton("① 条件梳理（AI）")
        self.btn_gen.clicked.connect(self.on_interpret)
        self.btn_confirm = QPushButton("② 确认并生成公式")
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.clicked.connect(self.on_generate)
        btn_key = QPushButton("AI 设置")
        btn_key.setToolTip("选择 AI 服务商、接口地址、模型和 API Key")
        btn_key.clicked.connect(self.on_ai_config)
        row.addWidget(self.btn_gen)
        row.addWidget(self.btn_confirm)
        row.addWidget(btn_key)
        v.addLayout(row)
        grp_ai._restore = lambda: self.w_img.setVisible(bool(self._image_b64))
        grp_ai.set_folded(bool(int(self.settings.value("fold_ai", 0))))
        lay.addWidget(grp_ai)

        grp_dsl = FoldGroup("筛选条件（JSON，可手工编辑）",
                            self.settings, "fold_dsl")
        v = QVBoxLayout(grp_dsl)
        v.setContentsMargins(0, 0, 0, 0)
        prow = QHBoxLayout()
        self._patterns: dict = {}
        self.cb_patterns = QComboBox()
        self._refresh_patterns()
        self.cb_patterns.activated.connect(self._on_pattern_selected)
        btn_pat_save = QPushButton("保存条件")
        btn_pat_save.clicked.connect(self._on_pattern_save)
        btn_pat_del = QPushButton("删除")
        btn_pat_del.clicked.connect(self._on_pattern_delete)
        prow.addWidget(QLabel("已存条件"))
        prow.addWidget(self.cb_patterns, stretch=1)
        prow.addWidget(btn_pat_save)
        prow.addWidget(btn_pat_del)
        v.addLayout(prow)
        self.ed_dsl = QPlainTextEdit()
        self.ed_dsl.setFont(QFont("Consolas", 9))
        self.ed_dsl.setPlainText(json.dumps(DEFAULT_DSL, ensure_ascii=False, indent=2))
        v.addWidget(self.ed_dsl)
        lay.addWidget(grp_dsl, stretch=1)
        # 折叠时不再参与剩余空间分配，把空间让给结果列表
        grp_dsl._on_fold = lambda folded: lay.setStretch(
            lay.indexOf(grp_dsl), 0 if folded else 1)
        grp_dsl.set_folded(bool(int(self.settings.value("fold_dsl", 0))))

        grp_scan = QGroupBox("筛选范围")
        v = QVBoxLayout(grp_scan)
        v.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.dt_date = QDateEdit(QDate.currentDate())
        self.dt_date.setCalendarPopup(True)
        self.dt_date.setDisplayFormat("yyyy-MM-dd")
        self.dt_date.setToolTip("按 年/月/日 分段编辑，或用 ↑↓ 调整、日历选择")
        row.addWidget(QLabel("日期"))
        row.addWidget(self.dt_date)
        self.lb_date_note = QLabel("")
        self.lb_date_note.setObjectName("warnnote")
        self.lb_date_note.setToolTip("所选日期为休市日时，筛选结果会为空")
        row.addWidget(self.lb_date_note)
        self.ck_today = QCheckBox("当日")
        self.ck_today.toggled.connect(self.dt_date.setDisabled)
        self.ck_today.toggled.connect(self._update_date_note)
        self.dt_date.dateChanged.connect(self._update_date_note)
        row.addWidget(self.ck_today)
        v.addLayout(row)
        self._update_date_note()

        row = QHBoxLayout()
        self.cb_pool = QComboBox()
        self.cb_pool.addItems(["全部A股", "自定义代码"])
        self.ed_codes = QLineEdit()
        self.ed_codes.setPlaceholderText("600000,000001,300750 …")
        self.cb_pool.currentIndexChanged.connect(self._on_pool_changed)
        row.addWidget(QLabel("股票池"))
        row.addWidget(self.cb_pool)
        row.addWidget(self.ed_codes, stretch=1)
        v.addLayout(row)

        self.w_excl = QWidget()
        # 两行网格：单行铺开会把左栏最小宽度撑到 ~500px，导致分割条拖不动
        exgrid = QGridLayout(self.w_excl)
        exgrid.setContentsMargins(0, 0, 0, 0)
        exgrid.setSpacing(4)
        self.ck_excl = {}
        for i, (key, label, default) in enumerate((
                ("st", "剔除ST", 1), ("star", "剔除科创板", 1),
                ("chinext", "剔除创业板", 1), ("bse", "剔除北证", 1),
                ("sh", "剔除沪市", 0), ("sz", "剔除深市", 0))):
            ck = QCheckBox(label)
            ck.setChecked(bool(int(self.settings.value(f"excl_{key}", default))))
            self.ck_excl[key] = ck
            exgrid.addWidget(ck, i // 3, i % 3)
        self.w_excl.setVisible(False)
        v.addWidget(self.w_excl)
        self._on_pool_changed(0)   # 默认：全部A股

        row = QHBoxLayout()
        self.ck_force = QCheckBox("强制刷新数据")
        self.btn_scan = QPushButton("开始筛选")
        self.btn_scan.setObjectName("primary")
        self.btn_scan.clicked.connect(self.on_scan)
        row.addWidget(self.ck_force)
        row.addStretch(1)
        row.addWidget(self.btn_scan)
        v.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        lay.addWidget(grp_scan)

        self.lb_count = QLabel("结果：0 条")
        self.lb_count.setObjectName("count")
        lay.addWidget(self.lb_count)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["代码", "名称", "命中日期"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.currentCellChanged.connect(self.on_row_changed)
        self.table.itemDoubleClicked.connect(self.on_row_double_clicked)

        self.tabs = QTabWidget()
        self.tabs.setCornerWidget(self._build_fav_corner(), Qt.TopRightCorner)
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(
            self._on_fav_tab_menu)

        w_res = QWidget()
        lr = QVBoxLayout(w_res)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.addWidget(self.table)
        self.tabs.addTab(w_res, "筛选结果")

        lay.addWidget(self.tabs, stretch=2)
        self._fav_pages: list = []          # [(folder, table), ...]
        self._last_fav_folder = DEFAULT_FAV_FOLDER
        self._fav_busy = False
        self._refresh_fav_table(switch=False)
        self.tabs.setCurrentIndex(0)        # 启动默认停在「筛选结果」
        return w

    def _build_fav_corner(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 2, 0)
        h.setSpacing(2)
        b_add = QPushButton("+")
        b_add.setFixedSize(22, 20)
        b_add.setToolTip("新建收藏夹")
        b_add.clicked.connect(self.on_new_fav_folder)
        b_rm = QPushButton("−")
        b_rm.setFixedSize(22, 20)
        b_rm.setToolTip("删除当前收藏夹")
        b_rm.clicked.connect(self.on_delete_fav_folder)
        h.addWidget(b_add)
        h.addWidget(b_rm)
        return w

    # ---------------- 笔记 ----------------
    def _build_note_panel(self) -> QWidget:
        """窗口最右侧的笔记栏。

        标签竖排；正文编辑区默认隐藏，点标签或新建笔记后才展开。
        """
        w = QWidget()
        w.setObjectName("note_panel")
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setMinimumWidth(150)
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        lb_title = QLabel("笔记")
        lb_title.setObjectName("note_title")
        btn_hide = QPushButton("×")
        btn_hide.setObjectName("note_hide")
        btn_hide.setFixedSize(18, 18)
        btn_hide.setCursor(Qt.PointingHandCursor)
        btn_hide.setToolTip("隐藏笔记栏（「外观」菜单里可重新显示）")
        btn_hide.clicked.connect(lambda: self._set_note_panel_visible(False))
        hdr.addWidget(lb_title, stretch=1)
        hdr.addWidget(btn_hide)
        v.addLayout(hdr)

        self.btn_new_note = QPushButton("新建笔记")
        self.btn_new_note.setObjectName("danger")
        self.btn_new_note.setCursor(Qt.PointingHandCursor)
        self.btn_new_note.setToolTip("新建一个可命名的笔记")
        self.btn_new_note.clicked.connect(self._on_new_note)
        v.addWidget(self.btn_new_note)

        # 标签竖排，超出时内部滚动
        self._note_tag_row = QVBoxLayout()
        self._note_tag_row.setContentsMargins(0, 0, 0, 0)
        self._note_tag_row.setSpacing(4)
        self._note_tag_host = QWidget()
        self._note_tag_host.setLayout(self._note_tag_row)
        self._note_tag_scroll = QScrollArea()
        self._note_tag_scroll.setObjectName("note_tag_scroll")
        self._note_tag_scroll.setWidgetResizable(True)
        self._note_tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._note_tag_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._note_tag_scroll.setFrameShape(QFrame.NoFrame)
        self._note_tag_scroll.setWidget(self._note_tag_host)
        v.addWidget(self._note_tag_scroll, stretch=1)

        # 正文编辑区：默认不显示，点标签后出现
        self._note_edit_box = QWidget()
        ev = QVBoxLayout(self._note_edit_box)
        ev.setContentsMargins(0, 6, 0, 0)
        ev.setSpacing(4)

        hrow = QHBoxLayout()
        hrow.setContentsMargins(0, 0, 0, 0)
        self.lb_note_title = QLabel("")
        self.lb_note_title.setObjectName("note_title")
        btn_collapse = QPushButton("收起")
        btn_collapse.setFixedHeight(20)
        btn_collapse.setCursor(Qt.PointingHandCursor)
        btn_collapse.setToolTip("收起笔记编辑区")
        btn_collapse.clicked.connect(self._hide_note_editor)
        hrow.addWidget(self.lb_note_title, stretch=1)
        hrow.addWidget(btn_collapse)
        ev.addLayout(hrow)

        self.ed_note = QPlainTextEdit()
        self.ed_note.setPlaceholderText("在这里记录…")
        self.ed_note.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.ed_note.textChanged.connect(self._on_note_text_changed)
        ev.addWidget(self.ed_note, stretch=1)

        self._note_edit_box.setVisible(False)
        v.addWidget(self._note_edit_box, stretch=2)

        self._refresh_note_bar()
        return w

    def _show_note_editor(self):
        """展开编辑区；右栏太窄时顺带加宽。"""
        self._note_edit_box.setVisible(True)
        sizes = self._splitter.sizes()
        if len(sizes) == 3 and sizes[2] < 330:
            if sizes[2] > 0:
                self._note_prev_width = sizes[2]
            sizes[2] = 340
            self._splitter.setSizes(sizes)
        self.lb_note_title.setText(self._cur_note)

    def _hide_note_editor(self):
        """收起编辑区并恢复原来的右栏宽度。"""
        self._save_current_note()
        self._note_edit_box.setVisible(False)
        self.lb_note_title.setText("")
        sizes = self._splitter.sizes()
        if len(sizes) == 3 and self._note_panel.isVisible():
            sizes[2] = min(self._note_prev_width or 170, 240)
            self._splitter.setSizes(sizes)

    def _set_note_panel_visible(self, visible: bool):
        """显示/隐藏右侧笔记栏，并记住状态。"""
        if visible:
            self._note_panel.setVisible(True)
            sizes = self._splitter.sizes()
            if len(sizes) == 3 and sizes[2] < 120:
                sizes[2] = max(self._note_prev_width, 170)
                self._splitter.setSizes(sizes)
        else:
            if self._note_edit_box.isVisible():
                self._save_current_note()
                self._note_edit_box.setVisible(False)
            sizes = self._splitter.sizes()
            if len(sizes) == 3 and sizes[2] >= 120:
                self._note_prev_width = sizes[2]
            self._note_panel.setVisible(False)
        self.settings.setValue("note_panel_visible", "1" if visible else "0")
        if hasattr(self, "act_note_panel"):
            self.act_note_panel.setChecked(visible)

    def _refresh_note_bar(self):
        """重建标签按钮列表并选中当前笔记。"""
        # 清掉旧按钮和旧弹簧，否则新标签会被之前的弹簧挤到底部
        while self._note_tag_row.count():
            item = self._note_tag_row.takeAt(0)
            old = item.widget()
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        self._note_tag_btns.clear()

        names = list(self._notes.keys())
        names.sort()     # 按名字排序，避免新建一个跑到队尾
        for name in names:
            b = QPushButton(name)
            b.setObjectName("note_tag")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"「{name}」\n单击编辑 / 双击重命名 / 右键删除")
            b.clicked.connect(lambda _c=False, n=name: self._switch_to_note(n))
            b.mouseDoubleClickEvent = (
                lambda _ev, n=name: self._rename_note(n))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, n=name, btn=b:
                    self._on_note_tag_menu(pos, n, btn))
            self._note_tag_row.addWidget(b)
            self._note_tag_btns.append(b)

        self._note_tag_row.addStretch(1)
        self._update_tag_checked()

    def _update_tag_checked(self):
        for b in self._note_tag_btns:
            b.setChecked(b.text() == self._cur_note)

    def _on_new_note(self):
        """弹框让用户命名新建笔记。"""
        name, ok = QInputDialog.getText(
            self, "新建笔记", "笔记名称：", echo=QLineEdit.Normal)
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.information(self, "笔记", "名字不能为空。")
            return
        if name in self._notes:
            QMessageBox.information(self, "笔记", f"已存在「{name}」。")
            return
        self._notes[name] = ""
        self._refresh_note_bar()
        self._switch_to_note(name)
        self.ed_note.setFocus()
        self.statusBar().showMessage(f"已新建笔记「{name}」")

    def _on_note_text_changed(self):
        """正文变化，延迟保存；切换笔记时已 blockSignals，不会误触发。"""
        if not self._cur_note:
            return
        self._note_save_timer.start()   # 重启 1.5s 定时器

    def _switch_to_note(self, name: str | None):
        """切换当前笔记：先把旧笔记内容存起来，再载入新笔记。"""
        self._save_current_note()
        if not name or name not in self._notes:
            self._cur_note = ""
            self.ed_note.blockSignals(True)
            self.ed_note.clear()
            self.ed_note.blockSignals(False)
            self._hide_note_editor()
            self._update_tag_checked()
            return
        self._cur_note = name
        self.ed_note.blockSignals(True)
        self.ed_note.setPlainText(self._notes.get(name, ""))
        self.ed_note.blockSignals(False)
        self._show_note_editor()
        self._update_tag_checked()

    def _rename_note(self, old_name: str):
        if old_name not in self._notes:
            return
        new_name, ok = QInputDialog.getText(
            self, "重命名笔记", "新名称：", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        if new_name in self._notes:
            QMessageBox.information(self, "笔记", f"已存在「{new_name}」。")
            return
        content = self._notes.pop(old_name)
        self._notes[new_name] = content
        if self._cur_note == old_name:
            self._cur_note = new_name
        _write_notes_file(self._notes)
        self._refresh_note_bar()
        self.statusBar().showMessage(
            f"笔记「{old_name}」已重命名为「{new_name}」")

    def _delete_note(self, name: str):
        if name not in self._notes:
            return
        reply = QMessageBox.question(
            self, "删除笔记",
            f"确定删除笔记「{name}」？此操作无法撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._notes.pop(name, None)
        _write_notes_file(self._notes)
        self._refresh_note_bar()
        if self._cur_note == name:
            remaining = list(self._notes.keys())
            remaining.sort()
            self._switch_to_note(remaining[0] if remaining else None)
        self.statusBar().showMessage(f"笔记「{name}」已删除")

    def _on_note_tag_menu(self, pos, name: str, btn: QPushButton):
        """笔记标签右键菜单。"""
        menu = QMenu(self)
        act_rename = menu.addAction("重命名")
        act_delete = menu.addAction("删除")
        act = menu.exec(btn.mapToGlobal(pos))
        if act is None:
            return
        if act is act_rename:
            self._rename_note(name)
        elif act is act_delete:
            self._delete_note(name)

    def _save_current_note(self):
        """把当前编辑中的正文写回 self._notes（暂不写文件，统一在 save_all 写）。"""
        self._note_save_timer.stop()
        if self._cur_note:
            self._notes[self._cur_note] = self.ed_note.toPlainText()

    def _save_all_notes(self):
        """先保存当前笔记，再整体写文件。"""
        self._save_current_note()
        _write_notes_file(self._notes)

    def _on_pool_changed(self, i: int):
        all_a = self.cb_pool.itemText(i) == "全部A股"
        self.ed_codes.setVisible(not all_a)
        self.w_excl.setVisible(all_a)

    # ---------------- 休市日标注 ----------------
    def _update_date_note(self, *_args):
        """日期旁标注：周末本地判定，工作日尽量用上证指数日K校验。"""
        if self.ck_today.isChecked():
            self._note_date = ""
            # 盘中选“当日”时，命中基于未收盘的K线，收盘后可能变化
            self.lb_date_note.setText(
                "盘中未收盘" if data.market_is_open() else "")
            return
        qd = self.dt_date.date()
        text = f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"
        day = datetime(qd.year(), qd.month(), qd.day())
        if day.weekday() >= 5:
            # 周末无需联网即可确定
            self._trade_day_cache[text] = False
            self._show_date_note(text, False, "周末")
            return
        if day.date() > datetime.now().date():
            # 未来日期无法判断是否节假日，不加标注
            self._note_date = ""
            self.lb_date_note.setText("")
            return
        known = self._trade_day_cache.get(text)
        if known is not None:
            self._show_date_note(text, known)
        else:
            self._note_date = text
            self.lb_date_note.setText("校验交易日…")
            self._request_probe(text)

    def _show_date_note(self, text: str, is_trade: bool, reason: str = "休市"):
        self._note_date = text
        if is_trade:
            self.lb_date_note.setText("")
        elif (reason == "周末"
              or text != datetime.now().strftime("%Y-%m-%d")):
            self.lb_date_note.setText(f"非交易日（{reason}）")
        else:
            # 当日盘前/盘中指数日K可能还没生成，不作休市标注
            self.lb_date_note.setText("")

    def _request_probe(self, text: str):
        """同一时间只跑一个探测线程，多余的日期只记最后一次。"""
        self._probe_want = text
        if self._probe is not None and self._probe.isRunning():
            return
        self._launch_probe()

    def _launch_probe(self):
        text, self._probe_want = self._probe_want, ""
        if not text:
            return
        self._probe_cur = text
        probe = TradeDayProbe(text, self)
        probe.result.connect(self._on_probe_result)
        probe.finished.connect(self._on_probe_done)
        self._probe = probe
        probe.start()

    def _on_probe_result(self, text: str, is_trade: bool):
        if not is_trade and text == datetime.now().strftime("%Y-%m-%d"):
            # 当日盘前/盘中指数日K可能尚未生成，不作休市判断
            return
        self._trade_day_cache[text] = is_trade
        if text == self._note_date:
            self._show_date_note(text, is_trade)

    def _on_probe_done(self):
        probe = self._probe
        if probe is None:
            return
        fail_text = self._probe_cur
        self._probe = None
        self._probe_cur = ""
        probe.deleteLater()
        if self._probe_want:
            self._launch_probe()
        elif (fail_text == self._note_date
              and fail_text not in self._trade_day_cache
              and self.lb_date_note.text() == "校验交易日…"):
            # 探测失败（断网等）时不保留占位提示
            self.lb_date_note.setText("")

    @staticmethod
    def _make_sep() -> QFrame:
        f = QFrame()
        f.setObjectName("sep")
        f.setFixedWidth(1)
        return f

    def _sync_mode_buttons(self):
        m = self.chart.mode()
        self.btn_day.setChecked(m == "day")
        self.btn_trend.setChecked(m == "trend")
        self.btn_m1.setChecked(m == "min1")
        self.btn_m5.setChecked(m == "min5")

    def _on_draw_mode_changed(self, mode: str):
        """画线工具切换（含画完自动回到选择）时同步按钮选中态。"""
        btn = self._draw_tool_map.get(mode)
        if btn is not None:
            btn.setChecked(True)

    def _on_draw_width_changed(self, value: float):
        self.chart.set_draw_width(value, apply_selected=True)
        self.settings.setValue("draw_width", float(value))

    def _build_right(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.chart = KLineChart()
        self.btn_day = QPushButton("日K (Esc)")
        self.btn_trend = QPushButton("分时")
        self.btn_m1 = QPushButton("1分钟")
        self.btn_m5 = QPushButton("5分钟")
        self.btn_fit = QPushButton("全图")
        self.btn_reset = QPushButton("重置")
        self.btn_bg = QPushButton("背景")
        self.btn_sidebar = QPushButton("侧栏")
        self.btn_sidebar.setCheckable(True)
        self.btn_sidebar.setChecked(True)
        self.btn_sidebar.toggled.connect(self._left_panel.setVisible)
        mode_btns = (self.btn_day, self.btn_trend, self.btn_m1, self.btn_m5)
        for b in mode_btns:
            b.setCheckable(True)
            b.setAutoExclusive(True)   # 周期按钮互斥，当前周期常亮
        self._chart_btns = [*mode_btns, self.btn_fit, self.btn_reset,
                            self.btn_bg, self.btn_sidebar]
        for b in mode_btns:
            bar.addWidget(b)
        bar.addWidget(self._make_sep())
        bar.addWidget(self.btn_reset)
        bar.addWidget(self.btn_fit)
        bar.addWidget(self._make_sep())
        bar.addWidget(self.btn_bg)
        bar.addWidget(self.btn_sidebar)
        menu = QMenu(self)
        for tname, hexv in [("深黑", "#0f1115"), ("纯黑", "#000000"),
                            ("深蓝", "#101820"), ("浅灰", "#f0f0f0")]:
            act = menu.addAction(tname)
            act.triggered.connect(
                lambda _checked=False, h=hexv: self._set_chart_bg(h))
        menu.addSeparator()
        act = menu.addAction("自选颜色…")
        act.triggered.connect(self._pick_bg)
        self.btn_bg.setMenu(menu)
        bar.addStretch(1)
        self.btn_day.clicked.connect(self.show_day)
        self.btn_trend.clicked.connect(self.show_trend)
        self.btn_m1.clicked.connect(lambda: self.show_min(1))
        self.btn_m5.clicked.connect(lambda: self.show_min(5))
        self.chart.activated.connect(self.open_min_at)
        self.btn_fit.clicked.connect(self.chart.fit_all)
        self.btn_reset.setToolTip("回到命中日：最新K线刷新为命中日K线")
        self.btn_reset.clicked.connect(self.reset_chart)
        lay.addLayout(bar)

        # 第二行工具栏：画线工具 + 均线设置 + 叠加品种
        bar2 = QHBoxLayout()
        bar2.setSpacing(6)
        self.btn_sel = QPushButton("选择")
        self.btn_line = QPushButton("直线")
        self.btn_hline = QPushButton("水平线")
        self.btn_ray = QPushButton("射线")
        self.btn_percent = QPushButton("百分位")
        self.btn_fib = QPushButton("黄金分割")
        self.btn_rr = QPushButton("盈亏比区")
        self.btn_text = QPushButton("文字")
        self.btn_clr_draw = QPushButton("清除标注")
        tool_buttons = (
            (self.btn_sel, "select"), (self.btn_line, "line"),
            (self.btn_hline, "hline"), (self.btn_ray, "ray"),
            (self.btn_percent, "percent"), (self.btn_fib, "fib"),
            (self.btn_rr, "rr"), (self.btn_text, "text"),
        )
        for b, mode in tool_buttons:
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.clicked.connect(lambda _checked=False, m=mode:
                              self.chart.set_draw_mode(m))
        self._draw_tool_map = {mode: b for b, mode in tool_buttons}
        self.chart.draw_mode_changed.connect(self._on_draw_mode_changed)
        self.btn_sel.setChecked(True)
        self.btn_rr.setToolTip("依次点击止损、止盈，再按住拖动建仓区；"
                               "选择模式下可拖动边界修改")
        self.btn_clr_draw.clicked.connect(self._on_clear_drawings)
        self._draw_btns = tuple(b for b, _mode in tool_buttons) + (
            self.btn_clr_draw,)
        for b in self._draw_btns:
            bar2.addWidget(b)
        bar2.addWidget(self._make_sep())
        bar2.addWidget(QLabel("线宽"))
        self.sp_line_w = QDoubleSpinBox()
        self.sp_line_w.setRange(0.8, 8.0)
        self.sp_line_w.setSingleStep(0.5)
        self.sp_line_w.setDecimals(1)
        self.sp_line_w.setFixedWidth(62)
        self.sp_line_w.setValue(float(self.settings.value("draw_width", 1.5)))
        self.sp_line_w.setToolTip("调整标注线宽；选中某条标注时同时修改它")
        self.sp_line_w.valueChanged.connect(self._on_draw_width_changed)
        bar2.addWidget(self.sp_line_w)
        self.btn_ma = QPushButton("均线设置")
        self.btn_ma.clicked.connect(self._open_ma_dialog)
        bar2.addWidget(self.btn_ma)
        bar2.addStretch(1)
        lay.addLayout(bar2)

        # 第三行工具栏：文字标注样式（字号/字体/颜色）
        bar3 = QHBoxLayout()
        bar3.setSpacing(6)
        bar3.addWidget(QLabel("文字样式"))
        self.cb_font = QFontComboBox()
        self.cb_font.setMaximumWidth(150)
        self.sp_font = QSpinBox()
        self.sp_font.setRange(8, 48)
        self.sp_font.setSuffix(" 号")
        self.sp_font.setMaximumWidth(72)
        self.btn_font_color = QPushButton("颜色")
        self.btn_font_color.setToolTip("修改文字标注颜色")
        self._text_color_hex = DRAW_COLORS["text"]
        self.cb_font.currentFontChanged.connect(self._on_text_style_changed)
        self.sp_font.valueChanged.connect(self._on_text_style_changed)
        self.btn_font_color.clicked.connect(self._pick_text_color)
        for ctl in (self.cb_font, self.sp_font, self.btn_font_color):
            bar3.addWidget(ctl)
        bar3.addWidget(self._make_sep())
        bar3.addWidget(QLabel("盈亏区"))
        self._rr_btns = {}
        for key, title in (("profit_color", "盈利区域颜色"),
                           ("entry_color", "建仓区域颜色"),
                           ("loss_color", "亏损区域颜色")):
            b = QPushButton()
            b.setFixedSize(26, 20)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(f"设置{title}；选中盈亏比区时同时修改该区")
            b.clicked.connect(
                lambda _c=False, k=key, t=title: self._pick_rr_zone_color(k, t))
            self._rr_btns[key] = b
            bar3.addWidget(b)
        bar3.addWidget(self._make_sep())
        bar3.addWidget(QLabel("叠加品种"))
        self.cb_index = QComboBox()
        self.cb_index.setMinimumWidth(150)
        self.cb_index.addItem("不叠加", ("none",))
        for code, name, secid in INDEX_LIST:
            self.cb_index.addItem(f"指数：{name}",
                                  ("index", code, name, secid))
        self.cb_index.addItem("板块：加载列表…", ("load_boards",))
        self._loading_boards = False
        self.cb_index.currentIndexChanged.connect(self._on_index_changed)
        bar3.addWidget(self.cb_index)
        self.btn_colors = QPushButton("K线配色")
        self.btn_colors.setToolTip("统一设置涨跌、均线、各类划线与盈亏比区颜色")
        self.btn_colors.clicked.connect(self._open_color_dialog)
        bar3.addWidget(self.btn_colors)
        bar3.addStretch(1)
        lay.addLayout(bar3)
        self._chart_btns.extend([*self._draw_btns, self.btn_ma,
                                 self.btn_colors])

        lay.addWidget(self.chart, stretch=1)
        hint = QLabel("滚轮/↑↓=缩放  拖动=平移  空格=下一日  Shift+空格=上一日  "
                      "鼠标/←→=十字光标  回车/双击=当日5分钟  Esc=日K  "
                      "PgUp/PgDn=切换个股  Insert=收藏  Del=剔除收藏  "
                      "选择模式拖动标注=调整  右键标注=删除  单击文字=编辑  "
                      "画完自动回到选择")
        hint.setObjectName("hint")
        hint.setWordWrap(True)   # 自动换行；单行会把右栏最小宽度撑到 ~925px
        lay.addWidget(hint)
        return w

    # ---------------- 全局快捷键 ----------------
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        # 跟踪鼠标拖动：拖动（拖选文字/平移K线）刚结束时屏蔽 Insert
        et = ev.type()
        if et == QEvent.MouseButtonPress:
            self._drag_active = True
            self._drag_moved = False
            self._drag_start = ev.globalPosition().toPoint()
        elif et == QEvent.MouseMove and self._drag_active:
            if self._drag_start is not None and (
                    ev.globalPosition().toPoint() - self._drag_start
            ).manhattanLength() > 6:
                self._drag_moved = True
        elif et == QEvent.MouseButtonRelease:
            if self._drag_active and self._drag_moved:
                self._insert_block_until = time.monotonic() + 0.35
            self._drag_active = False
            self._drag_moved = False
            self._drag_start = None
        if et == QEvent.KeyPress:
            ke: QKeyEvent = ev
            focus = QApplication.focusWidget()
            if focus is self.ed_desc and ke.matches(QKeySequence.Paste):
                md = QApplication.clipboard().mimeData()
                if md.hasImage():
                    self._attach_image(QApplication.clipboard().image())
                    return True
            editing = isinstance(focus, (QLineEdit, QPlainTextEdit,
                                         QDateEdit, QSpinBox, QComboBox))
            if ke.key() == Qt.Key_Insert:
                # 带修饰键的 Insert 属于文本框编辑快捷键（Ctrl+Insert 复制、
                # Shift+Insert 粘贴），不能抢占；文本控件内的 Insert 也忽略。
                if ke.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier
                                     | Qt.AltModifier | Qt.MetaModifier):
                    return False
                if editing or self.chart.is_text_editing():
                    return False
                # 正在按住鼠标（拖动选字/拖动平移）时不响应，避免误触
                if QApplication.mouseButtons() != Qt.NoButton:
                    return False
                # 拖动进行中，或拖选/拖动刚结束（松手瞬间），同样不响应
                if self._drag_active or time.monotonic() < self._insert_block_until:
                    return False
                # 模态对话框（如均线设置）打开时不触发全局快捷键
                if QApplication.activeModalWidget() is not None:
                    return False
                # 仅在结果表格或K线图上响应，避免其它控件里误触收藏
                if focus is not self.table and focus is not self.chart:
                    return False
                rows = sorted({i.row() for i in self.table.selectedIndexes()})
                if len(rows) > 1:
                    self.on_add_favorite_batch()
                else:
                    self.on_add_favorite()
                return True
            if not editing:
                if ke.key() == Qt.Key_Space:
                    if ke.modifiers() & Qt.ShiftModifier:
                        self.chart.prev_day()
                    else:
                        self.chart.next_day()
                    return True
                page = self._active_fav()
                in_table = (focus is self.table
                            or (page is not None and focus is page[1]))
                if ke.key() in (Qt.Key_Up, Qt.Key_Down) and not in_table:
                    self.chart.zoom(ke.key() == Qt.Key_Up)
                    return True
                if ke.key() in (Qt.Key_PageUp, Qt.Key_PageDown):
                    self._move_row(ke.key())
                    return True
                if ke.key() in (Qt.Key_Return, Qt.Key_Enter):
                    d = self.chart.cursor_date()
                    if d:
                        self.open_min_at(d)
                    return True
                if ke.key() == Qt.Key_Escape:
                    self.show_day()
                    return True
                if ke.key() == Qt.Key_Delete and self.tabs.currentIndex() >= 1:
                    self.on_del_favorite()
                    return True
                if ke.key() in (Qt.Key_Left, Qt.Key_Right) and not in_table:
                    self.chart.cursor_move(-1 if ke.key() == Qt.Key_Left else 1)
                    return True
        return super().eventFilter(obj, ev)

    def _move_row(self, key):
        page = self._active_fav()
        t = page[1] if page else self.table
        row = t.currentRow()
        n = t.rowCount()
        if n == 0:
            return
        step = {Qt.Key_Up: -1, Qt.Key_Down: 1,
                Qt.Key_PageUp: -1, Qt.Key_PageDown: 1}[key]
        row = min(max(row + step, 0), n - 1)
        t.setCurrentCell(row, 0)

    # ---------------- 已存条件 ----------------
    def _refresh_patterns(self, select: str = ""):
        self._patterns = _load_patterns_file()
        self.cb_patterns.blockSignals(True)
        self.cb_patterns.clear()
        self.cb_patterns.addItem("（选择已存条件…）")
        self.cb_patterns.addItems(sorted(self._patterns.keys()))
        idx = self.cb_patterns.findText(select) if select else 0
        self.cb_patterns.setCurrentIndex(max(idx, 0))
        self.cb_patterns.blockSignals(False)

    def _on_pattern_selected(self, _index: int):
        name = self.cb_patterns.currentText()
        spec = self._patterns.get(name)
        if not spec:
            return
        self.ed_dsl.setPlainText(json.dumps(spec, ensure_ascii=False, indent=2))
        # 成套载入：形态描述 + AI 梳理结果 + 公式
        desc = spec.get("description", "")
        self.ed_desc.setPlainText(desc)
        interpret = spec.get("interpret", "")
        self.ed_interpret.setPlainText(interpret)
        self.btn_confirm.setEnabled(bool(interpret.strip()))
        self.statusBar().showMessage(f"已载入条件：{name}")

    def _on_pattern_save(self):
        try:
            spec = json.loads(self.ed_dsl.toPlainText())
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "条件 JSON 错误", str(e))
            return
        name, ok = QInputDialog.getText(self, "保存条件", "条件名称：",
                                        text=str(spec.get("name", "")))
        if not ok or not name.strip():
            return
        name = name.strip()
        spec["name"] = name
        # 成套保存：形态描述 + AI 梳理结果
        spec["description"] = self.ed_desc.toPlainText().strip()
        spec["interpret"] = self.ed_interpret.toPlainText().strip()
        # 关联收藏夹
        folders, _ = _read_fav_data()
        old = _load_patterns_file().get(name, {})
        default_folder = old.get("fav_folder", folders[0] if folders else DEFAULT_FAV_FOLDER)
        folder, ok2 = QInputDialog.getItem(
            self, "关联收藏夹", "收藏时自动放入：", folders,
            folders.index(default_folder) if default_folder in folders else 0,
            False)
        if not ok2:
            return
        spec["fav_folder"] = folder
        d = _load_patterns_file()
        d[name] = spec
        _save_patterns_file(d)
        self._refresh_patterns(name)
        self.statusBar().showMessage(f"条件已保存：{name}（关联收藏夹：{folder}）")

    def _on_pattern_delete(self):
        name = self.cb_patterns.currentText()
        if name not in self._patterns:
            return
        d = _load_patterns_file()
        d.pop(name, None)
        _save_patterns_file(d)
        self._refresh_patterns()
        self.statusBar().showMessage(f"条件已删除：{name}")

    # ---------------- AI ----------------
    def _ai_conf(self) -> dict:
        """读取当前 AI 配置；旧版本只存过 deepseek_key 的继续沿用。"""
        key = str(self.settings.value("ai_key", "") or "")
        if not key:
            key = str(self.settings.value("deepseek_key", "") or "")
        return {
            "provider": str(self.settings.value("ai_provider", "DeepSeek")
                            or "DeepSeek"),
            "base_url": str(self.settings.value("ai_base_url", API_URL)
                            or API_URL),
            "model": str(self.settings.value("ai_model", DEFAULT_MODEL)
                         or DEFAULT_MODEL),
            "key": key,
        }

    def on_ai_config(self):
        dlg = AiConfigDialog(self._ai_conf(), self)
        if dlg.exec() != QDialog.Accepted:
            return
        conf = dlg.result_conf()
        self.settings.setValue("ai_provider", conf["provider"])
        self.settings.setValue("ai_base_url", conf["base_url"])
        self.settings.setValue("ai_model", conf["model"])
        self.settings.setValue("ai_key", conf["key"])
        self.statusBar().showMessage(
            f"AI 设置已保存：{conf['provider']} / {conf['model']}")

    def _attach_image(self, qimg):
        if qimg.isNull():
            return
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        qimg.save(buf, "PNG")
        self._image_b64 = bytes(ba.toBase64()).decode()
        self.lb_img.setPixmap(QPixmap.fromImage(qimg).scaledToHeight(
            72, Qt.SmoothTransformation))
        self.w_img.setVisible(True)
        self.statusBar().showMessage("已附加截图，AI 将按图理解形态")

    def _clear_image(self):
        self._image_b64 = ""
        self.w_img.setVisible(False)

    def _check_desc_key(self):
        """返回 (形态描述, AI 配置)；任一缺失时返回空配置。"""
        desc = self.ed_desc.toPlainText().strip()
        if not desc and not self._image_b64:
            QMessageBox.information(self, "提示", "请先输入形态描述或粘贴K线截图")
            return "", {}
        conf = self._ai_conf()
        if not conf["key"]:
            QMessageBox.information(
                self, "提示", "请先点击「AI 设置」填写 API Key")
            return "", {}
        return desc, conf

    def on_interpret(self):
        desc, conf = self._check_desc_key()
        if not conf:
            return
        self.btn_gen.setEnabled(False)
        self.btn_gen.setText("梳理中…")
        self._ds_thread = DeepSeekThread(
            conf["key"], desc, "interpret", image_b64=self._image_b64,
            base_url=conf["base_url"], model=conf["model"], parent=self)
        self._ds_thread.done_text.connect(self._on_interpret_done)
        self._ds_thread.failed.connect(self._on_gen_failed)
        self._ds_thread.start()

    def _on_interpret_done(self, text: str):
        self.btn_gen.setEnabled(True)
        self.btn_gen.setText("① 条件梳理（AI）")
        self.ed_interpret.setPlainText(text)
        self.btn_confirm.setEnabled(True)
        self.statusBar().showMessage("条件已梳理，请检查修改后点「② 确认并生成公式」")

    def on_generate(self):
        desc, conf = self._check_desc_key()
        if not conf:
            return
        confirmed = self.ed_interpret.toPlainText().strip()
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.setText("生成中…")
        self._ds_thread = DeepSeekThread(
            conf["key"], desc, "generate", confirmed,
            image_b64=self._image_b64, base_url=conf["base_url"],
            model=conf["model"], parent=self)
        self._ds_thread.done.connect(self._on_gen_done)
        self._ds_thread.failed.connect(self._on_gen_failed)
        self._ds_thread.start()

    def _on_gen_done(self, spec: dict):
        self.btn_confirm.setEnabled(True)
        self.btn_confirm.setText("② 确认并生成公式")
        self.ed_dsl.setPlainText(json.dumps(spec, ensure_ascii=False, indent=2))
        try:
            CompiledPattern(spec)
            self.statusBar().showMessage(f"已生成条件：{spec.get('name', '')}（可点「保存条件」收藏）")
        except PatternError as e:
            self.statusBar().showMessage("生成的条件未通过校验，自动纠错中…")
            self._start_fix(str(e))

    def _on_gen_failed(self, msg: str):
        self.btn_gen.setEnabled(True)
        self.btn_gen.setText("① 条件梳理（AI）")
        self.btn_confirm.setEnabled(bool(self.ed_interpret.toPlainText().strip()))
        self.btn_confirm.setText("② 确认并生成公式")
        QMessageBox.warning(self, "AI 调用失败", msg)

    # ---------------- 筛选 ----------------
    def _parse_pattern(self) -> CompiledPattern | None:
        try:
            spec = json.loads(self.ed_dsl.toPlainText())
            return CompiledPattern(spec)
        except json.JSONDecodeError as e:
            self._offer_fix("条件 JSON 错误", str(e))
        except PatternError as e:
            self._offer_fix("条件错误", str(e))
        return None

    # ---------------- AI 纠错 ----------------
    def _offer_fix(self, title: str, err: str):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(err + "\n\n是否让 AI 自动纠错？")
        btn_fix = box.addButton("AI 纠错", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is btn_fix:
            self._start_fix(err)

    def _start_fix(self, err: str):
        conf = self._ai_conf()
        if not conf["key"]:
            QMessageBox.information(
                self, "提示", "请先点击「AI 设置」填写 API Key")
            return
        self.btn_scan.setEnabled(False)
        self.statusBar().showMessage("AI 纠错中…")
        self._ds_thread = DeepSeekThread(
            conf["key"], self.ed_desc.toPlainText().strip(), "fix",
            bad=self.ed_dsl.toPlainText(), error=err,
            base_url=conf["base_url"], model=conf["model"], parent=self)
        self._ds_thread.done.connect(self._on_fix_done)
        self._ds_thread.failed.connect(self._on_fix_failed)
        self._ds_thread.start()

    def _on_fix_done(self, spec: dict):
        self.btn_scan.setEnabled(True)
        self.ed_dsl.setPlainText(json.dumps(spec, ensure_ascii=False, indent=2))
        try:
            CompiledPattern(spec)
            self.statusBar().showMessage("纠错完成，可重新点「开始筛选」")
        except PatternError as e:
            self.statusBar().showMessage(f"纠错后仍有问题：{e}（再点「开始筛选」可继续纠错）")

    def _on_fix_failed(self, msg: str):
        self.btn_scan.setEnabled(True)
        self.statusBar().showMessage("纠错失败")
        QMessageBox.warning(self, "AI 纠错失败", msg)

    def on_scan(self):
        if self._screener and self._screener.isRunning():
            self._screener.stop()
            self.btn_scan.setEnabled(False)
            self.statusBar().showMessage("正在停止…")
            return
        pattern = self._parse_pattern()
        if pattern is None:
            return
        if self.ck_today.isChecked():
            start = end = datetime.now().strftime("%Y-%m-%d")
        else:
            start = end = self.dt_date.date().toString("yyyy-MM-dd")

        if self.cb_pool.currentText() == "全部A股":
            excl = {}
            for key, ck in self.ck_excl.items():
                excl[key] = ck.isChecked()
                self.settings.setValue(f"excl_{key}", int(ck.isChecked()))
            pool_spec = {"mode": "all", "exclude": excl}
        else:
            codes = [c.strip() for c in self.ed_codes.text()
                     .replace("，", ",").split(",") if c.strip()]
            codes = [c for c in codes if c.isdigit() and len(c) == 6]
            if not codes:
                QMessageBox.warning(self, "提示", "请输入有效的6位股票代码")
                return
            pool_spec = {"mode": "custom", "codes": codes}

        self.table.setRowCount(0)
        self.lb_count.setText("结果：0 条")
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.btn_scan.setText("停止")
        self._pattern = pattern
        self._scan_start = start

        self._screener = ScreenerThread(pool_spec, start, end, pattern,
                                        force_refresh=self.ck_force.isChecked(),
                                        parent=self)
        self._screener.pool_ready.connect(
            lambda n: self.progress.setRange(0, n))
        self._screener.progress.connect(self._on_progress)
        self._screener.match_found.connect(self._on_match)
        self._screener.status.connect(self.statusBar().showMessage)
        self._screener.finished_ok.connect(self._on_scan_done)
        self._screener.failed.connect(self._on_scan_failed)
        self._screener.start()

    def _on_progress(self, done: int, total: int, name: str):
        self.progress.setValue(done)
        self.statusBar().showMessage(f"筛选中 {done}/{total}：{name}")

    def _on_match(self, m: dict):
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, key in enumerate(("code", "name", "date")):
            self.table.setItem(r, c, QTableWidgetItem(m[key]))
        self.lb_count.setText(f"结果：{r + 1} 条")

    def _on_scan_done(self, count: int):
        self.btn_scan.setText("开始筛选")
        self.btn_scan.setEnabled(True)
        self.progress.setVisible(False)
        msg = f"筛选完成，共 {count} 条命中"
        msg += getattr(self._screener, "result_note", "")
        if count == 0:
            msg += "（请确认所选日期是交易日，或放宽条件）"
        self.statusBar().showMessage(msg)
        if self.table.rowCount():
            self.table.setCurrentCell(0, 0)

    def _on_scan_failed(self, msg: str):
        self.btn_scan.setText("开始筛选")
        self.btn_scan.setEnabled(True)
        self.progress.setVisible(False)
        QMessageBox.warning(self, "筛选失败", msg)

    # ---------------- 结果联动 ----------------
    def on_row_changed(self, row: int, _col: int, _pr: int, _pc: int):
        if row < 0:
            return
        code = self.table.item(row, 0).text()
        name = self.table.item(row, 1).text()
        date = self.table.item(row, 2).text()
        self._load_day_chart(code, name, date)

    def _load_day_chart(self, code: str, name: str, date: str):
        # 保存上一只股票的标注
        if self._cur_draw_code and self._cur_draw_code != code:
            _save_drawings(self._cur_draw_code, self.chart.drawings())
        self._cur_draw_code = code
        self._day_want = code
        self.chart.set_drawings(_load_drawings(code))
        # 先用本地缓存秒显，网络数据在后台补齐
        cached = data.read_day_cache(code)
        if cached is not None:
            self.chart.set_data(code, name, cached, date)
            self._day_shown = self._df_key(code, cached)
            self._after_day_chart()
        else:
            self.chart.clear()
            self.chart.set_loading(f"正在加载 {name} K线…")
        self._start_day_fetch(code, name, date)

    def _start_day_fetch(self, code: str, name: str, date: str):
        if self._day_thread is not None and self._day_thread.isRunning():
            # 已有请求在跑：只记住最后一次，完成后再拉最新的
            self._day_pending = (code, name, date)
            return
        self.statusBar().showMessage(f"加载 {name} 日K数据…")
        self._day_thread = DayFetchThread(code, name, date, self)
        self._day_thread.done.connect(self._on_day_done)
        self._day_thread.failed.connect(self._on_day_failed)
        self._day_thread.start()

    def _on_day_done(self, code: str, name: str, date: str, df):
        pending = self._day_pending
        self._day_pending = None
        if code == self._day_want and self._df_key(code, df) != self._day_shown:
            self.chart.set_data(code, name, df, date)
            self._day_shown = self._df_key(code, df)
            self._after_day_chart()
        if code == self._day_want:
            self.statusBar().showMessage(f"{name} {code} 日K 命中日 {date}")
        if pending:
            self._start_day_fetch(*pending)

    def _on_day_failed(self, name: str, msg: str):
        pending = self._day_pending
        self._day_pending = None
        self.chart.set_loading("")
        self.statusBar().showMessage(f"加载 {name} 失败: {msg}")
        if pending:
            self._start_day_fetch(*pending)

    @staticmethod
    def _df_key(code: str, df):
        """日K数据指纹：代码、根数、最后日期与收盘价。"""
        if df is None or df.empty:
            return (code, 0, "", 0.0)
        return (code, len(df), str(df["date"].iloc[-1])[:10],
                float(df["close"].iloc[-1]))

    def _after_day_chart(self):
        # 指数叠加保持（若已选），重绘即可
        if self.cb_index.currentIndex() > 0:
            self._on_index_changed(self.cb_index.currentIndex())
        self._sync_mode_buttons()

    # ---------------- 收藏夹 ----------------
    def _active_fav(self):
        """当前选中的收藏夹页签 -> (folder, table)；在筛选结果页时返回 None。"""
        idx = self.tabs.currentIndex()
        if 1 <= idx <= len(self._fav_pages):
            return self._fav_pages[idx - 1]
        return None

    def _active_fav_folder(self) -> str:
        page = self._active_fav()
        return page[0] if page else ""

    def _make_fav_page(self, folder: str):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        t = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(["代码", "名称", "命中日期", "选股条件"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.currentCellChanged.connect(
            lambda r, c, pr, pc, f=folder: self._on_fav_row(f, r))
        v.addWidget(t)
        return w, t

    def _refresh_fav_table(self, switch: bool = True):
        folders, items = _read_fav_data()
        cur = self._active_fav_folder() or self._last_fav_folder
        stay_on_results = self.tabs.currentIndex() == 0
        self._fav_busy = True
        while self.tabs.count() > 1:
            page = self.tabs.widget(1)
            self.tabs.removeTab(1)
            page.deleteLater()
        self._fav_pages = []
        for f in folders:
            fitems = [it for it in items if it.get("folder") == f]
            page, t = self._make_fav_page(f)
            for it in fitems:
                r = t.rowCount()
                t.insertRow(r)
                vals = (it.get("code", ""), it.get("name", ""),
                        it.get("date", ""), it.get("pattern_name", ""))
                for c, v in enumerate(vals):
                    item = QTableWidgetItem(str(v))
                    if c == 3 and it.get("pattern"):
                        item.setToolTip(json.dumps(it["pattern"],
                                                   ensure_ascii=False, indent=2))
                    t.setItem(r, c, item)
            t.clearSelection()
            self.tabs.addTab(page, f"{f} ({len(fitems)})")
            self._fav_pages.append((f, t))
        if cur not in folders:
            cur = folders[0]
        if switch or not stay_on_results:
            for i, (f, _t) in enumerate(self._fav_pages, start=1):
                if f == cur:
                    self.tabs.setCurrentIndex(i)
                    break
        else:
            self.tabs.setCurrentIndex(0)
        self._last_fav_folder = cur
        self._fav_busy = False

    def on_add_favorite(self):
        row = self.table.currentRow()
        if row < 0 or self.table.item(row, 0) is None:
            self.statusBar().showMessage("请先在「筛选结果」里选中一只个股")
            return
        code = self.table.item(row, 0).text()
        name = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        date = self.table.item(row, 2).text() if self.table.item(row, 2) else ""
        spec = None
        try:
            spec = json.loads(self.ed_dsl.toPlainText())
        except json.JSONDecodeError:
            pass
        pname = (spec or {}).get("name") or "自定义条件"
        folders, items = _read_fav_data()
        # 优先使用条件关联的收藏夹
        linked = self._get_linked_folder(pname)
        if linked and linked in folders:
            folder = linked
        else:
            folder = self._active_fav_folder() or self._last_fav_folder
            if folder not in folders:
                folder = folders[0]
            remembered = str(self.settings.value("fav_folder_remember", ""))
            if (self.settings.value("fav_folder_pinned") == "1"
                    and remembered in folders):
                folder = remembered
            else:
                dlg = FavFolderDialog(folders, folder,
                                      f"把 {name} {date}「{pname}」收藏到：", self)
                if dlg.exec() != QDialog.Accepted:
                    return
                folder = dlg.folder()
                if not folder:
                    return
                self.settings.setValue("fav_folder_pinned",
                                       "1" if dlg.ck_remember.isChecked() else "0")
                self.settings.setValue("fav_folder_remember", folder)
        if folder not in folders:
            folders.append(folder)
        self._last_fav_folder = folder
        for it in items:
            if (it.get("code") == code and it.get("date") == date
                    and it.get("pattern_name") == pname
                    and it.get("folder") == folder):
                self.statusBar().showMessage(
                    f"{name} {date}「{pname}」已在「{folder}」中")
                return
        items.append({"code": code, "name": name, "date": date,
                      "pattern_name": pname, "pattern": spec, "folder": folder,
                      "added": datetime.now().strftime("%Y-%m-%d %H:%M")})
        _write_fav_data(folders, items)
        self._refresh_fav_table(switch=False)
        self.statusBar().showMessage(f"已收藏到「{folder}」：{name} {date}「{pname}」")

    def _get_linked_folder(self, pattern_name: str) -> str:
        """获取条件关联的收藏夹，无关联返回空。"""
        d = _load_patterns_file()
        spec = d.get(pattern_name, {})
        return str(spec.get("fav_folder", ""))

    def on_add_favorite_batch(self):
        """把筛选结果中选中的多只个股批量收藏到同一收藏夹。"""
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            cur = self.table.currentRow()
            if cur >= 0:
                rows = [cur]
        if not rows:
            self.statusBar().showMessage("请先在「筛选结果」里选中要收藏的个股")
            return
        spec = None
        try:
            spec = json.loads(self.ed_dsl.toPlainText())
        except json.JSONDecodeError:
            pass
        pname = (spec or {}).get("name") or "自定义条件"
        folders, items = _read_fav_data()
        # 优先使用条件关联的收藏夹
        linked = self._get_linked_folder(pname)
        if linked and linked in folders:
            folder = linked
        else:
            folder = self._active_fav_folder() or self._last_fav_folder
            if folder not in folders:
                folder = folders[0]
            remembered = str(self.settings.value("fav_folder_remember", ""))
            if (self.settings.value("fav_folder_pinned") == "1"
                    and remembered in folders):
                folder = remembered
            else:
                dlg = FavFolderDialog(folders, folder,
                                      f"把选中的 {len(rows)} 条结果收藏到：", self)
                if dlg.exec() != QDialog.Accepted:
                    return
                folder = dlg.folder()
                if not folder:
                    return
                self.settings.setValue("fav_folder_pinned",
                                       "1" if dlg.ck_remember.isChecked() else "0")
                self.settings.setValue("fav_folder_remember", folder)
        if folder not in folders:
            folders.append(folder)
        self._last_fav_folder = folder
        existing = {(it.get("code"), it.get("date"),
                     it.get("pattern_name"), it.get("folder")) for it in items}
        added = dup = 0
        for r in rows:
            code = self.table.item(r, 0).text() if self.table.item(r, 0) else ""
            name = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            date = self.table.item(r, 2).text() if self.table.item(r, 2) else ""
            if not code:
                continue
            key = (code, date, pname, folder)
            if key in existing:
                dup += 1
                continue
            items.append({"code": code, "name": name, "date": date,
                          "pattern_name": pname, "pattern": spec,
                          "folder": folder,
                          "added": datetime.now().strftime("%Y-%m-%d %H:%M")})
            existing.add(key)
            added += 1
        if not added:
            self.statusBar().showMessage(
                f"选中的 {len(rows)} 条均已存在于「{folder}」")
            return
        _write_fav_data(folders, items)
        self._refresh_fav_table(switch=False)
        msg = f"已批量收藏 {added} 条到「{folder}」"
        if dup:
            msg += f"（{dup} 条重复跳过）"
        self.statusBar().showMessage(msg)

    def on_del_favorite(self):
        page = self._active_fav()
        if page is None:
            self.statusBar().showMessage("请先切换到某个收藏夹页签")
            return
        folder, t = page
        row = t.currentRow()
        folders, items = _read_fav_data()
        fitems = [it for it in items if it.get("folder") == folder]
        if row < 0 or row >= len(fitems):
            self.statusBar().showMessage("请先在当前收藏夹里选中一条")
            return
        it = fitems[row]
        items = [x for x in items
                 if not (x.get("code") == it.get("code")
                         and x.get("date") == it.get("date")
                         and x.get("pattern_name") == it.get("pattern_name")
                         and x.get("folder") == folder)]
        _write_fav_data(folders, items)
        self._refresh_fav_table()
        self.statusBar().showMessage(
            f"已从「{folder}」移除 {it.get('name', '')} {it.get('date', '')}")

    # ---------------- 收藏夹管理 ----------------
    def on_new_fav_folder(self):
        name, ok = QInputDialog.getText(self, "新建收藏夹", "收藏夹名称：")
        name = (name or "").strip()
        if not ok or not name:
            return
        folders, items = _read_fav_data()
        if name in folders:
            self.statusBar().showMessage(f"收藏夹「{name}」已存在")
            return
        folders.append(name)
        _write_fav_data(folders, items)
        self._last_fav_folder = name
        self._refresh_fav_table()
        self._select_fav_folder(name)
        self.statusBar().showMessage(f"已新建收藏夹「{name}」")

    def on_rename_fav_folder(self):
        old = self._active_fav_folder() or self._last_fav_folder
        if not old:
            self.statusBar().showMessage("请在收藏夹页签上右键重命名")
            return
        name, ok = QInputDialog.getText(self, "重命名收藏夹", "新名称：",
                                        text=old)
        name = (name or "").strip()
        if not ok or not name or name == old:
            return
        folders, items = _read_fav_data()
        if name in folders:
            self.statusBar().showMessage(f"收藏夹「{name}」已存在")
            return
        if old in folders:
            folders[folders.index(old)] = name
        for it in items:
            if it.get("folder") == old:
                it["folder"] = name
        _write_fav_data(folders, items)
        self._last_fav_folder = name
        self._refresh_fav_table()
        self._select_fav_folder(name)
        self.statusBar().showMessage(f"已重命名「{old}」→「{name}」")

    def on_delete_fav_folder(self):
        old = self._active_fav_folder()
        folders, items = _read_fav_data()
        if not old:
            self.statusBar().showMessage("请先切换到要删除的收藏夹页签")
            return
        if len(folders) <= 1:
            self.statusBar().showMessage("至少保留一个收藏夹")
            return
        n = sum(1 for it in items if it.get("folder") == old)
        ret = QMessageBox.question(
            self, "删除收藏夹",
            f"删除收藏夹「{old}」及其中 {n} 条收藏？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        folders = [f for f in folders if f != old]
        items = [it for it in items if it.get("folder") != old]
        _write_fav_data(folders, items)
        self._last_fav_folder = folders[0]
        self._refresh_fav_table()
        self.statusBar().showMessage(f"已删除收藏夹「{old}」")

    def _select_fav_folder(self, name: str):
        for i, (f, _t) in enumerate(self._fav_pages, start=1):
            if f == name:
                self.tabs.setCurrentIndex(i)
                self._last_fav_folder = name
                return

    def _on_fav_tab_menu(self, pos):
        idx = self.tabs.tabBar().tabAt(pos)
        if idx <= 0:
            return
        self.tabs.setCurrentIndex(idx)
        menu = QMenu(self)
        menu.addAction("重命名收藏夹", self.on_rename_fav_folder)
        menu.addAction("删除收藏夹", self.on_delete_fav_folder)
        menu.exec(self.tabs.tabBar().mapToGlobal(pos))

    def on_load_fav_pattern(self):
        page = self._active_fav()
        if page is None:
            self.statusBar().showMessage("请先切换到某个收藏夹页签")
            return
        folder, t = page
        row = t.currentRow()
        folders, items = _read_fav_data()
        fitems = [it for it in items if it.get("folder") == folder]
        if row < 0 or row >= len(fitems):
            self.statusBar().showMessage("请先在当前收藏夹里选中一条")
            return
        spec = fitems[row].get("pattern")
        if not spec:
            self.statusBar().showMessage("该收藏没有保存条件")
            return
        self.ed_dsl.setPlainText(json.dumps(spec, ensure_ascii=False, indent=2))
        self.statusBar().showMessage(
            f"已载入条件「{spec.get('name', '')}」，可直接点「开始筛选」")

    def _on_fav_row(self, folder: str, row: int):
        if self._fav_busy or row < 0:
            return
        folders, items = _read_fav_data()
        fitems = [it for it in items if it.get("folder") == folder]
        if row >= len(fitems):
            return
        it = fitems[row]
        self._last_fav_folder = folder
        self._load_day_chart(it.get("code", ""), it.get("name", ""),
                             it.get("date", ""))
        self.statusBar().showMessage(
            f"{it.get('name', '')} {it.get('code', '')}  "
            f"命中日 {it.get('date', '')}  条件「{it.get('pattern_name', '')}」"
            f"  〔{folder}〕")

    def on_row_double_clicked(self, _item):
        self.chart.setFocus()

    # ---------------- 图表模式切换 ----------------
    def _set_chart_bg(self, hex_color: str):
        self.chart.set_bg(hex_color)
        self.settings.setValue("chart_bg", hex_color)
        self.settings.setValue("chart_bg_auto", "0")   # 手动选色后不再随主题
        self._sync_appearance()

    def _pick_bg(self):
        c = QColorDialog.getColor(
            QColor(self.settings.value("chart_bg", "#0f1115")),
            self, "选择K线背景色")
        if c.isValid():
            self._set_chart_bg(c.name())

    def _ref_info(self):
        """参考日（揭示日）与昨收。日K模式下实时取自图表并记住。"""
        if self.chart.mode() == "day":
            d = self.chart.current_reveal_date()
            if d:
                self._ref_date = d
                self._ref_pc = self.chart.prev_close_of_reveal()
        return self._ref_date, self._ref_pc

    def _current_code_name(self):
        page = self._active_fav()
        if page is not None:
            t = page[1]
            row = t.currentRow()
            if 0 <= row < t.rowCount():
                c0, c1 = t.item(row, 0), t.item(row, 1)
                if c0:
                    return c0.text(), (c1.text() if c1 else "")
        row = self.table.currentRow()
        if row < 0:
            return "", ""
        return self.table.item(row, 0).text(), self.table.item(row, 1).text()

    def show_day(self):
        if self.chart.mode() == "day":
            return
        code, name = self._current_code_name()
        if not code:
            return
        md = self.chart.match_date() or self._ref_date
        self._load_day_chart(code, name, md)

    def reset_chart(self):
        """重置：最新K线回到命中日（从分钟图/分时回到日K时同样适用）。"""
        code, name = self._current_code_name()
        if not code:
            return
        md = self.chart.match_date()
        if self.chart.mode() != "day" and md:
            self._load_day_chart(code, name, md)
        self.chart.reset_to_match()
        self._ref_date = self.chart.current_reveal_date()
        self._ref_pc = self.chart.prev_close_of_reveal()
        if self._ref_date:
            self.statusBar().showMessage(f"已重置到命中日 {self._ref_date}")
        self._sync_mode_buttons()

    # ---------------- 画线 / 均线 / 指数叠加 ----------------
    def _on_clear_drawings(self):
        self.chart.clear_drawings()
        code = self._current_code_name()[0]
        if code:
            _save_drawings(code, [])
        self.statusBar().showMessage("已清除当前个股的全部标注")

    def _migrate_palette(self):
        """旧版默认色偏荧光，升级后一次性清掉旧配色存储，套用柔和新配色。"""
        if str(self.settings.value("palette_v", "")) == PALETTE_VERSION:
            return
        for key in ("ma_colors", "chart_up", "chart_down",
                    "chart_draw_colors", "rr_profit_color",
                    "rr_loss_color", "rr_entry_color", "text_color"):
            self.settings.remove(key)
        self.settings.setValue("palette_v", PALETTE_VERSION)

    def _apply_chart_colors(self):
        saved_up = self.settings.value("chart_up")
        saved_down = self.settings.value("chart_down")
        if saved_up and saved_down:
            self.chart.set_updown_colors(str(saved_up), str(saved_down))
        try:
            saved = json.loads(
                self.settings.value("chart_draw_colors", "{}") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict):
            self.chart.set_draw_colors(saved)

    def _open_color_dialog(self):
        """统一的K线配色设置：涨跌、均线、划线、盈亏比区。"""
        periods, ma_colors = self.chart.ma_config()
        up, down = self.chart.updown_colors()
        dlg = ColorSchemeDialog(up, down, list(zip(periods, ma_colors)),
                                self.chart.draw_colors(),
                                self.chart.rr_colors(), self)
        if dlg.exec() != QDialog.Accepted:
            return
        res = dlg.result_colors()
        apply_sel = dlg.apply_selected()
        self.chart.set_updown_colors(res["up"], res["down"])
        self.chart.set_draw_colors(res, apply_selected=apply_sel)
        new_colors = [res[f"ma:{p}"] for p in periods]
        self.chart.set_ma_config(periods, new_colors)
        self.chart.set_rr_colors(res["profit_color"], res["loss_color"],
                                 res["entry_color"], apply_selected=apply_sel)
        # 文字标注颜色与工具栏色块保持一致
        self._text_color_hex = res["text"]
        self._update_color_btn()
        self.chart.set_text_style(self.cb_font.currentFont().family(),
                                  self.sp_font.value(), res["text"])
        # 持久化
        self.settings.setValue("chart_up", res["up"])
        self.settings.setValue("chart_down", res["down"])
        self.settings.setValue("chart_draw_colors", json.dumps(
            {k: res[k] for k in ("line", "hline", "ray", "percent",
                                 "fib", "rr", "text")},
            ensure_ascii=False))
        self.settings.setValue("ma_colors", json.dumps(new_colors))
        self.settings.setValue("text_color", res["text"])
        for key in ("profit_color", "loss_color", "entry_color"):
            self.settings.setValue(f"rr_{key}", res[key])
        self._update_rr_buttons()
        self.statusBar().showMessage("K线配色已更新")

    def _open_ma_dialog(self):
        periods, colors = self.chart.ma_config()
        dlg = MaConfigDialog(periods, colors, self.chart.ma_width(), self)
        if dlg.exec() == QDialog.Accepted:
            periods, colors = dlg.result_config()
            width = dlg.result_width()
            if periods:
                self.chart.set_ma_config(periods, colors, width)
                self.settings.setValue("ma_periods",
                                       json.dumps(periods))
                self.settings.setValue("ma_colors",
                                       json.dumps(colors))
                self.settings.setValue("ma_width", width)
                self.statusBar().showMessage(
                    f"均线已更新：{','.join(str(p) for p in periods)}")

    def _apply_ma_config(self):
        try:
            periods = json.loads(self.settings.value("ma_periods", "[]"))
            colors = json.loads(self.settings.value("ma_colors", "[]"))
            width = float(self.settings.value("ma_width", 1.2))
        except (TypeError, ValueError, json.JSONDecodeError):
            periods, colors, width = [], [], 1.2
        if not periods:
            periods, colors = [5, 10, 20, 30], None
        self.chart.set_ma_config(periods, colors or None, width)

    def _on_index_changed(self, idx: int):
        if idx <= 0 or self._loading_boards:
            if idx == 0:
                self.chart.clear_overlay()
            return
        spec = self.cb_index.itemData(idx)
        if not spec:
            return
        if spec[0] == "none":
            self.chart.clear_overlay()
            return
        if spec[0] == "load_boards":
            self._load_boards(idx)
            return
        kind, code, name, secid = spec
        df = self._overlay_cache.get(secid)
        if df is None:
            self.statusBar().showMessage(f"加载 {name} 日K数据中…")
            try:
                beg = "19900101"
                end = datetime.now().strftime("%Y%m%d")
                df = data.fetch_kline(code, beg, end, secid=secid)
            except Exception as e:  # noqa: BLE001
                self.statusBar().showMessage(f"{name} 加载失败：{e}")
                self.chart.clear_overlay()
                return
            if df is None or df.empty:
                self.statusBar().showMessage(f"{name} 数据加载失败")
                self.chart.clear_overlay()
                return
            self._overlay_cache[secid] = df
        self.chart.set_overlay(code, name, df, kind)
        self.statusBar().showMessage(f"已叠加 {name} 日K（橙/蓝蜡烛）")

    def _load_boards(self, idx: int):
        """加载东财行业板块列表并填充下拉，随后叠加第一个板块。"""
        self.statusBar().showMessage("加载板块列表中…")
        boards = data.fetch_board_list()
        if not boards:
            self.statusBar().showMessage("板块列表加载失败，请检查网络后重试")
            self._loading_boards = True
            self.cb_index.setCurrentIndex(0)
            self._loading_boards = False
            return
        self._loading_boards = True
        self.cb_index.removeItem(idx)
        for code, name in boards:
            self.cb_index.addItem(f"板块：{name}",
                                  ("board", code, name, f"90.{code}"))
        self._loading_boards = False
        self.cb_index.setCurrentIndex(min(idx, self.cb_index.count() - 1))
        self.statusBar().showMessage(f"已加载 {len(boards)} 个行业板块")

    # ---------------- 文字标注样式 ----------------
    def _on_text_style_changed(self, *_args):
        self.chart.set_text_style(self.cb_font.currentFont().family(),
                                  self.sp_font.value(),
                                  self._text_color_hex)
        self.settings.setValue("text_font_family",
                               self.cb_font.currentFont().family())
        self.settings.setValue("text_font_size", self.sp_font.value())
        self.settings.setValue("text_color", self._text_color_hex)

    def _pick_text_color(self):
        c = QColorDialog.getColor(QColor(self._text_color_hex), self,
                                  "文字颜色")
        if c.isValid():
            self._text_color_hex = c.name()
            self._update_color_btn()
            self._on_text_style_changed()

    def _update_color_btn(self):
        self.btn_font_color.setStyleSheet(
            f"background:{self._text_color_hex};color:"
            f"{'#000' if QColor(self._text_color_hex).lightness() > 150 else '#fff'};")

    def _apply_text_style(self):
        fam = str(self.settings.value("text_font_family", ""))
        size = int(self.settings.value("text_font_size", 12))
        color = str(self.settings.value("text_color", DRAW_COLORS["text"]))
        self._text_color_hex = color
        if fam:
            self.cb_font.setCurrentFont(QFont(fam))
        self.sp_font.setValue(max(8, min(48, size)))
        self._update_color_btn()
        self.chart.set_text_style(self.cb_font.currentFont().family(),
                                  self.sp_font.value(), color)

    def _pick_rr_zone_color(self, key: str, title: str):
        """单独设置某一个盈亏比区的颜色。"""
        current = self.chart.rr_colors()
        color = QColorDialog.getColor(QColor(current[key]), self, f"设置{title}")
        if not color.isValid():
            return
        values = dict(current)
        values[key] = color.name()
        self.chart.set_rr_colors(values["profit_color"], values["loss_color"],
                                 values["entry_color"], apply_selected=True)
        self.settings.setValue(f"rr_{key}", color.name())
        self._update_rr_buttons()
        self.statusBar().showMessage(f"{title}已更新")

    def _update_rr_buttons(self):
        colors = self.chart.rr_colors()
        for key, btn in getattr(self, "_rr_btns", {}).items():
            btn.setStyleSheet(
                f"background:{colors.get(key, '#888888')};"
                "border:1px solid #6b7280;border-radius:4px;")

    def _apply_rr_colors(self):
        current = self.chart.rr_colors()
        values = {key: str(self.settings.value(f"rr_{key}", default))
                  for key, default in current.items()}
        self.chart.set_rr_colors(values["profit_color"], values["loss_color"],
                                 values["entry_color"])
        self._update_rr_buttons()

    def show_trend(self):
        ref, pc = self._ref_info()
        code, name = self._current_code_name()
        if not ref or not code:
            return
        self._start_min_fetch("trend", code, name,
                              f"{ref} 09:00:00", f"{ref} 15:30:00",
                              date_str=ref, prev_close=pc)

    def show_min(self, klt: int):
        # 以当前K线（十字光标）为准；无光标则用揭示日
        ref = self.chart.cursor_date() or self._ref_info()[0]
        self._open_min(klt, ref)

    def open_min_at(self, date: str):
        """双击K线 / 回车：打开该日的5分钟图。"""
        if not date:
            return
        self._ref_date = date
        self._open_min(5, date)

    def _open_min(self, klt: int, ref: str):
        code, name = self._current_code_name()
        if not code:
            return
        # 命中日前后各 60 天：前 60 天靠新浪回退源补，后 60 天东财就能覆盖
        end = datetime.now()
        beg = end - timedelta(days=14 if klt == 1 else 120)
        if ref:
            try:
                ref_dt = datetime.strptime(ref, "%Y-%m-%d")
            except ValueError:
                ref_dt = end
            # 确保命中日前后各 60 天都在区间里
            beg = min(beg, ref_dt - timedelta(days=60))
            end = max(end, ref_dt + timedelta(days=3))
        self._start_min_fetch(f"min{klt}", code, name,
                              beg.strftime("%Y-%m-%d 09:00:00"),
                              end.strftime("%Y-%m-%d 15:30:00"),
                              date_str=ref)

    def _start_min_fetch(self, mode: str, code: str, name: str,
                         beg: str, end: str,
                         date_str: str = "", prev_close: float = 0.0):
        if self._min_thread and self._min_thread.isRunning():
            return
        # 切到分钟/分时后，迟到的日K结果不再覆盖当前图
        self._day_want = ""
        for b in (self.btn_trend, self.btn_m1, self.btn_m5):
            b.setEnabled(False)
        self.statusBar().showMessage(f"加载 {name} 分钟数据…")
        self._min_thread = MinFetchThread(code, mode, beg, end,
                                          date_str, prev_close, self)
        self._min_thread.done.connect(self._on_min_done)
        self._min_thread.failed.connect(self._on_min_failed)
        self._min_thread.start()

    def _on_min_done(self, mode: str, df, code: str,
                     date_str: str, prev_close: float):
        for b in (self.btn_trend, self.btn_m1, self.btn_m5):
            b.setEnabled(True)
        name_item = self.table.item(self.table.currentRow(), 1)
        name = name_item.text() if name_item else ""
        if mode == "trend":
            self.chart.set_trend_data(code, name, df, prev_close, date_str)
            self.statusBar().showMessage(f"{name} {date_str} 分时")
        else:
            klt = 5 if mode == "min5" else 1
            self.chart.set_min_data(code, name, klt, df, anchor_date=date_str)
            if date_str and self.chart.anchor_index() < 0:
                self.statusBar().showMessage(
                    f"{name} {klt}分钟：接口仅提供近期数据，{date_str} 无分钟数据；"
                    f"已显示最近区间")
            else:
                self.statusBar().showMessage(
                    f"{name} {klt}分钟：右缘已对齐 {date_str or '最新'}，"
                    f"滚轮缩放查看跨日历史，Esc 返回日K")
        self._sync_mode_buttons()

    def _on_min_failed(self, msg: str):
        for b in (self.btn_trend, self.btn_m1, self.btn_m5):
            b.setEnabled(True)
        self.statusBar().showMessage(f"分钟数据加载失败：{msg}")

    # ---------------- 数据导出 / 导入 ----------------
    def _export_data(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出数据", "klinepicker_backup.json", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            data = {
                "version": 1,
                "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "favorites": dict(zip(("folders", "items"), _read_fav_data())),
                "patterns": _load_patterns_file(),
                "drawings": {},
            }
            # 导出所有画线标注
            ddir = _drawings_dir()
            if os.path.isdir(ddir):
                for fn in os.listdir(ddir):
                    if fn.endswith(".json"):
                        code = fn[:-5]
                        data["drawings"][code] = _load_drawings(code)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"数据已导出：{path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(e))

    def _import_data(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入数据", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("文件格式不正确")
            # 导入收藏夹
            fav = data.get("favorites")
            if fav and isinstance(fav, dict):
                folders = fav.get("folders", [DEFAULT_FAV_FOLDER])
                items = fav.get("items", [])
                _write_fav_data(folders, items)
                self._refresh_fav_table()
            # 导入条件
            pats = data.get("patterns")
            if pats and isinstance(pats, dict):
                d = _load_patterns_file()
                d.update(pats)
                _save_patterns_file(d)
                self._refresh_patterns()
            # 导入画线标注
            drawings = data.get("drawings")
            if drawings and isinstance(drawings, dict):
                for code, items in drawings.items():
                    _save_drawings(code, items)
                # 刷新当前股票的标注
                cur = self._current_code_name()[0]
                if cur:
                    self.chart.set_drawings(_load_drawings(cur))
            self.statusBar().showMessage("数据导入完成")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))

    def closeEvent(self, event):
        self._save_all_notes()
        self.settings.setValue("win_geometry", self.saveGeometry())
        self.settings.setValue("splitter_state", self._splitter.saveState())
        if self._cur_draw_code:
            _save_drawings(self._cur_draw_code, self.chart.drawings())
        if self._screener and self._screener.isRunning():
            self._screener.stop()
            self._screener.wait(3000)
        if self._probe is not None and self._probe.isRunning():
            # 交易日探测是短请求，最多等 4 秒，避免线程未结束就被销毁
            self._probe.wait(4000)
        if self._day_thread is not None and self._day_thread.isRunning():
            # 日K请求最长约 10 秒，先等一会儿，仍未结束就强制收掉，避免退出崩溃
            self._day_thread.wait(3000)
            if self._day_thread.isRunning():
                self._day_thread.terminate()
                self._day_thread.wait(500)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 9))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
