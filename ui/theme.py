"""Colours, spacing and the stylesheet, kept in one place.

Anything that decides how Flow looks belongs here rather than scattered
through the widgets, so a restyle is one file.
"""

# Surfaces, darkest to lightest. Blue-black rather than neutral grey: the
# cool cast is most of what makes the UI read as a tool, not a web page.
BG = "#10131a"
SURFACE = "#171b24"
SURFACE_HI = "#1f2532"
SURFACE_SUNK = "#0b0e14"
BORDER = "#262d3b"
BORDER_HI = "#364052"

TEXT = "#e8ecf3"
TEXT_DIM = "#97a3b6"
TEXT_FAINT = "#69758a"

ACCENT = "#36a9ff"
ACCENT_HI = "#7cc8ff"
ACCENT_SUNK = "#1b5c99"
# The accent as a wash: checked chips, selected rows, drop targets.
ACCENT_GLOW = "rgba(54,169,255,0.12)"

OK = "#34d399"
WARN = "#fbbf24"
ERROR = "#f87171"
# Client notes stand apart from internal ones in every notes view.
CLIENT = "#f0883e"
CLIENT_SUNK = "#3a2618"

# ShotGrid status short codes -> colour. Anything unknown falls back to grey.
STATUS_COLOURS = {
    "ip": ACCENT,        # in progress
    "rdy": "#a78bfa",    # ready to start
    "fin": OK,           # final
    "cmpt": OK,          # complete
    "apr": OK,           # approved
    "rev": "#f0abfc",    # pending review
    "hld": WARN,         # on hold
    "omt": TEXT_FAINT,   # omitted
    "na": TEXT_FAINT,
}

# Palette for generated thumbnails, so a project without an image still gets a
# stable, recognisable colour instead of flat grey.
TILE_COLOURS = [
    ("#1e3a5f", "#2d5a8f"), ("#3d2952", "#5c3d7a"), ("#1f4037", "#2d6a5a"),
    ("#4a2c2a", "#7a4340"), ("#2a3b52", "#3f5a7d"), ("#4a3b1f", "#7a6130"),
    ("#2f2a52", "#48407d"), ("#1f424a", "#2f6570"),
]

RADIUS = 8
RADIUS_SM = 5


def status_colour(code):
    return STATUS_COLOURS.get((code or "").lower(), TEXT_FAINT)


def tile_colours(name):
    """Same name always gets the same pair, so tiles stay put visually."""
    return TILE_COLOURS[sum(ord(c) for c in name or "?") % len(TILE_COLOURS)]


STYLE = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: 'Inter', 'Open Sans', 'Segoe UI', sans-serif;
    font-size: 13px;
}}

/* ---- header ---- */
#header {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {SURFACE_HI}, stop:1 {SURFACE});
    border-bottom: 1px solid {ACCENT_SUNK};
}}
/* The QWidget rule above paints every label {BG}, which on the {SURFACE}
   header shows as a dark block whose edge reads as a divider. Labels here are
   transparent; the avatar needs two ids to out-rank that and keep its fill. */
#header QLabel {{ background: transparent; }}
#header #avatar {{ background: {ACCENT_SUNK}; }}
#headerTitle {{ font-size: 16px; font-weight: 600; color: {TEXT}; }}
#crumb {{ color: {TEXT_FAINT}; font-size: 16px; padding: 0 2px; }}
#backBtn {{
    background: transparent; border: none; color: {TEXT_DIM};
    font-size: 13px; padding: 6px 10px; border-radius: {RADIUS_SM}px;
}}
#backBtn:hover {{ color: {TEXT}; background: {SURFACE_HI}; }}

#userChip {{
    background: {SURFACE_HI}; border: 1px solid {BORDER};
    border-radius: 13px; padding: 4px 12px 4px 4px; color: {TEXT_DIM};
}}
#avatar {{
    background: {ACCENT_SUNK}; color: {TEXT};
    border-radius: 10px; font-weight: 700; font-size: 10px;
}}

#taskSearch {{
    background: {SURFACE_SUNK}; border: 1px solid {BORDER};
    border-radius: 13px; padding: 5px 12px; margin-right: 10px;
    color: {TEXT}; font-size: 12px;
}}
#taskSearch:focus {{ border-color: {ACCENT}; background: {SURFACE}; }}
#searchResults {{
    background: {SURFACE}; border: 1px solid {BORDER_HI};
    border-radius: {RADIUS_SM}px; color: {TEXT}; padding: 4px;
    outline: none;
}}
#searchResults::item {{ padding: 5px 8px; border-radius: {RADIUS_SM}px; }}
#searchResults::item:selected {{
    background: {ACCENT_SUNK}; color: {TEXT};
}}

