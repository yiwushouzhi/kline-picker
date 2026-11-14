"""界面主题：配色方案与全局样式表。"""
from __future__ import annotations

THEMES = {
    "dark": {
        "label": "深色",
        "bg": "#0e1015", "panel": "#171a21", "panel2": "#1f232c",
        "border": "#2b303c", "text": "#e3e7ee", "sub": "#8b93a3",
        "hover": "#262b36", "chart_bg": "#0f1115",
    },
    "light": {
        "label": "浅色",
        "bg": "#eef0f4", "panel": "#ffffff", "panel2": "#f5f7fa",
        "border": "#d8dde5", "text": "#252a33", "sub": "#6b7280",
        "hover": "#e9edf3", "chart_bg": "#ffffff",
    },
    "eye": {
        "label": "护眼绿",
        "bg": "#e4ecdf", "panel": "#f6faf2", "panel2": "#ebf3e5",
        "border": "#c4d6ba", "text": "#2c3829", "sub": "#66755c",
        "hover": "#dcead2", "chart_bg": "#f4f9f0",
    },
}

ACCENTS = [("红", "#e5484d"), ("橙", "#e8890c"), ("蓝", "#3b82f6"),
           ("青", "#0ea5a5"), ("绿", "#22a55e"), ("紫", "#8b5cf6")]
DEFAULT_ACCENT = ACCENTS[0][1]
DEFAULT_THEME = "dark"

# 界面密度：按钮 / 页签 / 表格行 的内边距与行高
DENSITY = {
    "compact": {"label": "紧凑", "btn": "3px 9px", "tab": "5px 11px",
                "item": "1px 5px", "row": 22, "ctrl_h": 24},
    "normal": {"label": "标准", "btn": "4px 12px", "tab": "6px 14px",
               "item": "3px 6px", "row": 26, "ctrl_h": 26},
    "loose": {"label": "宽松", "btn": "6px 16px", "tab": "8px 18px",
              "item": "5px 9px", "row": 31, "ctrl_h": 29},
}
DEFAULT_DENSITY = "normal"


def build_qss(t: dict, accent: str, density: str = DEFAULT_DENSITY,
              check_png: str = "") -> str:
    """按主题配色 + 强调色 + 密度生成全局样式表。"""
    bg, panel, panel2 = t["bg"], t["panel"], t["panel2"]
    border, text, sub, hover = t["border"], t["text"], t["sub"], t["hover"]
    d = DENSITY.get(density, DENSITY[DEFAULT_DENSITY])
    pad_btn, pad_tab, pad_item = d["btn"], d["tab"], d["item"]
    check_rule = (f'\n                       image: url("{check_png}");'
                  if check_png else "")
    return f"""
QFrame#sep {{ background: {border}; border: none; max-width: 1px; }}

QWidget {{ background: {bg}; color: {text}; }}
QMainWindow, QDialog {{ background: {bg}; }}

QMenuBar {{ background: {panel}; color: {text};
           border-bottom: 1px solid {border}; padding: 2px; }}
QMenuBar::item {{ background: transparent; padding: 5px 12px;
                 border-radius: 5px; }}
QMenuBar::item:selected {{ background: {hover}; }}
QMenu {{ background: {panel2}; border: 1px solid {border};
        border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 16px; border-radius: 5px; }}
QMenu::item:selected {{ background: {accent}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}

QGroupBox {{ background: {panel}; border: 1px solid {border};
            border-radius: 9px; padding: 26px 9px 8px 9px; }}
QGroupBox::title {{ subcontrol-origin: padding; subcontrol-position: top left;
                   left: 5px; top: 1px; padding: 0 6px;
                   color: {sub}; font-weight: 600; }}

QPushButton {{ background: {panel2}; border: 1px solid {border};
              border-radius: 6px; padding: {pad_btn}; color: {text}; }}
QPushButton:hover {{ background: {hover}; border-color: {accent}; }}
QPushButton:pressed {{ background: {accent}; color: #ffffff;
                      border-color: {accent}; }}
QPushButton:checked {{ background: {accent}; color: #ffffff;
                      border-color: {accent}; font-weight: 600; }}
QPushButton:disabled {{ color: {sub}; background: {panel};
                       border-color: {border}; }}
QPushButton#primary {{ background: {accent}; color: #ffffff;
    border: 1px solid {accent}; border-radius: 6px;
    padding: 5px 20px; font-weight: 600; }}
QPushButton#primary:hover {{ border: 1px solid {text}; }}
QPushButton#primary:pressed {{ background: {panel2}; color: {accent}; }}
QPushButton#primary:disabled {{ background: {panel2}; color: {sub};
    border-color: {border}; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateEdit {{
    background: {panel2}; border: 1px solid {border}; border-radius: 6px;
    padding: 3px 7px; color: {text};
    selection-background-color: {accent}; selection-color: #ffffff; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDateEdit:focus {{ border-color: {accent}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {panel2}; color: {text};
    border: 1px solid {border}; border-radius: 6px;
    selection-background-color: {accent}; selection-color: #ffffff;
    outline: none; padding: 3px; }}
QSpinBox::up-button, QSpinBox::down-button,
QDateEdit::up-button, QDateEdit::down-button {{
    background: {hover}; border: none; width: 16px; border-radius: 3px; }}

QCheckBox {{ background: transparent; spacing: 6px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 3px;
                       border: 1px solid {border}; background: {panel2}; }}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent};{check_rule} }}

QTabWidget::pane {{ border: 1px solid {border}; border-radius: 9px;
                   background: {panel}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {sub};
               padding: {pad_tab}; margin-right: 3px;
               border: 1px solid transparent;
               border-top-left-radius: 7px; border-top-right-radius: 7px; }}
QTabBar::tab:selected {{ background: {panel}; color: {text};
                        border-color: {border}; border-bottom-color: {panel};
                        font-weight: 600; }}
QTabBar::tab:hover:!selected {{ background: {hover}; border-radius: 7px; }}

QTableWidget, QTableView {{ background: {panel};
    alternate-background-color: {panel2}; gridline-color: {border};
    border: 1px solid {border}; border-radius: 8px;
    selection-background-color: {accent}; selection-color: #ffffff; }}
QTableWidget::item {{ padding: {pad_item}; }}
QHeaderView::section {{ background: {panel2}; color: {sub};
    padding: 6px 4px; border: none; border-right: 1px solid {border};
    border-bottom: 1px solid {border}; font-weight: 600; }}
QTableCornerButton::section {{ background: {panel2}; border: none; }}

QSplitter::handle {{ background: {border}; width: 7px; }}
QSplitter::handle:hover {{ background: {accent}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px;
                              min-height: 26px; }}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 5px;
                                min-width: 26px; }}
QScrollBar::handle:horizontal:hover {{ background: {accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QProgressBar {{ background: {panel2}; border: 1px solid {border};
               border-radius: 6px; text-align: center; height: 16px;
               color: {text}; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}

QStatusBar {{ background: {panel}; color: {sub};
             border-top: 1px solid {border}; }}
QStatusBar::item {{ border: none; }}
QLabel {{ background: transparent; }}
QLabel#hint {{ color: {sub}; }}
QLabel#count {{ color: {sub}; font-weight: 600; padding: 3px 2px; }}
QLabel#warnnote {{ color: #e8890c; font-weight: 600; }}
QToolTip {{ background: {panel2}; color: {text};
           border: 1px solid {border}; padding: 4px 7px; border-radius: 5px; }}
"""
