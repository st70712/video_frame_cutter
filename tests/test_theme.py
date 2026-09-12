import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QPushButton, QStyle, QWidget

from video_frame_cutter.__main__ import configure_application
from video_frame_cutter.models import AnalysisSettings
from video_frame_cutter.ui import theme
from video_frame_cutter.ui.widgets import AnalysisSettingsDialog, Timeline

SCHEMES = [Qt.ColorScheme.Light, Qt.ColorScheme.Dark]


def luminance(color):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def contrast(foreground, background):
    light, dark = sorted(
        (luminance(QColor(foreground)), luminance(QColor(background))), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)


@pytest.fixture
def app(qapp):
    palette, stylesheet = QPalette(qapp.palette()), qapp.styleSheet()
    yield qapp
    theme.apply(qapp, Qt.ColorScheme.Light)
    qapp.setPalette(palette)
    qapp.setStyleSheet(stylesheet)


def test_resolve_scheme():
    assert theme.resolve(Qt.ColorScheme.Dark) is theme.DARK
    assert theme.resolve(Qt.ColorScheme.Light) is theme.LIGHT
    assert theme.resolve(Qt.ColorScheme.Unknown) is theme.LIGHT


@pytest.mark.parametrize("scheme", SCHEMES, ids=lambda scheme: scheme.name.lower())
def test_palette_text_is_readable(app, scheme):
    colors = theme.apply(app, scheme)
    palette = app.palette()
    role = QPalette.ColorRole
    for foreground, background in (
        (role.Text, role.Base),
        (role.WindowText, role.Window),
        (role.ButtonText, role.Button),
        (role.HighlightedText, role.Highlight),
        (role.ToolTipText, role.ToolTipBase),
    ):
        assert contrast(palette.color(foreground), palette.color(background)) >= 4.5
    assert contrast(palette.color(role.WindowText), colors.panel) >= 4.5
    assert contrast(colors.timeline_fg, colors.timeline_bg) >= 4.5
    for accent in (colors.curve, colors.marker, colors.marker_review, colors.playhead):
        assert contrast(accent, colors.timeline_bg) >= 3


def test_analysis_dialog_fields_are_readable_in_dark(app, qtbot):
    theme.apply(app, Qt.ColorScheme.Dark)
    dialog = AnalysisSettingsDialog(AnalysisSettings())
    qtbot.addWidget(dialog)
    for control in (dialog.threshold, dialog.width):
        palette = control.palette()
        assert contrast(palette.text().color(), palette.base().color()) >= 4.5
    assert "QWidget {" not in app.styleSheet()


@pytest.mark.parametrize("scheme", SCHEMES, ids=lambda scheme: scheme.name.lower())
def test_timeline_paints_theme_background(app, qtbot, scheme):
    colors = theme.apply(app, scheme)
    timeline = Timeline()
    qtbot.addWidget(timeline)
    timeline.resize(800, 160)
    image = timeline.grab().toImage()
    assert image.pixelColor(image.width() - 3, image.height() - 3).name() == colors.timeline_bg


def test_disabled_standard_icon_stays_visible_in_dark(app, qtbot):
    theme.apply(app, Qt.ColorScheme.Dark)
    container = QWidget()
    qtbot.addWidget(container)
    container.resize(40, 32)
    button = QPushButton(container)
    button.setGeometry(0, 0, 40, 32)
    icon = theme.with_disabled_glyph(
        button.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
    )
    button.setIcon(icon)
    button.setEnabled(False)
    image = container.grab().toImage()
    face = image.pixelColor(6, 16)
    assert face.lightness() < 96
    contrasting = sum(
        1
        for x in range(image.width())
        for y in range(image.height())
        if abs(image.pixelColor(x, y).lightness() - face.lightness()) > 30
    )
    assert contrasting > 20


def test_configure_application_follows_system_scheme(app):
    configure_application(app)
    assert theme.current() is theme.resolve(app.styleHints().colorScheme())