#termBtn {{
    background: transparent; border: 1px solid {BORDER}; border-radius: {RADIUS_SM}px;
    color: {TEXT_DIM}; padding: 5px 12px; margin-right: 10px; font-size: 12px;
}}
#termBtn:hover {{ border-color: {BORDER_HI}; color: {TEXT}; }}
#termBtn:checked {{
    color: {ACCENT_HI}; border-color: {ACCENT_SUNK}; background: {ACCENT_GLOW};
}}

/* ---- home page ---- */
#chip {{
    background: transparent; border: 1px solid {BORDER}; border-radius: 12px;
    color: {TEXT_DIM}; padding: 4px 12px; font-size: 12px;
}}
#chip:hover {{ border-color: {BORDER_HI}; color: {TEXT}; }}
#chip:checked {{
    color: {ACCENT_HI}; border-color: {ACCENT_SUNK}; background: {ACCENT_GLOW};
}}
#launchBtn {{
    background: {SURFACE_HI}; border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px; color: {TEXT}; font-size: 12px;
    padding: 3px 10px; margin: 4px 0;
}}
#launchBtn[split="true"] {{ padding-right: 22px; }}
#launchBtn:hover {{ border-color: {ACCENT}; color: {ACCENT_HI}; }}
#launchBtn::menu-indicator {{ image: none; width: 0; }}
#launchBtn::menu-button {{ border-left: 1px solid {BORDER}; width: 18px; }}
#sectionToggle {{
    background: transparent; border: none; color: {TEXT};
    font-size: 16px; font-weight: 600; padding: 0;
}}
#sectionToggle:hover {{ color: {ACCENT_HI}; }}

/* ---- tiles ---- */
#tile {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
}}
#tile:hover {{ background: {SURFACE_HI}; border-color: {ACCENT_SUNK}; }}
#tileThumb {{ border-radius: {RADIUS_SM}px; }}
#tileName {{ color: {TEXT}; font-weight: 600; font-size: 13px; }}
#tileSub {{ color: {TEXT_FAINT}; font-size: 11px; }}

/* ---- inputs ---- */
QLineEdit {{
    background: {SURFACE_SUNK}; border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px; padding: 7px 12px; color: {TEXT};
    selection-background-color: {ACCENT_SUNK};
}}
QLineEdit:focus {{ border-color: {ACCENT}; background: {SURFACE}; }}

QCheckBox {{ color: {TEXT_DIM}; spacing: 6px; }}
QCheckBox::indicator {{
    width: 13px; height: 13px; border-radius: 3px;
    border: 1px solid {BORDER_HI}; background: {SURFACE_SUNK};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* ---- tables ---- */
QTableWidget {{
    background: {BG}; border: none; gridline-color: transparent;
    alternate-background-color: {BG};
    selection-background-color: {ACCENT_GLOW};
    selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 0 12px; border-bottom: 1px solid {SURFACE}; }}
QTableWidget::item:selected {{
    color: {TEXT}; border-bottom: 1px solid {ACCENT_SUNK};
}}
QTableWidget::item:hover {{ background: {SURFACE}; }}
QHeaderView::section {{
    background: {BG}; color: {TEXT_FAINT}; border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px 12px; font-weight: 600; font-size: 10px;
    text-transform: uppercase; letter-spacing: 1.2px;
}}

/* ---- tabs ---- */
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_FAINT};
    padding: 10px 4px; margin-right: 22px; font-weight: 600; font-size: 13px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {TEXT_DIM}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

/* ---- context bar + status ---- */
#contextBar {{
    background: {SURFACE}; color: {TEXT_DIM};
    border-top: 1px solid {BORDER}; font-size: 12px;
}}
#contextBarEmpty {{
    background: {SURFACE}; color: {TEXT_FAINT};
    border-top: 1px solid {BORDER}; font-size: 12px;
}}
QStatusBar {{
    background: {SURFACE}; color: {TEXT_FAINT};
    border-top: 1px solid {BORDER}; font-size: 12px;
}}
QStatusBar::item {{ border: none; }}

/* ---- console ---- */
#console {{ background: {SURFACE_SUNK}; }}
#consoleBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
#consoleTitle {{ color: {TEXT}; font-weight: 600; font-size: 12px; }}
#consoleSource {{ color: {TEXT_FAINT}; padding-left: 8px; font-size: 11px; }}
#consoleBtn {{
    background: transparent; border: none; color: {TEXT_FAINT};
    padding: 3px 9px; border-radius: {RADIUS_SM}px; font-size: 12px;
}}
#consoleBtn:hover {{ color: {TEXT}; background: {SURFACE_HI}; }}
#consoleView {{
    background: {SURFACE_SUNK}; color: {TEXT_DIM}; border: none;
    selection-background-color: {ACCENT_SUNK};
}}

