"""Light and dark colour themes that follow the operating system appearance.

Fusion takes its widget colours from the application palette, so the palette carries
the theme and the stylesheet only adds spacing and a few accent colours. Custom painted
widgets read :func:`current` at paint time so a system theme change repaints them too.
"""

from dataclasses import dataclass

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap


@dataclass(frozen=True)
class Theme:
    window: str
    panel: str
    button: str
    text: str
    disabled_text: str
    base: str
    border: str
    highlight: str
    highlight_text: str
    accent: str
    tooltip_bg: str
    tooltip_fg: str
    timeline_bg: str
    timeline_fg: str
    curve: str
    marker: str
    marker_review: str
    playhead: str


LIGHT = Theme(
    window="#f7f8f8",
    panel="#e9eeec",
    button="#f0f0f0",
    text="#222b29",
    disabled_text="#8b9592",
    base="#ffffff",
    border="#d6dedb",
    highlight="#d4eee6",
    highlight_text="#174e43",
    accent="#228673",
    tooltip_bg="#293a34",
    tooltip_fg="#ffffff",
    timeline_bg="#f0f2f2",
    timeline_fg="#566160",
    curve="#269486",
    marker="#147c6f",
    marker_review="#be6d22",
    playhead="#cc4e48",
)

DARK = Theme(
    window="#1c1f1e",
    panel="#262b2a",
    button="#2b3130",
    text="#e6ebe9",
    disabled_text="#7d8784",
    base="#242827",
    border="#3a4240",
    highlight="#2b5a4f",
    highlight_text="#e8f6f1",
    accent="#3fb8a6",
    tooltip_bg="#e6ebe9",
    tooltip_fg="#1c1f1e",
    timeline_bg="#202524",
    timeline_fg="#a3adaa",
    curve="#3fb8a6",
    marker="#3bbfa8",
    marker_review="#e39a4e",
    playhead="#ef6a63",
)

PREFERENCES = ("system", "light", "dark")
SETTINGS_KEY = "appearance/theme"
_current = LIGHT
_preference = "system"


def current():
    return _current


def resolve(scheme):
    return DARK if scheme == Qt.ColorScheme.Dark else LIGHT


def detect(app):
    return app.styleHints().colorScheme()


def build_palette(theme):
    palette = QPalette()
    role = QPalette.ColorRole
    for color_role, color in (
        (role.Window, theme.window),
        (role.WindowText, theme.text),
        (role.Base, theme.base),
        (role.AlternateBase, theme.window),
        (role.Text, theme.text),
        (role.Button, theme.button),
        (role.ButtonText, theme.text),
        (role.Highlight, theme.highlight),
        (role.HighlightedText, theme.highlight_text),
        (role.ToolTipBase, theme.tooltip_bg),
        (role.ToolTipText, theme.tooltip_fg),
        (role.PlaceholderText, theme.disabled_text),
        (role.Link, theme.accent),
    ):
        palette.setColor(color_role, QColor(color))
    disabled = QPalette.ColorGroup.Disabled
    for color_role in (role.Text, role.WindowText, role.ButtonText):
        palette.setColor(disabled, color_role, QColor(theme.disabled_text))
    return palette


DISABLED_GLYPH = "#858e8b"


def with_disabled_glyph(icon):
    """Give a Fusion standard icon an explicit, visible disabled glyph.

    Fusion paints the normal glyph in the palette text colour, but it derives the disabled
    glyph by blending towards the Disabled Window colour, which on a dark theme leaves it
    almost as dark as the button face. A mid grey reads on both themes, so the disabled
    pixmaps never need refreshing when the theme changes.
    """
    result = QIcon(icon)
    for size in icon.availableSizes() or [QSize(16, 16), QSize(32, 32)]:
        source = icon.pixmap(size)
        glyph = QPixmap(source.size())
        glyph.setDevicePixelRatio(source.devicePixelRatio())
        glyph.fill(Qt.GlobalColor.transparent)
        painter = QPainter(glyph)
        painter.drawPixmap(0, 0, source)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(glyph.rect(), QColor(DISABLED_GLYPH))
        painter.end()
        result.addPixmap(glyph, QIcon.Mode.Disabled)
    return result


def build_stylesheet(theme):
    return f"""
        QToolBar {{ border: 0; spacing: 6px; padding: 6px; background: {theme.panel}; }}
        QPushButton {{ padding: 5px 9px; }}
        QListWidget {{ border: 1px solid {theme.border}; }}
        QListWidget::item:selected {{
            background: {theme.highlight}; color: {theme.highlight_text};
        }}
        QProgressBar::chunk {{ background: {theme.accent}; }}
        QToolTip {{ color: {theme.tooltip_fg}; background: {theme.tooltip_bg}; border: 0; }}
    """


def apply(app, scheme=None):
    global _current
    _current = resolve(detect(app) if scheme is None else scheme)
    app.setPalette(build_palette(_current))
    app.setStyleSheet(build_stylesheet(_current))
    return _current


def settings():
    return QSettings()


def preference():
    return _preference


def scheme_for(app, preference):
    if preference == "light":
        return Qt.ColorScheme.Light
    if preference == "dark":
        return Qt.ColorScheme.Dark
    return detect(app)


def set_preference(app, preference):
    """Apply ``system``, ``light`` or ``dark`` and remember it for the next launch."""
    global _preference
    if preference not in PREFERENCES:
        raise ValueError(f"unknown theme preference: {preference!r}")
    _preference = preference
    settings().setValue(SETTINGS_KEY, preference)
    return apply(app, scheme_for(app, preference))


def install(app):
    global _preference
    stored = settings().value(SETTINGS_KEY, "system")
    _preference = stored if stored in PREFERENCES else "system"
    app.styleHints().colorSchemeChanged.connect(
        lambda scheme: apply(app, scheme) if _preference == "system" else None
    )
    return apply(app, scheme_for(app, _preference))
