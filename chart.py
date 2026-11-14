"""自绘 K 线图组件：日K/分钟K/分时，支持缩放/平移/逐日揭示。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QLineEdit, QWidget

BG = QColor("#0f1115")
GRID = QColor("#232834")
DAYBREAK = QColor("#3a4250")
TEXT = QColor("#8b93a3")
TITLE = QColor("#dfe4ec")
# 柔和红绿：饱和度略降，长时间盯盘不刺眼，同时保持涨跌区分度
UP = QColor("#e2575f")
DOWN = QColor("#2fa88a")
MATCH = QColor("#ff5a52")
PRICE_LINE = QColor("#cfd6e4")
AVG_LINE = QColor("#e0b153")
PREV_CLOSE_C = QColor("#5f6673")
# 均线默认配色：暖金 / 蓝 / 紫 / 青，明度接近，互不抢视线
MA_COLORS = {"ma5": QColor("#e6c07b"), "ma10": QColor("#6cb6ff"),
             "ma20": QColor("#c58fd6"), "ma30": QColor("#59b8c4")}
# 各类划线默认配色（低饱和、彼此易区分）
DRAW_COLORS = {
    "line": "#d99a5e",      # 直线：暖橙
    "hline": "#94c47a",     # 水平线：柔和绿
    "ray": "#59b8c4",       # 射线：青
    "percent": "#c58fd6",   # 百分位线：紫
    "fib": "#e6c07b",       # 黄金分割：金
    "rr": "#aeb6c4",        # 盈亏比区外框：中性灰
    "text": "#e6e9ef",      # 文字标注
}
# 盈亏比区三区默认配色：盈利红、建仓灰、亏损绿
RR_COLORS = {"profit_color": "#d94b4b", "loss_color": "#2f9e6f",
             "entry_color": "#9096a0"}
# 浅色背景下的配色（同色系加深，保证对比度）
UP_LIGHT = QColor("#c9464f")
DOWN_LIGHT = QColor("#1f8a70")
DRAW_COLORS_LIGHT = {
    "line": "#b5621f",
    "hline": "#2f7d3a",
    "ray": "#16707c",
    "percent": "#7b3fa0",
    "fib": "#9a6a05",
    "rr": "#5a6472",
    "text": "#2a2f3a",
}
AXIS_W = 64
DATE_H = 22
MIN_BARS = 5.0


class KLineChart(QWidget):
    activated = Signal(str)   # 双击某根日K → 该日期（YYYY-MM-DD）
    draw_mode_changed = Signal(str)   # 画线工具变化（含画完自动回到 select）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._mode = "day"           # day / min1 / min5 / trend
        self._code = ""
        self._name = ""
        self._dates: list = []
        self._o = self._h = self._l = self._c = self._v = None
        self._amt = None
        self._mas: dict = {}
        self._pct = None
        self._reveal_end = -1        # 日K模式：已揭示到的最后一根 bar（含）
        self._match_idx = -1
        self._match_date = ""        # 命中日（日K设置后跨模式保留）
        self._bars_visible = 80.0
        self._left_f = 0.0
        self._drag_x = None
        self._drag_left = 0.0
        self._draw_drag = None         # 选择模式下拖动标注的状态
        self._prev_close = 0.0       # 分时模式：昨收
        self._trend_date = ""
        self._day_breaks: list = []  # 分钟模式：每日首根 bar (索引, 日期)
        self._bg = BG
        self._aux = dict(grid=GRID, daybreak=DAYBREAK, text=TEXT, title=TITLE,
                         price=PRICE_LINE, avg=AVG_LINE, prev=PREV_CLOSE_C)
        self._ma_colors = dict(MA_COLORS)
        self._up_color = QColor(UP)
        self._down_color = QColor(DOWN)
        self._cursor = -1          # 光标 bar 索引（-1=关闭）
        self._hover_y = None       # 十字光标横线 y（None=不显示）
        self._to = None            # 换手率
        # 均线自定义（周期与颜色）
        self._ma_periods: list[int] = [5, 10, 20, 30]
        self._ma_colors_custom: dict | None = None  # 周期->QColor
        self._ma_width = 1.2            # 均线线宽
        # 画线/文字标注
        self._draw_mode = "select"      # select / line / text
        self._draw_width = 1.5          # 新建标注的默认线宽
        self._drawings: list = []       # [{type,p1,p2,color,text}]
        self._draw_colors = dict(DRAW_COLORS)   # 各类划线默认颜色
        self._draw_colors_custom: dict | None = None  # 用户自定义划线颜色
        self._updown_custom: tuple | None = None      # 用户自定义涨跌色
        self._is_light = False          # 当前背景是否浅色
        self._drawing = None            # 正在绘制中的临时项
        self._sel_drawing = -1          # 选中的标注索引
        self._rr_step = 0               # 盈亏比区绘制步骤：0=未开始 1=止损 2=止盈 3=建仓
        self._rr_draft = None           # 盈亏比区草稿
        self._rr_entry_anchor = None    # 建仓区拖动起点价格
        self._rr_colors = dict(RR_COLORS)
        # 文字样式（新建标注使用）
        self._text_family = ""
        self._text_size = 12
        self._text_color = DRAW_COLORS["text"]
        self._text_edit: QLineEdit | None = None
        self._text_edit_ctx = None      # (bar, price, drawing_idx|None)
        self._text_pending = None       # 按下文字标注但尚未拖动（松开即编辑）
        # 叠加品种（指数/板块日K）
        self._overlay: dict | None = None  # {code,name,kind,bars:{date:(o,h,l,c)}}

    def set_bg(self, color):
        """设置背景色；网格/文字/划线等辅助色按明暗自动适配。"""
        self._bg = QColor(color)
        lum = 0.299 * self._bg.red() + 0.587 * self._bg.green() + 0.114 * self._bg.blue()
        self._is_light = lum > 127
        if self._is_light:
            self._aux = dict(grid=QColor("#dcdcdc"), daybreak=QColor("#c5c5c5"),
                             text=QColor("#666666"), title=QColor("#222222"),
                             price=QColor("#1a1a1a"), avg=QColor("#cc8f00"),
                             prev=QColor("#999999"))
            # 浅色背景用深一档的均线色，避免浅色线看不见
            self._ma_colors = {"ma5": QColor("#3d4756"),
                               "ma10": QColor("#a8761b"),
                               "ma20": QColor("#8b4fa8"),
                               "ma30": QColor("#23798a")}
        else:
            self._aux = dict(grid=GRID, daybreak=DAYBREAK, text=TEXT, title=TITLE,
                             price=PRICE_LINE, avg=AVG_LINE, prev=PREV_CLOSE_C)
            self._ma_colors = dict(MA_COLORS)
        # 划线/涨跌默认色随背景切换；用户自定义过则保持不变
        if self._draw_colors_custom is None:
            self._draw_colors = dict(DRAW_COLORS_LIGHT if self._is_light
                                     else DRAW_COLORS)
        if self._updown_custom is None:
            self._up_color = QColor(UP_LIGHT if self._is_light else UP)
            self._down_color = QColor(DOWN_LIGHT if self._is_light else DOWN)
        # 文字默认色跟随背景明暗（自定义过则不动）
        if self._is_light and self._text_color.lower() == DRAW_COLORS["text"]:
            self._text_color = DRAW_COLORS_LIGHT["text"]
        elif (not self._is_light
              and self._text_color.lower() == DRAW_COLORS_LIGHT["text"]):
            self._text_color = DRAW_COLORS["text"]
        self.update()

    # ---------------- 均线配置 ----------------
    def set_ma_config(self, periods: list[int], colors: list[str] | None = None,
                      width: float | None = None):
        """设置均线周期、颜色与线宽。colors 为 hex 列表，长度需与 periods 一致；
        为 None 时使用默认配色。"""
        self._ma_periods = [int(p) for p in periods if int(p) > 0]
        if width is not None:
            self._ma_width = max(0.5, float(width))
        if colors and len(colors) == len(self._ma_periods):
            self._ma_colors_custom = {p: QColor(c) for p, c in
                                      zip(self._ma_periods, colors)}
        else:
            self._ma_colors_custom = None
        # 已有数据时重算均线
        if self._c is not None and self._mode == "day":
            self._mas = self._compute_mas(self._c)
        self.update()

    def ma_config(self):
        """返回 (periods, colors_hex)。"""
        colors = []
        for p in self._ma_periods:
            c = (self._ma_colors_custom.get(p) if self._ma_colors_custom
                 else self._ma_colors.get(f"ma{p}"))
            colors.append(c.name() if c is not None else "#ffffff")
        return list(self._ma_periods), colors

    def ma_width(self) -> float:
        return self._ma_width

    def set_ma_width(self, width: float):
        self._ma_width = max(0.5, float(width))
        self.update()

    # ---------------- 标注线宽 ----------------
    def draw_width(self) -> float:
        return self._draw_width

    def set_draw_width(self, width: float, apply_selected: bool = False):
        """设置新建标注的线宽；apply_selected 时同时修改已选中的标注。"""
        self._draw_width = max(0.5, float(width))
        if (apply_selected and 0 <= self._sel_drawing < len(self._drawings)):
            self._drawings[self._sel_drawing]["width"] = self._draw_width
        self.update()

    def _compute_mas(self, close) -> dict:
        """按当前 _ma_periods 从收盘价计算均线数组。"""
        out = {}
        arr = np.asarray(close, float)
        for p in self._ma_periods:
            if p <= 0:
                continue
            if p > arr.size:
                out[f"ma{p}"] = np.full(arr.size, np.nan)
            else:
                out[f"ma{p}"] = pd.Series(arr).rolling(p).mean().to_numpy()
        return out

    def _ma_color(self, mname: str) -> QColor:
        p = int(mname[2:])
        if self._ma_colors_custom and p in self._ma_colors_custom:
            return self._ma_colors_custom[p]
        return self._ma_colors.get(mname, QColor("#ffffff"))

    # ---------------- 数据 ----------------
    def set_data(self, code: str, name: str, df, match_date: str):
        """日K模式：截至命中日，空格逐日揭示。"""
        self._finish_text_edit()
        self._mode = "day"
        self._code, self._name = code, name
        self._load_ohlc(df)
        self._mas = self._compute_mas(self._c)
        try:
            self._match_idx = self._dates.index(match_date)
        except ValueError:
            self._match_idx = len(self._dates) - 1
        self._match_date = (str(match_date)[:10]
                            if 0 <= self._match_idx < len(self._dates) else "")
        self._reveal_end = self._match_idx
        self._bars_visible = float(min(80, max(20, self._match_idx + 1)))
        self._left_f = max(0.0, self._match_idx - self._bars_visible + 1)
        self.update()

    def set_min_data(self, code: str, name: str, klt: int, df,
                     anchor_date: str = ""):
        """分钟K模式：跨日历史分钟线。

        anchor_date 非空时，把视图右缘定位到该日的最后一根 bar（即命中/揭示日），
        否则定位到最新的 bar。
        """
        self._mode = f"min{klt}"
        self._code, self._name = code, name
        self._load_ohlc(df)
        self._reveal_end = len(self._dates) - 1
        prev = ""
        for i, d in enumerate(self._dates):
            day = str(d)[:10]
            if day != prev:
                self._day_breaks.append((i, day))
                prev = day
        self._bars_visible = float(min(240, max(60, len(self._dates))))
        self._left_f = max(0.0, len(self._dates) - self._bars_visible)
        self._cursor = -1
        self._anchor_idx = -1
        if anchor_date:
            for i, d in enumerate(self._dates):
                if str(d)[:10] <= anchor_date:
                    self._anchor_idx = i
            if self._anchor_idx >= 0:
                self._cursor = self._anchor_idx
                self._left_f = max(0.0, self._anchor_idx + 1 - self._bars_visible)
                self._clamp_view()
        self.update()

    def anchor_index(self) -> int:
        """分钟模式下命中/揭示日对应的 bar 索引（-1=该日不在数据范围内）。"""
        return getattr(self, "_anchor_idx", -1)

    def date_at(self, idx: int) -> str:
        if 0 <= idx < len(self._dates):
            return str(self._dates[idx])
        return ""

    def set_trend_data(self, code: str, name: str, df,
                       prev_close: float, date_str: str):
        """分时模式：单日价格线 + 均价线。"""
        self._mode = "trend"
        self._code, self._name = code, name
        self._load_ohlc(df)
        self._reveal_end = len(self._dates) - 1
        self._prev_close = prev_close
        self._trend_date = date_str
        self._bars_visible = float(max(len(self._dates), 2))
        self._left_f = 0.0
        self.update()

    def _load_ohlc(self, df):
        self._dates = list(df["date"])
        self._o = df["open"].to_numpy(float)
        self._h = df["high"].to_numpy(float)
        self._l = df["low"].to_numpy(float)
        self._c = df["close"].to_numpy(float)
        self._v = df["volume"].to_numpy(float)
        self._amt = df["amount"].to_numpy(float) if "amount" in df else None
        self._pct = df["pct"].to_numpy(float) if "pct" in df else None
        self._to = df["turnover"].to_numpy(float) if "turnover" in df else None
        self._mas = {}
        self._day_breaks = []
        self._match_idx = -1
        self._cursor = -1

    def clear(self):
        self._dates = []
        self.update()

    def mode(self) -> str:
        return self._mode

    def match_date(self) -> str:
        return self._match_date

    def current_reveal_date(self) -> str:
        """日K模式下当前揭示到的日期（YYYY-MM-DD）。"""
        if self._mode == "day" and 0 <= self._reveal_end < len(self._dates):
            return str(self._dates[self._reveal_end])[:10]
        return ""

    def cursor_date(self) -> str:
        """日K模式下光标所在日期；无光标时退回揭示日。"""
        if self._mode != "day":
            return ""
        if 0 <= self._cursor < len(self._dates):
            return str(self._dates[self._cursor])[:10]
        return self.current_reveal_date()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            # 选择模式下双击文字标注：就地编辑
            if self._draw_mode == "select" and self._drawings:
                idx = self._hit_drawing(e.position().x(), e.position().y())
                if idx >= 0 and self._drawings[idx].get("type") == "text":
                    d = self._drawings[idx]
                    self._begin_text_edit(d["p1"][0], d["p1"][1], idx)
                    return
            d = self.cursor_date()
            if d:
                self.activated.emit(d)
        super().mouseDoubleClickEvent(e)

    def prev_close_of_reveal(self) -> float:
        """揭示日前一交易日收盘价（分时图昨收基准）。"""
        if self._mode == "day" and 0 < self._reveal_end < len(self._c):
            return float(self._c[self._reveal_end - 1])
        return 0.0

    def fit_all(self):
        """缩放至全部可见历史（日K为已揭示范围）。"""
        if not self._dates:
            return
        self._bars_visible = float(max(self._view_count(), MIN_BARS))
        self._clamp_view()
        self.update()

    # ---------------- 坐标辅助（供鼠标交互与标注使用） ----------------
    def _visible_range(self):
        """返回当前可视区 (i0, i1, bw, pr, vr, lo, hi)。"""
        pr, vr = self._price_pane(), self._vol_pane()
        right = self._right()
        bw = pr.width() / self._bars_visible
        i0 = max(0, int(math.floor(self._left_f)))
        i1 = min(right, int(math.ceil(self._left_f + self._bars_visible)))
        if i1 < i0 or self._l is None:
            return i0, i1, bw, pr, vr, 0.0, 1.0
        lo = float(np.nanmin(self._l[i0:i1 + 1]))
        hi = float(np.nanmax(self._h[i0:i1 + 1]))
        for arr in self._mas.values():
            seg = arr[i0:i1 + 1]
            seg = seg[~np.isnan(seg)]
            if seg.size:
                lo = min(lo, float(seg.min()))
                hi = max(hi, float(seg.max()))
        pad = max((hi - lo) * 0.06, 1e-6)
        lo, hi = lo - pad, hi + pad
        return i0, i1, bw, pr, vr, lo, hi

    def _bar_at_x(self, x_px: float) -> int:
        """像素 → bar 索引。右侧留白区也可落点（用于在空白处画线/写文字）。"""
        _, _, bw, pr, _, _, _ = self._visible_range()
        if bw <= 0:
            return -1
        i = int(round(self._left_f + (x_px - pr.left()) / bw - 0.5))
        return min(max(i, 0), self._view_last_bar())

    def _view_last_bar(self) -> int:
        """当前视图最右侧仍可见的 bar 索引（可超出已揭示的最后一根）。"""
        return max(self._right(),
                   int(math.floor(self._left_f + self._bars_visible - 0.5)))

    def _price_at_y(self, y_px: float) -> float:
        _, _, _, pr, _, lo, hi = self._visible_range()
        return lo + (pr.bottom() - y_px) / pr.height() * (hi - lo)

    # ---------------- 画线 / 文字标注 ----------------
    def set_draw_mode(self, mode: str):
        changed = mode != self._draw_mode
        if changed:
            self._finish_text_edit()
        self._draw_mode = mode
        self._drawing = None
        self._draw_drag = None
        self._text_pending = None
        self._sel_drawing = -1
        self._rr_step = 0           # 盈亏比区绘制步骤
        self._rr_draft = None       # 盈亏比区草稿
        self._rr_entry_anchor = None
        cursor_map = {
            "select": Qt.ArrowCursor,
            "line": Qt.CrossCursor,
            "hline": Qt.CrossCursor,
            "ray": Qt.CrossCursor,
            "percent": Qt.CrossCursor,
            "fib": Qt.CrossCursor,
            "rr": Qt.CrossCursor,
            "text": Qt.IBeamCursor,
        }
        self.setCursor(cursor_map.get(mode, Qt.ArrowCursor))
        if changed:
            self.draw_mode_changed.emit(mode)
        self.update()

    def _return_to_select(self):
        """一步操作画完后自动回到选择模式（保留刚画好的标注选中态）。"""
        if self._draw_mode == "select":
            return
        self._draw_mode = "select"
        self._drawing = None
        self._draw_drag = None
        self._text_pending = None
        self._rr_step = 0
        self._rr_draft = None
        self._rr_entry_anchor = None
        self.setCursor(Qt.ArrowCursor)
        self.draw_mode_changed.emit("select")
        self.update()

    def draw_mode(self) -> str:
        return self._draw_mode

    def set_drawings(self, drawings: list):
        self._finish_text_edit()
        self._drawings = list(drawings)
        self._drawing = None
        self._draw_drag = None
        self._rr_draft = None
        self._rr_step = 0
        self._sel_drawing = -1
        self.update()

    def drawings(self) -> list:
        return list(self._drawings)

    def is_text_editing(self) -> bool:
        """K线图上是否正在就地输入文字（用于屏蔽全局快捷键）。"""
        return self._text_edit is not None

    # ---------------- 配色 ----------------
    def updown_colors(self) -> tuple[str, str]:
        return self._up_color.name(), self._down_color.name()

    def set_updown_colors(self, up: str, down: str):
        self._up_color = QColor(up)
        self._down_color = QColor(down)
        self._updown_custom = (up, down)
        self.update()

    def text_color(self) -> str:
        return self._text_color

    def draw_colors(self) -> dict:
        return dict(self._draw_colors)

    def set_draw_colors(self, colors: dict, apply_selected: bool = False):
        """设置各类划线默认颜色；apply_selected=True 时同步修改选中标注。
        传入空字典表示恢复当前背景下的默认配色。"""
        if not colors:
            self._draw_colors_custom = None
            self._draw_colors = dict(DRAW_COLORS_LIGHT if self._is_light
                                     else DRAW_COLORS)
        else:
            for key in self._draw_colors:
                value = colors.get(key)
                if value:
                    self._draw_colors[key] = str(value)
            self._draw_colors_custom = dict(self._draw_colors)
        if (apply_selected and 0 <= self._sel_drawing < len(self._drawings)):
            d = self._drawings[self._sel_drawing]
            key = d.get("type", "text")
            if key in self._draw_colors:
                d["color"] = self._draw_colors[key]
        self.update()

    def rr_colors(self) -> dict:
        return dict(self._rr_colors)

    def set_rr_colors(self, profit: str, loss: str, entry: str,
                      apply_selected: bool = False):
        """设置盈亏比区三区颜色；可同时应用到当前选中的盈亏比区。"""
        self._rr_colors = {"profit_color": profit, "loss_color": loss,
                           "entry_color": entry}
        targets = []
        if apply_selected and 0 <= self._sel_drawing < len(self._drawings):
            targets.append(self._drawings[self._sel_drawing])
        if self._rr_draft is not None:
            targets.append(self._rr_draft)
        for d in targets:
            if d.get("type") == "rr":
                d.update(self._rr_colors)
        self.update()

    def clear_drawings(self):
        self._finish_text_edit()
        self._drawings = []
        self._drawing = None
        self._draw_drag = None
        self._rr_draft = None
        self._rr_step = 0
        self._sel_drawing = -1
        self.update()

    # ---------------- 文字标注：图上就地输入 ----------------
    def _map_point(self, bar: int, price: float):
        """(bar, price) -> 屏幕坐标。"""
        _, _, bw, pr, _, lo, hi = self._visible_range()
        px = pr.left() + (bar - self._left_f + 0.5) * bw
        py = pr.bottom() - (price - lo) / (hi - lo) * pr.height()
        return px, py

    def _edit_font(self, family: str | None = None,
                   size: int | None = None) -> QFont:
        f = QFont(family or self._text_family or self.font().family())
        f.setPointSize(int(size or self._text_size))
        return f

    def _edit_qss(self, color: str | None = None) -> str:
        """输入框底色与 K 线图背景保持一致。"""
        return (f"QLineEdit{{background:{self._bg.name()};"
                f"color:{color or self._text_color};"
                "border:1px solid #6cb6ff;padding:1px 3px;}")

    def _begin_text_edit(self, bar: int, price: float,
                         idx: int | None = None):
        """在图上指定位置直接显示输入框（不弹对话框）。"""
        self._finish_text_edit()
        d = self._drawings[idx] if idx is not None else None
        fam = (d or {}).get("font_family") or self._text_family
        size = int((d or {}).get("font_size") or self._text_size)
        color = (d or {}).get("color") or self._text_color
        edit = QLineEdit(self)
        edit.setText((d or {}).get("text", ""))
        edit.setFont(self._edit_font(fam, size))
        edit.setStyleSheet(self._edit_qss(color))
        edit.setFrame(False)
        px, py = self._map_point(bar, price)
        h = max(20, QFontMetrics(self._edit_font(fam, size)).height() + 8)
        w = max(140, min(int(self.width() * 0.45), 320))
        x0 = int(min(max(px, 2), self.width() - w - 4))
        y0 = int(min(max(py - h, 2), self.height() - h - 2))
        edit.setGeometry(x0, y0, w, h)
        edit.show()
        edit.setFocus()
        edit.selectAll()
        edit.returnPressed.connect(lambda: self._finish_text_edit(True))
        edit.editingFinished.connect(lambda: self._finish_text_edit(False))
        self._text_edit = edit
        self._text_edit_ctx = (bar, price, idx)
        self._sel_drawing = idx if idx is not None else -1

    def _finish_text_edit(self, _from_return: bool = False):
        edit, self._text_edit = self._text_edit, None
        if edit is None or self._text_edit_ctx is None:
            return
        bar, price, idx = self._text_edit_ctx
        self._text_edit_ctx = None
        txt = edit.text().strip()
        edit.deleteLater()
        if idx is not None and 0 <= idx < len(self._drawings):
            d = self._drawings[idx]
            if txt:
                d["text"] = txt
            else:
                self._drawings.pop(idx)
                self._sel_drawing = -1
        elif txt:
            self._drawings.append({
                "type": "text", "p1": (bar, price), "text": txt,
                "color": self._text_color,
                "font_family": self._text_family,
                "font_size": self._text_size,
                "width": self._draw_width})
            self._sel_drawing = len(self._drawings) - 1
            if self._draw_mode == "text":
                self._return_to_select()
        self.setFocus()
        self.update()

    def _sync_text_edit(self):
        """平移/缩放后让输入框跟随原位置。"""
        if self._text_edit is None or self._text_edit_ctx is None:
            return
        bar, price, _idx = self._text_edit_ctx
        px, py = self._map_point(bar, price)
        h = self._text_edit.height()
        w = self._text_edit.width()
        x0 = int(min(max(px, 2), self.width() - w - 4))
        y0 = int(min(max(py - h, 2), self.height() - h - 2))
        self._text_edit.move(x0, y0)

    # ---------------- 叠加品种（指数/板块日K） ----------------
    def set_text_style(self, family: str, size: int, color: str):
        """设置新建文字标注的默认字体、字号与颜色。"""
        self._text_family = family or ""
        self._text_size = max(6, int(size))
        self._text_color = color or "#ffffff"
        if self._text_edit is not None:
            self._text_edit.setFont(self._edit_font())
            self._text_edit.setStyleSheet(self._edit_qss())
        self.update()

    def text_style(self):
        return self._text_family, self._text_size, self._text_color

    def set_overlay(self, code: str, name: str, df, kind: str = "index"):
        """叠加指数/板块的日K蜡烛图。df 需含 date/open/high/low/close 列。"""
        if df is None or df.empty:
            self._overlay = None
        else:
            bars = {}
            for d, o, h, l, c in zip(df["date"], df["open"], df["high"],
                                     df["low"], df["close"]):
                try:
                    bars[str(d)[:10]] = (float(o), float(h), float(l), float(c))
                except (TypeError, ValueError):
                    continue
            self._overlay = {"code": code, "name": name, "kind": kind,
                             "bars": bars} if bars else None
        self.update()

    def clear_overlay(self):
        self._overlay = None
        self.update()

    # ---------------- 交互 ----------------
    def cursor_move(self, delta: int):
        """K线模式：左右键移动光标，自动跟随滚动。"""
        if self._mode not in ("day", "min1", "min5") or not self._dates:
            return
        right = self._right()
        if self._cursor < 0:
            self._cursor = right
        else:
            self._cursor = min(max(self._cursor + delta, 0), right)
        # 光标移出可视区时滚动跟随
        if self._cursor < self._left_f:
            self._left_f = float(self._cursor)
        elif self._cursor > self._left_f + self._bars_visible - 1:
            self._left_f = self._cursor - self._bars_visible + 1
        self._clamp_view()
        self.update()

    def cursor_off(self):
        if self._cursor >= 0:
            self._cursor = -1
            self.update()

    def next_day(self):
        if self._mode != "day" or not self._dates:
            return
        if self._reveal_end < len(self._dates) - 1:
            at_edge = self._left_f >= self._follow_left() - 0.5
            self._reveal_end += 1
            self._cursor = self._reveal_end      # 新出现的K线即当前K线
            if at_edge:
                self._left_f = self._follow_left()
            self.update()

    def prev_day(self):
        if self._mode != "day":
            return
        if self._reveal_end > self._match_idx:
            self._reveal_end -= 1
            self._cursor = self._reveal_end      # 回退后最新K线即当前K线
            self._left_f = min(self._left_f, self._follow_left())
            self.update()

    def reset_to_match(self):
        """重置：视图/最新K线回到命中日。"""
        if self._mode != "day" or not self._dates:
            return
        self._reveal_end = self._match_idx
        self._cursor = self._match_idx
        self._bars_visible = float(min(80, max(20, self._match_idx + 1)))
        self._left_f = max(0.0, self._match_idx - self._bars_visible + 1)
        self._clamp_view()
        self.update()

    def _right(self):
        if self._mode == "day":
            return self._reveal_end
        return len(self._dates) - 1

    def _view_count(self):
        return self._right() + 1

    def _follow_left(self):
        """最新一根K线贴在右边缘时的左边界（空格揭示时用于跟随）。"""
        return max(0.0, self._right() - self._bars_visible + 1)

    def _max_left(self):
        """平移上限：允许命中日左移、右侧留白，极限只剩命中日这一根K线。"""
        return float(self._right())

    def wheelEvent(self, e):
        if not self._dates or self._mode == "trend":
            return
        pane = self._price_pane().united(self._vol_pane())
        rel = min(max((e.position().x() - pane.left()) / pane.width(), 0.0), 1.0)
        self._zoom_at(rel, 0.85 if e.angleDelta().y() > 0 else 1.18)
        self._sync_text_edit()

    def zoom(self, zoom_in: bool):
        """键盘缩放：以视图中心为锚点。zoom_in=True 放大(K线变少)。"""
        if not self._dates or self._mode == "trend":
            return
        self._zoom_at(0.5, 0.85 if zoom_in else 1.18)

    def _zoom_at(self, rel: float, factor: float):
        anchor = self._left_f + rel * self._bars_visible - 0.5
        max_bars = float(max(self._view_count(), MIN_BARS))
        new_bv = min(max(self._bars_visible * factor, MIN_BARS), max_bars)
        self._bars_visible = new_bv
        self._left_f = anchor + 0.5 - rel * new_bv
        self._clamp_view()
        self.update()

    def mousePressEvent(self, e):
        if not self._dates or self._mode == "trend":
            return
        if self._text_edit is not None:
            self._finish_text_edit()
            return
        pos = e.position()
        px, py = pos.x(), pos.y()
        if e.button() == Qt.RightButton:
            # 右键：删除最近的标注（若点击在标注附近）
            idx = self._hit_drawing(px, py)
            if idx >= 0:
                self._drawings.pop(idx)
                self._sel_drawing = -1
                self._draw_drag = None
                self.update()
            return
        if e.button() != Qt.LeftButton:
            return
        i = self._bar_at_x(px)
        price = self._price_at_y(py)
        if i < 0:
            return
        # ---- 各类画线模式 ----
        if self._draw_mode == "line":
            self._drawing = {"type": "line",
                             "p1": (i, price), "p2": (i, price),
                             "color": self._draw_colors["line"],
                             "width": self._draw_width}
            self.update()
            return
        if self._draw_mode == "hline":
            self._drawings.append({"type": "hline", "p1": (i, price),
                                   "color": self._draw_colors["hline"],
                                   "width": self._draw_width})
            self._sel_drawing = len(self._drawings) - 1
            self.update()
            self._return_to_select()
            return
        if self._draw_mode == "ray":
            self._drawing = {"type": "ray",
                             "p1": (i, price), "p2": (i, price),
                             "color": self._draw_colors["ray"],
                             "width": self._draw_width}
            self.update()
            return
        if self._draw_mode in ("percent", "fib"):
            self._drawing = {"type": self._draw_mode,
                             "p1": (i, price), "p2": (i, price),
                             "color": self._draw_colors[self._draw_mode],
                             "width": self._draw_width}
            self.update()
            return
        if self._draw_mode == "rr":
            self._rr_press(i, price)
            return
        if self._draw_mode == "text":
            self._begin_text_edit(i, price)
            return
        # select 模式：选中后可拖动端点、边界或整个标注
        idx, part = self._hit_drawing_detail(px, py)
        if idx >= 0:
            self._sel_drawing = idx
            self._draw_drag = {"idx": idx, "part": part or "move",
                               "start": (i, price),
                               "press": (px, py),
                               "orig": dict(self._drawings[idx])}
            # 单击文字标注（未拖动）直接进入编辑
            self._text_pending = (idx if
                                  self._drawings[idx].get("type") == "text"
                                  and (part or "move") == "move" else None)
            self._drag_x = None
            self.update()
            return
        self._sel_drawing = -1
        self._text_pending = None
        self._draw_drag = None
        self._drag_x = px
        self._drag_left = self._left_f

    def mouseMoveEvent(self, e):
        if not self._dates or self._mode == "trend":
            return
        pos = e.position()
        px, py = pos.x(), pos.y()
        i = self._bar_at_x(px)
        price = self._price_at_y(py)
        if self._drawing is not None:
            if i >= 0:
                self._drawing["p2"] = (i, price)
                self.update()
            return
        if self._rr_draft is not None:
            self._rr_move(i, price)
            return
        if self._draw_drag is not None and i >= 0:
            if self._text_pending is not None:
                sx, sy = self._draw_drag["press"]
                if abs(px - sx) + abs(py - sy) < 4:
                    return          # 还可能是单击，先不移动文字
                self._text_pending = None
            self._drag_selected_drawing(i, price)
            return
        pr = self._price_pane()
        bw = pr.width() / self._bars_visible
        if self._drag_x is not None:
            self._left_f = self._drag_left - (px - self._drag_x) / bw
            self._clamp_view()
            self.update()
            return
        # 鼠标悬停移动十字光标
        if bw > 0:
            self._hover_y = py
            idx = int(round(self._left_f + (px - pr.left()) / bw - 0.5))
            idx = min(max(idx, 0), self._right())
            if idx != self._cursor:
                self._cursor = idx
            self.update()

    def leaveEvent(self, _e):
        if self._hover_y is not None:
            self._hover_y = None
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drawing is not None:
            p1, p2 = self._drawing["p1"], self._drawing["p2"]
            # 起点终点相同则丢弃
            if p1 != p2:
                self._drawings.append(dict(self._drawing))
                self._sel_drawing = len(self._drawings) - 1
                self._drawing = None
                self._return_to_select()
                return
            self._drawing = None
            self.update()
            return
        if self._rr_draft is not None and self._rr_step == 3:
            # 完成盈亏比区绘制
            self._drawings.append(dict(self._rr_draft))
            self._sel_drawing = len(self._drawings) - 1
            self._rr_draft = None
            self._rr_step = 0
            self._rr_entry_anchor = None
            self.update()
            self._return_to_select()
            return
        if self._draw_drag is not None:
            pending = self._text_pending
            self._text_pending = None
            self._draw_drag = None
            if pending is not None and 0 <= pending < len(self._drawings):
                d = self._drawings[pending]
                self._begin_text_edit(d["p1"][0], d["p1"][1], pending)
                return
            self.update()
            return
        self._drag_x = None

    def _rr_press(self, i: int, price: float):
        """盈亏比区绘制：点击止损 → 点击止盈 → 拖动建仓区。"""
        if self._rr_step == 0:
            self._rr_draft = {
                "type": "rr", "stop_loss": price, "take_profit": price,
                "entry_top": price, "entry_bottom": price,
                "p1": (i, price), "p2": (i + 10, price),
                "color": self._draw_colors["rr"],
                "width": self._draw_width, **self._rr_colors,
            }
            self._rr_step = 1
            self.update()
        elif self._rr_step == 1:
            self._rr_draft["take_profit"] = price
            self._rr_draft["entry_top"] = price
            self._rr_draft["entry_bottom"] = price
            start_i = self._rr_draft["p1"][0]
            self._rr_draft["p2"] = (i if i != start_i else i + 10, price)
            self._rr_step = 2
            self.update()
        elif self._rr_step == 2:
            sl = self._rr_draft["stop_loss"]
            tp = self._rr_draft["take_profit"]
            anchor = min(max(price, min(sl, tp)), max(sl, tp))
            self._rr_entry_anchor = anchor
            self._rr_draft["entry_top"] = anchor
            self._rr_draft["entry_bottom"] = anchor
            self._rr_step = 3
            self.update()

    def _rr_move(self, i: int, price: float):
        """盈亏比区三步绘制过程中的实时预览。"""
        d = self._rr_draft
        if d is None:
            return
        if self._rr_step == 1:
            d["take_profit"] = price
            d["entry_top"] = price
            d["entry_bottom"] = price
            d["p2"] = (i, price)
        elif self._rr_step == 2:
            d["entry_top"] = price
            d["entry_bottom"] = price
        elif self._rr_step == 3 and self._rr_entry_anchor is not None:
            sl, tp = d["stop_loss"], d["take_profit"]
            value = min(max(price, min(sl, tp)), max(sl, tp))
            anchor = self._rr_entry_anchor
            d["entry_top"] = max(anchor, value)
            d["entry_bottom"] = min(anchor, value)
        self.update()

    def _drag_selected_drawing(self, i: int, price: float):
        drag = self._draw_drag
        idx = drag["idx"]
        if not 0 <= idx < len(self._drawings):
            return
        d = self._drawings[idx]
        orig = drag["orig"]
        part = drag["part"]
        typ = d.get("type", "text")
        start_i, start_price = drag["start"]
        di, dp = i - start_i, price - start_price
        if part == "move":
            for key in ("p1", "p2"):
                if key in orig:
                    bar, value = orig[key]
                    d[key] = (bar + di, value + dp)
            if typ == "rr":
                for key in ("stop_loss", "take_profit",
                            "entry_top", "entry_bottom"):
                    d[key] = float(orig.get(key, 0.0)) + dp
        elif part in ("p1", "p2"):
            if typ == "rr":
                # 盈亏比区的左右边界只改变时间范围，不改变价格。
                d[part] = (i, orig[part][1])
            elif typ == "hline":
                d["p1"] = (orig["p1"][0], price)
            else:
                d[part] = (i, price)
        elif typ == "rr" and part in (
                "stop_loss", "take_profit", "entry_top", "entry_bottom"):
            value = price
            if part in ("entry_top", "entry_bottom"):
                sl, tp = d["stop_loss"], d["take_profit"]
                value = min(max(value, min(sl, tp)), max(sl, tp))
                if part == "entry_top":
                    value = max(value, d["entry_bottom"])
                else:
                    value = min(value, d["entry_top"])
            d[part] = value
        self.update()

    @staticmethod
    def _level_ratios(typ: str) -> tuple:
        if typ == "percent":
            return 0.0, 0.25, 0.5, 0.75, 1.0
        return 0.0, 0.236, 0.382, 0.5, 0.618, 1.0

    @staticmethod
    def _ray_end(x1: float, y1: float, x2: float, y2: float,
                 pr: QRectF) -> tuple:
        """射线从 p1 经 p2 延伸到价格窗边缘。"""
        dx, dy = x2 - x1, y2 - y1
        if abs(dx) + abs(dy) < 1e-9:
            return x2, y2
        candidates = []
        if dx > 0:
            candidates.append((pr.right() - x1) / dx)
        elif dx < 0:
            candidates.append((pr.left() - x1) / dx)
        if dy > 0:
            candidates.append((pr.bottom() - y1) / dy)
        elif dy < 0:
            candidates.append((pr.top() - y1) / dy)
        candidates = [t for t in candidates if t > 0]
        t = min(candidates) if candidates else 1.0
        return x1 + dx * t, y1 + dy * t

    def _drawing_hit_info(self, d: dict, px: float, py: float,
                          to_xy, pr: QRectF) -> tuple:
        """返回 (到标注的距离, 拖动部位)。"""
        typ = d.get("type", "text")
        far = float("inf")
        if typ in ("line", "ray"):
            x1, y1 = to_xy(d["p1"])
            x2, y2 = to_xy(d["p2"])
            best_d, best_part = far, "move"
            for part, xx, yy in (("p1", x1, y1), ("p2", x2, y2)):
                dist = math.hypot(px - xx, py - yy)
                if dist < best_d:
                    best_d, best_part = dist, part
            if best_d <= 7.0:
                return best_d, best_part
            if typ == "ray":
                x2, y2 = self._ray_end(x1, y1, x2, y2, pr)
            return self._dist_to_seg(px, py, x1, y1, x2, y2), "move"
        if typ == "hline":
            _x1, y1 = to_xy(d["p1"])
            if pr.left() - 4 <= px <= pr.right() + 4:
                return abs(py - y1), "p1"
            return far, ""
        if typ in ("percent", "fib"):
            x1, y1 = to_xy(d["p1"])
            x2, y2 = to_xy(d["p2"])
            best_d, best_part = far, "move"
            for part, xx, yy in (("p1", x1, y1), ("p2", x2, y2)):
                dist = math.hypot(px - xx, py - yy)
                if dist < best_d:
                    best_d, best_part = dist, part
            if best_d <= 7.0:
                return best_d, best_part
            left, right = sorted((x1, x2))
            if left - 4 <= px <= right + 4:
                for ratio in self._level_ratios(typ):
                    yy = y1 + (y2 - y1) * ratio
                    best_d = min(best_d, abs(py - yy))
            if min(y1, y2) - 4 <= py <= max(y1, y2) + 4:
                best_d = min(best_d, abs(px - left), abs(px - right))
            return best_d, "move"
        if typ == "rr":
            x1, _ = to_xy(d["p1"])
            x2, _ = to_xy(d["p2"])
            left, right = sorted((x1, x2))
            lines = [(key, to_xy((d["p1"][0], float(d.get(key, 0.0))))[1])
                     for key in ("take_profit", "entry_top",
                                 "entry_bottom", "stop_loss")]
            best_d, best_part = far, ""
            if left - 6 <= px <= right + 6:
                for key, yy in lines:
                    dist = abs(py - yy)
                    if dist < best_d:
                        best_d, best_part = dist, key
                if best_d <= 6.0:
                    return best_d, best_part
            all_y = [yy for _key, yy in lines]
            top, bottom = min(all_y), max(all_y)
            if top - 6 <= py <= bottom + 6:
                if abs(px - left) <= 8:
                    return abs(px - left), "p1"
                if abs(px - right) <= 8:
                    return abs(px - right), "p2"
            rect = QRectF(left, top, max(right - left, 1.0),
                          max(bottom - top, 1.0))
            if rect.contains(px, py):
                return 0.0, "move"
            dist = min(
                self._dist_to_seg(px, py, left, top, right, top),
                self._dist_to_seg(px, py, right, top, right, bottom),
                self._dist_to_seg(px, py, right, bottom, left, bottom),
                self._dist_to_seg(px, py, left, bottom, left, top))
            return dist, "move"
        # 文字标注
        x1, y1 = to_xy(d["p1"])
        fm = QFontMetrics(self._edit_font(d.get("font_family"),
                                          d.get("font_size")))
        rect = QRectF(x1 + 2, y1 - fm.ascent() - 2,
                      fm.horizontalAdvance(d.get("text", "")) + 8,
                      fm.height() + 4)
        if rect.contains(px, py):
            return 0.0, "move"
        dx = max(rect.left() - px, 0.0, px - rect.right())
        dy = max(rect.top() - py, 0.0, py - rect.bottom())
        return math.hypot(dx, dy), "move"

    def _hit_drawing_detail(self, px: float, py: float) -> tuple:
        """返回 (标注索引, 命中部位)；未命中为 (-1, '')。"""
        _i0, _i1, bw, pr, _, lo, hi = self._visible_range()
        if bw <= 0:
            return -1, ""

        def to_xy(point):
            i, value = point
            xx = pr.left() + (i - self._left_f + 0.5) * bw
            yy = pr.bottom() - (value - lo) / (hi - lo) * pr.height()
            return xx, yy

        best_idx, best_part = -1, ""
        best_d = 8.0
        for idx in range(len(self._drawings) - 1, -1, -1):
            dist, part = self._drawing_hit_info(
                self._drawings[idx], px, py, to_xy, pr)
            if dist < best_d:
                best_d = dist
                best_idx, best_part = idx, part
        return best_idx, best_part

    def _hit_drawing(self, px: float, py: float) -> int:
        """返回点击位置附近（8px）的标注索引，-1 表示未命中。"""
        return self._hit_drawing_detail(px, py)[0]

    @staticmethod
    def _dist_to_seg(px, py, x1, y1, x2, y2) -> float:
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        if l2 == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

    def _clamp_view(self):
        n = self._view_count()
        if self._bars_visible >= n:
            self._left_f = (n - self._bars_visible) / 2
        else:
            self._left_f = min(max(self._left_f, 0.0), self._max_left())

    # ---------------- 布局 ----------------
    def _price_pane(self) -> QRectF:
        w = self.width() - AXIS_W
        h = (self.height() - DATE_H) * 0.72
        return QRectF(0, 0, w, h)

    def _vol_pane(self) -> QRectF:
        w = self.width() - AXIS_W
        y = (self.height() - DATE_H) * 0.72 + 6
        return QRectF(0, y, w, self.height() - DATE_H - y)

    # ---------------- 绘制 ----------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), self._bg)
        if not self._dates or self._right() < 0:
            p.setPen(self._aux["text"])
            p.drawText(self.rect(), Qt.AlignCenter, "双击左侧结果或按方向键选择个股")
            p.end()
            return
        if self._mode == "trend":
            self._paint_trend(p)
        else:
            self._paint_kline(p)
        p.end()

    def _draw_grid(self, p: QPainter, pr: QRectF, vr: QRectF, hi: float, lo: float):
        for k in range(6):
            yy = pr.top() + pr.height() * k / 5
            p.setPen(QPen(self._aux["grid"], 1))
            p.drawLine(QPointF(pr.left(), yy), QPointF(pr.right(), yy))
            price = hi - (hi - lo) * k / 5
            p.setPen(self._aux["text"])
            p.drawText(QRectF(pr.right() + 2, yy - 8, AXIS_W - 4, 16),
                       Qt.AlignVCenter | Qt.AlignLeft, f"{price:.2f}")
        p.setPen(QPen(self._aux["grid"], 1))
        p.drawLine(QPointF(vr.left(), vr.top()), QPointF(vr.right(), vr.top()))

    def _paint_kline(self, p: QPainter):
        pr, vr = self._price_pane(), self._vol_pane()
        right = self._right()
        bw = pr.width() / self._bars_visible
        i0 = max(0, int(math.floor(self._left_f)))
        i1 = min(right, int(math.ceil(self._left_f + self._bars_visible)))
        if i1 < i0:
            return

        lo = float(np.nanmin(self._l[i0:i1 + 1]))
        hi = float(np.nanmax(self._h[i0:i1 + 1]))
        for arr in self._mas.values():
            seg = arr[i0:i1 + 1]
            seg = seg[~np.isnan(seg)]
            if seg.size:
                lo = min(lo, float(seg.min()))
                hi = max(hi, float(seg.max()))
        pad = max((hi - lo) * 0.06, 1e-6)
        lo, hi = lo - pad, hi + pad
        vmax = float(np.nanmax(self._v[i0:i1 + 1])) or 1.0

        def x(i):
            return pr.left() + (i - self._left_f + 0.5) * bw

        def y(price):
            return pr.bottom() - (price - lo) / (hi - lo) * pr.height()

        self._draw_grid(p, pr, vr, hi, lo)

        # 命中日高亮带（日K模式）：半透明底色标出整列，不遮挡影线
        if self._mode == "day" and i0 <= self._match_idx <= i1:
            band = QColor(MATCH)
            band.setAlpha(34)
            p.fillRect(QRectF(x(self._match_idx) - bw / 2, pr.top(),
                              bw, vr.bottom() - pr.top()), band)

        # 日界分隔（分钟模式）
        if self._mode != "day":
            for idx, day in self._day_breaks:
                if not (i0 <= idx <= i1):
                    continue
                xx = x(idx) - bw / 2
                p.setPen(QPen(self._aux["daybreak"], 1, Qt.DashLine))
                p.drawLine(QPointF(xx, pr.top()), QPointF(xx, vr.bottom()))
                p.setPen(self._aux["text"])
                p.drawText(QRectF(xx + 2, pr.top() + 2, 80, 14),
                           Qt.AlignLeft, day[5:])

        # K线（阳线够宽时空心）
        body_w = max(1.0, bw * 0.72)
        hollow = body_w >= 5.0
        for i in range(i0, i1 + 1):
            up = self._c[i] >= self._o[i]
            color = self._up_color if up else self._down_color
            xx = x(i)
            p.setPen(QPen(color, 1))
            p.drawLine(QPointF(xx, y(self._h[i])), QPointF(xx, y(self._l[i])))
            top = y(max(self._o[i], self._c[i]))
            bot = y(min(self._o[i], self._c[i]))
            rect = QRectF(xx - body_w / 2, top, body_w, max(bot - top, 1.0))
            if up and hollow:
                p.fillRect(rect, self._bg)
                p.drawRect(rect)
            else:
                p.fillRect(rect, color)

        # 均线
        for mname, arr in self._mas.items():
            p.setPen(QPen(self._ma_color(mname), self._ma_width))
            prev = None
            for i in range(i0, i1 + 1):
                v = arr[i]
                if math.isnan(v):
                    prev = None
                    continue
                pt = QPointF(x(i), y(v))
                if prev is not None:
                    p.drawLine(prev, pt)
                prev = pt

        # 叠加指数走势（按可视区首根K线的指数收盘归一化为涨跌幅）
        if self._overlay is not None:
            self._paint_overlay(p, x, y, i0, i1, pr, lo, hi)

        # 成交量
        for i in range(i0, i1 + 1):
            color = (self._up_color if self._c[i] >= self._o[i]
                     else self._down_color)
            vh = self._v[i] / vmax * vr.height()
            p.fillRect(QRectF(x(i) - body_w / 2, vr.bottom() - vh, body_w, vh),
                       color)
        p.setPen(self._aux["text"])
        p.drawText(QRectF(vr.right() + 2, vr.top(), AXIS_W - 4, 16),
                   Qt.AlignLeft, self._fmt_vol(vmax))

        # 命中日标记（日K模式）：仅保留低点下方小三角，虚线已改为背景高亮带
        if self._mode == "day" and i0 <= self._match_idx <= i1:
            mx = x(self._match_idx)
            my = y(self._l[self._match_idx]) + 6
            p.setBrush(MATCH)
            p.setPen(Qt.NoPen)
            p.drawPolygon([QPointF(mx - 5, my), QPointF(mx + 5, my),
                           QPointF(mx, my - 6)])

        # 用户画线 / 文字标注
        self._paint_drawings(p, x, y, pr)

        # 十字光标（K线模式，鼠标悬停/左右键移动）
        if self._mode != "trend" and i0 <= self._cursor <= i1:
            cx = x(self._cursor)
            cross = QColor(self._aux["title"])
            cross.setAlpha(150)
            p.setPen(QPen(cross, 1, Qt.DashLine))
            p.drawLine(QPointF(cx, pr.top()), QPointF(cx, vr.bottom()))
            if self._hover_y is not None and pr.top() <= self._hover_y <= pr.bottom():
                hy = self._hover_y
                p.drawLine(QPointF(pr.left(), hy), QPointF(pr.right(), hy))
                price = lo + (pr.bottom() - hy) / pr.height() * (hi - lo)
                p.fillRect(QRectF(pr.right() + 1, hy - 9, AXIS_W - 2, 18),
                           self._aux["grid"])
                p.setPen(self._aux["title"])
                p.drawText(QRectF(pr.right() + 3, hy - 9, AXIS_W - 6, 18),
                           Qt.AlignVCenter | Qt.AlignLeft, f"{price:.2f}")

        # 日期/时间轴
        p.setPen(self._aux["text"])
        step = max(1, int(self._bars_visible / 6))
        if self._mode == "day":
            for i in range(i0 - i0 % step, i1 + 1, step):
                p.drawText(QRectF(x(i) - 45, self.height() - DATE_H, 90, DATE_H),
                           Qt.AlignCenter, str(self._dates[i]))
        else:
            multi_day = len(self._day_breaks) > 1
            for i in range(i0 - i0 % step, i1 + 1, step):
                s = str(self._dates[i])          # "YYYY-MM-DD HH:MM"
                label = s[5:16] if multi_day else s[11:16]  # 跨日带日期, 单日只显示时分
                p.drawText(QRectF(x(i) - 55, self.height() - DATE_H, 110, DATE_H),
                           Qt.AlignCenter, label)

        # 光标处日期标签（吸底显示）
        if self._mode != "trend" and i0 <= self._cursor <= i1:
            s = str(self._dates[self._cursor])
            label = s if self._mode == "day" else s[5:16]
            bx = x(self._cursor)
            p.fillRect(QRectF(bx - 55, self.height() - DATE_H, 110, DATE_H),
                       self._aux["grid"])
            p.setPen(self._aux["title"])
            p.drawText(QRectF(bx - 55, self.height() - DATE_H, 110, DATE_H),
                       Qt.AlignCenter, label)

        self._draw_header(p, pr)

    def _paint_overlay(self, p, x, y, i0, i1, pr, lo, hi):
        """叠加指数/板块日K：按可视区自身高低点缩放到价格窗，绘制细蜡烛。"""
        bars = self._overlay["bars"]
        vis = []   # (bar_index, o,h,l,c)
        for i in range(i0, i1 + 1):
            b = bars.get(str(self._dates[i])[:10])
            if b is not None:
                vis.append((i,) + b)
        if len(vis) < 2:
            return
        o_lo = min(v[3] for v in vis)
        o_hi = max(v[2] for v in vis)
        span = max(o_hi - o_lo, 1e-9)
        top, bottom = pr.top() + 6, pr.bottom() - 6

        def oy(price):
            return bottom - (price - o_lo) / span * (bottom - top)

        bw = max(1.0, (pr.width() / self._bars_visible) * 0.5)
        up_c, down_c = QColor("#ffb300"), QColor("#42a5f5")
        for i, o, h, l, c in vis:
            cx = x(i)
            col = up_c if c >= o else down_c
            p.setPen(QPen(col, 1.0))
            p.drawLine(QPointF(cx, oy(h)), QPointF(cx, oy(l)))
            y1, y2 = oy(o), oy(c)
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(cx - bw / 2, min(y1, y2),
                              max(bw, 1.2), max(abs(y2 - y1), 1.2)))
        # 右上角图例：最新一根的涨跌
        i_last, o_l, _h_l, _l_l, c_l = vis[-1]
        pct = (c_l / o_l - 1) * 100 if o_l else 0.0
        label = f"{self._overlay['name']} 日K {pct:+.2f}%"
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(label)
        p.fillRect(QRectF(pr.right() - tw - 12, 2, tw + 10, 16),
                   QColor(0, 0, 0, 90))
        p.setPen(QColor("#ffb300"))
        p.drawText(QRectF(pr.right() - tw - 8, 2, tw + 4, 16),
                   Qt.AlignVCenter | Qt.AlignRight, label)

    @staticmethod
    def _rr_ratios(d: dict) -> list:
        """按建仓区上下沿计算盈亏比范围，同时兼容做多和做空。"""
        sl = float(d.get("stop_loss", 0.0))
        tp = float(d.get("take_profit", 0.0))
        if abs(tp - sl) < 1e-12:
            return []
        direction = 1.0 if tp > sl else -1.0
        values = []
        for key in ("entry_bottom", "entry_top"):
            entry = float(d.get(key, 0.0))
            reward = (tp - entry) * direction
            risk = (entry - sl) * direction
            if reward >= 0 and risk > 0:
                values.append(reward / risk)
        return sorted(values)

    def _paint_drawings(self, p, x, y, pr):
        """绘制直线、水平线、射线、比例线、盈亏比区与文字。"""
        items = list(self._drawings)
        if self._drawing is not None:
            items.append(self._drawing)
        rr_draft_active = self._rr_draft is not None
        if rr_draft_active:
            items.append(self._rr_draft)
        base_font = p.font()

        def tag(xx, yy, text, col, right_aligned=False):
            fm = p.fontMetrics()
            width = fm.horizontalAdvance(text) + 8
            left = xx - width if right_aligned else xx
            left = min(max(left, pr.left() + 2),
                       max(pr.left() + 2, pr.right() - width - 2))
            rect = QRectF(left, yy - 8, width, 16)
            p.fillRect(rect, QColor(0, 0, 0, 150))
            p.setPen(col)
            p.drawText(rect, Qt.AlignCenter, text)

        def handles(points, col):
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            for xx, yy in points:
                p.drawEllipse(QPointF(xx, yy), 4, 4)

        for idx, d in enumerate(items):
            p.setBrush(Qt.NoBrush)
            typ = d.get("type", "text")
            color = QColor(d.get("color", "#ffffff"))
            sel = (idx == self._sel_drawing
                   or (self._drawing is not None and idx == len(items) - 1)
                   or (rr_draft_active and idx == len(items) - 1))
            base_w = float(d.get("width") or self._draw_width)
            pen_w = base_w + (0.8 if sel else 0.0)
            if typ in ("line", "ray"):
                i1, price1 = d["p1"]
                i2, price2 = d["p2"]
                x1, y1 = x(i1), y(price1)
                x2, y2 = x(i2), y(price2)
                end_x, end_y = (self._ray_end(x1, y1, x2, y2, pr)
                                if typ == "ray" else (x2, y2))
                p.setPen(QPen(color, pen_w))
                p.drawLine(QPointF(x1, y1), QPointF(end_x, end_y))
                if sel:
                    handles([(x1, y1), (x2, y2)], color)
            elif typ == "hline":
                i1, price1 = d["p1"]
                yy = y(price1)
                p.setPen(QPen(color, pen_w))
                p.drawLine(QPointF(pr.left(), yy), QPointF(pr.right(), yy))
                tag(pr.right() - 4, yy, f"{price1:.2f}", color, True)
                if sel:
                    handles([(x(i1), yy)], color)
            elif typ in ("percent", "fib"):
                i1, price1 = d["p1"]
                i2, price2 = d["p2"]
                x1, x2 = x(i1), x(i2)
                left, right = sorted((x1, x2))
                p.setPen(QPen(color, pen_w))
                for ratio in self._level_ratios(typ):
                    value = price1 + (price2 - price1) * ratio
                    yy = y(value)
                    p.drawLine(QPointF(left, yy), QPointF(right, yy))
                    label = f"{ratio * 100:.3g}%  {value:.2f}"
                    p.drawText(QRectF(max(pr.left(), left) + 4, yy - 16,
                                      130, 16), Qt.AlignVCenter | Qt.AlignLeft,
                               label)
                if sel:
                    p.setPen(QPen(color, 1, Qt.DashLine))
                    p.drawLine(QPointF(left, y(price1)),
                               QPointF(left, y(price2)))
                    p.drawLine(QPointF(right, y(price1)),
                               QPointF(right, y(price2)))
                    handles([(x1, y(price1)), (x2, y(price2))], color)
            elif typ == "rr":
                sl = float(d.get("stop_loss", 0.0))
                tp = float(d.get("take_profit", 0.0))
                entry_top = float(d.get("entry_top", 0.0))
                entry_bottom = float(d.get("entry_bottom", 0.0))
                x1, x2 = x(d["p1"][0]), x(d["p2"][0])
                left, right = sorted((x1, x2))
                if right - left < 2:
                    right = left + 2
                y_sl, y_tp = y(sl), y(tp)
                y_et, y_eb = y(entry_top), y(entry_bottom)
                profit_c = QColor(d.get("profit_color",
                                        self._rr_colors["profit_color"]))
                loss_c = QColor(d.get("loss_color",
                                      self._rr_colors["loss_color"]))
                entry_c = QColor(d.get("entry_color",
                                       self._rr_colors["entry_color"]))

                def fill_zone(y_a, y_b, col):
                    rect = QRectF(left, min(y_a, y_b), right - left,
                                  abs(y_b - y_a)).intersected(pr)
                    if not rect.isEmpty():
                        fill = QColor(col)
                        fill.setAlpha(50)
                        p.fillRect(rect, fill)

                # 只用色块区分盈利/建仓/亏损三个区域，不画边框和分隔线
                fill_zone(y_tp, y_et, profit_c)
                fill_zone(y_et, y_eb, entry_c)
                fill_zone(y_eb, y_sl, loss_c)
                top = min(y_sl, y_tp, y_et, y_eb)
                bottom = max(y_sl, y_tp, y_et, y_eb)
                # 价位只显示数字，放在色块右侧外
                fm = p.fontMetrics()
                for value, yy, col in ((tp, y_tp, profit_c),
                                       (entry_top, y_et, entry_c),
                                       (entry_bottom, y_eb, entry_c),
                                       (sl, y_sl, loss_c)):
                    txt = f"{value:.2f}"
                    tw = fm.horizontalAdvance(txt)
                    tx = right + 6
                    if tx + tw > pr.right() - 2:
                        # 右侧放不下时改放到色块左侧外
                        tx = left - tw - 6
                        if tx < pr.left() + 2:
                            tx = pr.right() - tw - 2
                    p.setPen(col)
                    p.drawText(QRectF(tx, yy - 8, tw + 2, 16),
                               Qt.AlignVCenter | Qt.AlignLeft, txt)
                # 盈亏比：色块上方居中，无底色，字号更大
                ratios = self._rr_ratios(d)
                if not ratios:
                    ratio_text = "盈亏比 --"
                elif abs(ratios[-1] - ratios[0]) < 0.005:
                    ratio_text = f"盈亏比 {ratios[0]:.2f}"
                else:
                    ratio_text = f"盈亏比 {ratios[0]:.2f} ~ {ratios[-1]:.2f}"
                ratio_font = QFont(p.font())
                ratio_font.setPointSize(max(11, ratio_font.pointSize() + 2))
                ratio_font.setBold(True)
                p.setFont(ratio_font)
                rfm = p.fontMetrics()
                rtw = rfm.horizontalAdvance(ratio_text)
                rx = (left + right) / 2 - rtw / 2
                rx = min(max(rx, pr.left() + 2), pr.right() - rtw - 2)
                ry = top - rfm.height() - 1
                if ry < 56:
                    # 避开顶部三行行情信息（名称/涨跌/均线）
                    ry = 56
                if top > 56 and ry + rfm.height() > top:
                    ry = top + 2      # 上方放不下就到色块内顶部
                p.setPen(self._aux["title"])
                p.drawText(QPointF(rx, ry + rfm.ascent()), ratio_text)
                p.setFont(base_font)
            else:
                i1, price1 = d["p1"]
                px, py = x(i1), y(price1)
                txt = d.get("text", "")
                f = self._edit_font(d.get("font_family"),
                                    d.get("font_size"))
                p.setFont(f)
                p.setPen(QPen(color, pen_w))
                p.drawText(QPointF(px + 4, py - 4), txt)
                if sel:
                    fm = QFontMetrics(f)
                    tw = fm.horizontalAdvance(txt)
                    p.setPen(QPen(color, 1, Qt.DashLine))
                    p.drawRect(QRectF(px + 2, py - fm.ascent() - 2,
                                      tw + 6, fm.height() + 4))
                p.setFont(base_font)
        p.setFont(base_font)

    def _draw_header(self, p: QPainter, pr: QRectF):
        right = self._right()
        j = self._cursor if 0 <= self._cursor <= right else right
        mode_txt = {"day": "日K", "min1": "1分钟", "min5": "5分钟"}.get(self._mode, "")
        p.setPen(self._aux["title"])
        p.drawText(QRectF(8, 2, pr.width() - 16, 18), Qt.AlignLeft,
                   f"{self._name} {self._code}  {mode_txt}  {self._dates[j]}")
        if self._pct is not None:
            pct = float(self._pct[j])
        elif j > 0:
            pct = (self._c[j] / self._c[j - 1] - 1) * 100
        else:
            pct = 0.0
        info = (f"开 {self._o[j]:.2f}  高 {self._h[j]:.2f}"
                f"  低 {self._l[j]:.2f}  收 {self._c[j]:.2f}"
                f"  {pct:+.2f}%   量 {self._fmt_vol(self._v[j])}")
        if self._amt is not None:
            info += f"   额 {self._fmt_vol(self._amt[j])}"
        if self._to is not None:
            info += f"   换手 {self._to[j]:.2f}%"
        p.setPen(self._up_color if pct >= 0 else self._down_color)
        p.drawText(QRectF(8, 20, pr.width() - 16, 18), Qt.AlignLeft, info)
        if self._mas:
            fm = p.fontMetrics()
            xx = 8
            for m in sorted(self._mas.keys(),
                            key=lambda s: int(s[2:])):
                v = self._mas[m][j]
                if math.isnan(v):
                    continue
                txt = f"{m.upper()} {v:.2f}"
                p.setPen(self._ma_color(m))
                p.drawText(QRectF(xx, 38, 300, 16), Qt.AlignLeft, txt)
                xx += fm.horizontalAdvance(txt) + 14

    def _paint_trend(self, p: QPainter):
        pr, vr = self._price_pane(), self._vol_pane()
        n = len(self._dates)
        pc = self._prev_close
        hi = float(np.nanmax(self._h))
        lo = float(np.nanmin(self._l))
        if pc > 0:  # 围绕昨收对称
            dev = max(abs(hi - pc), abs(lo - pc), pc * 0.005) * 1.05
            hi, lo = pc + dev, pc - dev
        else:
            pad = max((hi - lo) * 0.06, 1e-6)
            hi, lo = hi + pad, lo - pad
        vmax = float(np.nanmax(self._v)) or 1.0
        bw = pr.width() / max(n, 1)

        def x(i):
            return pr.left() + (i + 0.5) * bw

        def y(price):
            return pr.bottom() - (price - lo) / (hi - lo) * pr.height()

        # 网格 + 右轴百分比
        for k in range(6):
            yy = pr.top() + pr.height() * k / 5
            p.setPen(QPen(self._aux["grid"], 1))
            p.drawLine(QPointF(pr.left(), yy), QPointF(pr.right(), yy))
            price = hi - (hi - lo) * k / 5
            if pc > 0:
                pct = (price / pc - 1) * 100
                p.setPen(self._up_color if pct > 0
                         else (self._down_color if pct < 0
                               else self._aux["text"]))
            else:
                p.setPen(self._aux["text"])
            label = f"{pct:+.2f}%" if pc > 0 else f"{price:.2f}"
            p.drawText(QRectF(pr.right() + 2, yy - 8, AXIS_W - 4, 16),
                       Qt.AlignVCenter | Qt.AlignLeft, label)
        if pc > 0:
            p.setPen(QPen(self._aux["prev"], 1, Qt.DashLine))
            p.drawLine(QPointF(pr.left(), y(pc)), QPointF(pr.right(), y(pc)))
        p.setPen(QPen(self._aux["grid"], 1))
        p.drawLine(QPointF(vr.left(), vr.top()), QPointF(vr.right(), vr.top()))

        # 价格线
        p.setPen(QPen(self._aux["price"], 1.4))
        prev = None
        for i in range(n):
            pt = QPointF(x(i), y(self._c[i]))
            if prev is not None:
                p.drawLine(prev, pt)
            prev = pt

        # 均价线 = 累计成交额 / (累计成交量*100)
        if self._amt is not None:
            cum_v = np.cumsum(self._v)
            cum_a = np.cumsum(self._amt)
            with np.errstate(divide="ignore", invalid="ignore"):
                avg = np.where(cum_v > 0, cum_a / (cum_v * 100.0), np.nan)
            p.setPen(QPen(self._aux["avg"], 1.2))
            prev = None
            for i in range(n):
                if math.isnan(avg[i]):
                    prev = None
                    continue
                pt = QPointF(x(i), y(avg[i]))
                if prev is not None:
                    p.drawLine(prev, pt)
                prev = pt

        # 成交量
        body_w = max(1.0, bw * 0.7)
        for i in range(n):
            ref = self._c[i - 1] if i > 0 else (pc or self._o[0])
            color = (self._up_color if self._c[i] >= ref
                     else self._down_color)
            vh = self._v[i] / vmax * vr.height()
            p.fillRect(QRectF(x(i) - body_w / 2, vr.bottom() - vh, body_w, vh),
                       color)
        p.setPen(self._aux["text"])
        p.drawText(QRectF(vr.right() + 2, vr.top(), AXIS_W - 4, 16),
                   Qt.AlignLeft, self._fmt_vol(vmax))

        # 时间轴
        p.setPen(self._aux["text"])
        step = max(1, n // 6)
        for i in range(0, n, step):
            p.drawText(QRectF(x(i) - 40, self.height() - DATE_H, 80, DATE_H),
                       Qt.AlignCenter, str(self._dates[i])[-5:])
        if (n - 1) % step:
            p.drawText(QRectF(x(n - 1) - 40, self.height() - DATE_H, 80, DATE_H),
                       Qt.AlignCenter, str(self._dates[-1])[-5:])

        # 头部信息
        j = n - 1
        pct = (self._c[j] / pc - 1) * 100 if pc > 0 else 0.0
        p.setPen(self._aux["title"])
        p.drawText(QRectF(8, 2, pr.width() - 16, 18), Qt.AlignLeft,
                   f"{self._name} {self._code}  分时  {self._trend_date}")
        p.setPen(self._up_color if pct >= 0 else self._down_color)
        p.drawText(QRectF(8, 20, pr.width() - 16, 18), Qt.AlignLeft,
                   f"收 {self._c[j]:.2f}  {pct:+.2f}%"
                   f"   总量 {self._fmt_vol(float(np.nansum(self._v)))}")
        if pc > 0:
            p.setPen(self._aux["text"])
            p.drawText(QRectF(8, 38, pr.width() - 16, 18), Qt.AlignLeft,
                       f"昨收 {pc:.2f}")

    @staticmethod
    def _fmt_vol(v):
        if v >= 1e8:
            return f"{v / 1e8:.2f}亿"
        if v >= 1e4:
            return f"{v / 1e4:.1f}万"
        return f"{v:.0f}"
