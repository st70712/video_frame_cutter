import pytest
from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QStyle

from video_frame_cutter.__main__ import configure_application
from video_frame_cutter.models import AnalysisSettings
from video_frame_cutter.ui import theme
from video_frame_cutter.ui.main_window import MainWindow
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
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.ini"
    monkeypatch.setattr(theme, "settings", lambda: QSettings(str(path), QSettings.Format.IniFormat))
    return path


@pytest.fixture
def app(qapp, settings_file):
    palette, stylesheet = QPalette(qapp.palette()), qapp.styleSheet()
    yield qapp
    theme.set_preference(qapp, "system")
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


def test_disabled_standard_icon_has_a_visible_glyph(app):
    theme.apply(app, Qt.ColorScheme.Dark)
    icon = theme.with_disabled_glyph(app.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
    image = icon.pixmap(QSize(16, 16), QIcon.Mode.Disabled).toImage()
    centre = image.pixelColor(image.width() // 2, image.height() // 2)
    assert centre.alpha() > 0
    assert centre.name() == theme.DISABLED_GLYPH
    for colors in (theme.LIGHT, theme.DARK):
        assert contrast(theme.DISABLED_GLYPH, colors.button) >= 2.5


def test_configure_application_follows_system_scheme(app):
    configure_application(app)
    assert theme.preference() == "system"
    assert theme.current() is theme.resolve(app.styleHints().colorScheme())


def test_preference_overrides_system_and_persists(app):
    assert theme.set_preference(app, "dark") is theme.DARK
    assert theme.settings().value(theme.SETTINGS_KEY) == "dark"
    assert theme.install(app) is theme.DARK
    assert theme.set_preference(app, "light") is theme.LIGHT
    assert theme.install(app) is theme.LIGHT
    assert theme.set_preference(app, "system") is theme.resolve(theme.detect(app))
    with pytest.raises(ValueError):
        theme.set_preference(app, "sepia")


def test_fixed_preference_ignores_system_scheme_changes(app):
    theme.set_preference(app, "dark")
    theme.install(app)
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
        assert theme.current() is theme.DARK
        theme.set_preference(app, "system")
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
        assert theme.current() is theme.DARK
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
        assert theme.current() is theme.LIGHT
    finally:
        app.styleHints().setColorScheme(Qt.ColorScheme.Unknown)


def test_main_window_theme_menu(app, qtbot):
    theme.set_preference(app, "light")
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.theme_action.text() == "外觀"
    assert window.theme_actions["light"].isChecked()
    window.theme_actions["dark"].trigger()
    assert theme.current() is theme.DARK
    assert theme.preference() == "dark"
    assert window.theme_actions["dark"].isChecked()
    assert not window.theme_actions["light"].isChecked()