/* ---- combo boxes ---- */
QComboBox {{
    background: {SURFACE_SUNK}; border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px; padding: 5px 10px; color: {TEXT_DIM};
    min-height: 18px;
}}
QComboBox:hover {{ border-color: {BORDER_HI}; color: {TEXT}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_HI}; border: 1px solid {BORDER_HI};
    selection-background-color: {ACCENT_SUNK}; color: {TEXT};
    outline: none; padding: 4px;
}}
#filterLabel {{ color: {TEXT_FAINT}; font-size: 11px; }}
#warnText {{ color: {WARN}; font-size: 12px; }}
#errorText {{ color: {ERROR}; font-size: 12px; }}

/* ---- notes ---- */
#noteCard {{
    background: {SURFACE}; border: 1px solid {BORDER};
    border-left: 2px solid {ACCENT_SUNK}; border-radius: {RADIUS_SM}px;
}}
#replyCard {{
    background: {SURFACE_SUNK}; border: 1px solid {BORDER};
    border-left: 2px solid {BORDER_HI}; border-radius: {RADIUS_SM}px;
}}
#threadRail {{ background: {BORDER_HI}; border-radius: 1px; }}
#noteSubject {{ color: {TEXT}; font-weight: 600; font-size: 12px; }}
#noteBody {{ color: {TEXT_DIM}; font-size: 12px; }}
#noteCardClient {{
    background: {CLIENT_SUNK}; border: 1px solid {BORDER};
    border-left: 2px solid {CLIENT}; border-radius: {RADIUS_SM}px;
}}
#roleChipClient {{
    background: {CLIENT}; color: {BG}; border: 1px solid {CLIENT};
    border-radius: 3px; padding: 1px 6px; font-size: 9px;
    font-weight: 700; letter-spacing: 0.6px;
}}
#roleChip {{
    background: {SURFACE_HI}; color: {TEXT_FAINT}; border: 1px solid {BORDER};
    border-radius: 3px; padding: 1px 6px; font-size: 9px;
    font-weight: 700; letter-spacing: 0.6px;
}}

/* ---- task notes chat ---- */
#chatMine {{
    background: {ACCENT_SUNK}; border: 1px solid {ACCENT};
    border-radius: {RADIUS}px;
}}
#chatTheirs {{
    background: {SURFACE_HI}; border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}
#chatClient {{
    background: {CLIENT_SUNK}; border: 1px solid {CLIENT};
    border-radius: {RADIUS}px;
}}
#chatMine QLabel, #chatTheirs QLabel, #chatClient QLabel {{
    background: transparent;
}}
#chatMeta {{ color: {TEXT_FAINT}; font-size: 10px; }}
#scopeChip {{
    background: {SURFACE_SUNK}; color: {WARN}; border: 1px solid {BORDER};
    border-radius: 3px; padding: 1px 6px; font-size: 10px;
}}
#dayDivider {{
    color: {TEXT_FAINT}; font-size: 10px; font-weight: 600;
    letter-spacing: 0.6px; padding: 6px 0 2px 0;
}}
#chatBody {{ color: {TEXT}; font-size: 12px; }}

/* ---- preflight ---- */
#checkOk {{ color: {OK}; font-size: 12px; }}
#checkWarn {{ color: {WARN}; font-size: 12px; }}
#checkError {{ color: {ERROR}; font-size: 12px; }}
#checkSection {{
    color: {TEXT_FAINT}; font-size: 10px; font-weight: 700;
    letter-spacing: 0.6px;
}}

/* ---- drop zone ---- */
#dropZone {{
    background: {SURFACE}; border: 1px dashed {BORDER_HI};
    border-radius: {RADIUS}px;
}}
#dropZoneActive {{
    background: {ACCENT_GLOW}; border: 1px dashed {ACCENT};
    border-radius: {RADIUS}px;
}}
#dropHint {{ color: {TEXT_FAINT}; font-size: 12px; }}

/* ---- empty states ---- */
#emptyIcon {{ color: {BORDER_HI}; font-size: 34px; }}
#emptyTitle {{ color: {TEXT_DIM}; font-size: 14px; font-weight: 600; }}
#emptyHint {{ color: {TEXT_FAINT}; font-size: 12px; }}

/* ---- menus ---- */
QMenu {{
    background: {SURFACE_HI}; border: 1px solid {BORDER_HI};
    border-radius: {RADIUS_SM}px; padding: 5px;
}}
QMenu::item {{ padding: 6px 26px 6px 12px; border-radius: 4px; color: {TEXT}; }}
QMenu::item:selected {{ background: {ACCENT_SUNK}; }}
QMenu::item:disabled {{ color: {TEXT_FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

/* ---- scrollbars ---- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {BORDER_HI}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_SUNK}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; }}
QScrollBar::handle:horizontal {{
    background: {BORDER_HI}; border-radius: 4px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT_SUNK}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {BORDER}; height: 1px; }}
QToolTip {{
    background: {SURFACE_HI}; color: {TEXT}; border: 1px solid {BORDER_HI};
    padding: 5px 8px; border-radius: 4px;
}}
"""
