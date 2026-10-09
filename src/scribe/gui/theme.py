"""Design tokens and Qt stylesheet, following docs/DESIGN-notion.md.

Warm paper canvas, white document surface, near-black ink, hairline borders, and exactly one
structural accent (blue) for the primary action / focus. Sticker colours are decoration only
(speaker chips). Korean text falls back to Noto Sans KR / Malgun Gothic since Inter has no Hangul.
"""

from __future__ import annotations

from PySide6.QtGui import QFont

PRIMARY = "#0075de"
PRIMARY_ACTIVE = "#005bab"
CANVAS = "#ffffff"
CANVAS_SOFT = "#f6f5f4"
INK = "#1a1a1a"  # ~95% black, as the guide renders body ink
INK_SECONDARY = "#31302e"
INK_MUTED = "#615d59"
INK_FAINT = "#a39e98"
HAIRLINE = "#e6e6e6"
ROW_HOVER = "#efeeec"
ROW_ACTIVE = "#e8e7e4"
HIGHLIGHT = "#fbf3db"  # search hit, warm yellow like a Notion highlight
WARN_TEXT = "#793400"  # accent-orange-deep
WARN_BG = "#fdf2e9"

# decorative sticker tints for speaker chips: (background, text)
SPEAKER_CHIP = {
    "others": ("#e7f2fc", "#1f5f99"),  # sky
    "me": ("#e6f5ea", "#18712d"),  # green
}

FONT_FAMILIES = ["Inter", "Segoe UI", "Noto Sans KR", "Malgun Gothic"]


def font(px: int, weight: QFont.Weight = QFont.Weight.Normal, tracking: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(weight)
    if tracking:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


STYLESHEET = f"""
* {{ color: {INK}; }}
QMainWindow, #canvas {{ background: {CANVAS_SOFT}; }}

/* sidebar (app shell rows) */
#sidebar {{ background: {CANVAS_SOFT}; border-right: 1px solid {HAIRLINE}; }}
#sidebarTitle {{ color: {INK_MUTED}; padding: 4px 8px; }}
QListWidget#sessionList {{ background: transparent; border: none; outline: 0; }}
QListWidget#sessionList::item {{
    color: {INK_SECONDARY}; padding: 6px 10px; border-radius: 5px; margin: 1px 0;
}}
QListWidget#sessionList::item:hover {{ background: {ROW_HOVER}; }}
QListWidget#sessionList::item:selected {{ background: {ROW_ACTIVE}; color: {INK}; }}

/* top bar */
#topbar {{ background: {CANVAS}; border-bottom: 1px solid {HAIRLINE}; }}
#topbar QLabel {{ color: {INK_MUTED}; }}

/* buttons: one blue pill for the main action, 8px utility buttons for the rest */
QPushButton#primary {{
    background: {PRIMARY}; color: #ffffff; border: none; border-radius: 16px;
    padding: 6px 18px; font-weight: 500;
}}
QPushButton#primary:pressed {{ background: {PRIMARY_ACTIVE}; }}
QPushButton#primary:disabled {{ background: {INK_FAINT}; }}
QPushButton#stop {{
    background: {CANVAS}; color: {INK}; border: 1px solid {HAIRLINE}; border-radius: 16px;
    padding: 6px 18px; font-weight: 500;
}}
QPushButton#stop:pressed {{ background: {ROW_ACTIVE}; }}
QPushButton#utility {{
    background: {CANVAS}; color: {INK}; border: 1px solid {HAIRLINE}; border-radius: 8px;
    padding: 4px 14px;
}}
QPushButton#utility:pressed {{ background: {ROW_ACTIVE}; }}
QPushButton#utility:disabled {{ color: {INK_FAINT}; }}

/* inputs stay tight at 4px */
QLineEdit, QComboBox {{
    background: {CANVAS}; border: 1px solid #dddddd; border-radius: 4px; padding: 4px 6px;
    selection-background-color: {PRIMARY};
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {PRIMARY}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {CANVAS}; border: 1px solid {HAIRLINE}; selection-background-color: {ROW_ACTIVE};
    selection-color: {INK};
}}
QCheckBox {{ color: {INK_SECONDARY}; spacing: 6px; }}

/* document page */
#page {{ background: {CANVAS}; }}
#pageTitle {{ color: {INK}; }}
#property {{ color: {INK_MUTED}; }}
#propertyKey {{ color: {INK_FAINT}; }}
#callout {{
    background: {WARN_BG}; border-radius: 8px; color: {WARN_TEXT}; padding: 10px 14px;
}}
QTextBrowser#transcript {{ background: {CANVAS}; border: none; }}
#emptyState {{ background: {CANVAS_SOFT}; border-radius: 16px; color: {INK_MUTED}; padding: 32px; }}
QPushButton#jump {{
    background: {CANVAS}; color: {INK_SECONDARY}; border: 1px solid {HAIRLINE};
    border-radius: 14px; padding: 4px 12px;
}}

/* footer status */
#footer {{ background: {CANVAS_SOFT}; border-top: 1px solid {HAIRLINE}; }}
#footer QLabel {{ color: {INK_SECONDARY}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #d9d7d3; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #c4c1bc; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

/* mini mode: phone frame */
#phone {{ background: {CANVAS}; border: 1px solid {HAIRLINE}; border-radius: 22px; }}
#miniHeader {{ background: transparent; }}
#handle {{ background: #dcdad6; border-radius: 2px; }}
#statePill {{
    background: {CANVAS_SOFT}; color: {INK_MUTED}; border-radius: 10px; padding: 3px 10px;
}}
#statePill[live="true"] {{ background: #e7f2fc; color: {PRIMARY_ACTIVE}; }}
#navbar {{
    background: {CANVAS}; border-top: 1px solid {HAIRLINE};
    border-bottom-left-radius: 22px; border-bottom-right-radius: 22px;
}}
QToolButton#navButton {{
    background: transparent; border: none; border-radius: 10px; color: {INK_SECONDARY};
    padding: 4px 2px;
}}
QToolButton#navButton:hover {{ background: {CANVAS_SOFT}; }}
QToolButton#navButton:pressed {{ background: {ROW_ACTIVE}; }}
QToolButton#navButton:disabled {{ color: {INK_FAINT}; }}

QDialog {{ background: {CANVAS}; }}
QPlainTextEdit {{
    background: {CANVAS_SOFT}; border: 1px solid {HAIRLINE}; border-radius: 8px; padding: 8px;
}}
"""
