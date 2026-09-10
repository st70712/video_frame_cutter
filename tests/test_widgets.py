from PIL import Image
from PySide6.QtCore import Qt

from video_frame_cutter.models import AnalysisSettings, CropRect, ExportSettings
from video_frame_cutter.ui.widgets import (
    AnalysisSettingsDialog,
    CropDialog,
    ExportSettingsDialog,
    Timeline,
)
from video_frame_cutter.workers import Job


def test_analysis_settings_dialog_roundtrip(qtbot):
    original = AnalysisSettings(0.125, 0.034, 0.65, 1.2, 640, 3.0)
    dialog = AnalysisSettingsDialog(original)
    qtbot.addWidget(dialog)

    assert dialog.settings() == original
    dialog.threshold.setValue(0.25)
    dialog.reject()
    assert original == AnalysisSettings(0.125, 0.034, 0.65, 1.2, 640, 3.0)


def test_export_settings_dialog_options(qtbot):
    original = ExportSettings(1280, 720, 88, True)
    dialog = ExportSettingsDialog(original, 3, "pptx", True)
    qtbot.addWidget(dialog)

    assert dialog.kind() == "pptx"
    assert dialog.timestamp.isEnabled()
    assert dialog.selected_only.isChecked()
    assert dialog.settings() == original
    dialog.format.setCurrentIndex(dialog.format.findData("jpg"))
    assert not dialog.timestamp.isEnabled()
    dialog.reject()
    assert original == ExportSettings(1280, 720, 88, True)

    no_selection = ExportSettingsDialog(original, 0, selected_only=True)
    qtbot.addWidget(no_selection)
    assert not no_selection.selected_only.isEnabled()
    assert not no_selection.selected_only.isChecked()


def test_crop_dialog(qtbot):
    dialog = CropDialog(Image.new("RGB", (320, 180), "red"), CropRect(), ExportSettings(), 3)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.fields[0].setValue(25)
    assert dialog.canvas.crop.left == 0.25
    assert dialog.batch.isEnabled()
    assert not dialog.preview.pixmap().isNull()
    dialog.reject()


def test_timeline_seek(qtbot):
    timeline = Timeline()
    qtbot.addWidget(timeline)
    timeline.resize(800, 160)
    timeline.duration = 100
    timeline.show()
    with qtbot.waitSignal(timeline.seek) as captured:
        qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton)
    assert 0 <= captured.args[0] <= 100
    timeline.set_zoom(10)
    assert timeline.span == 10


def test_job(qtbot):
    job = Job(lambda cancel, progress: 42)
    with qtbot.waitSignal(job.result) as captured:
        job.start()
    assert captured.args == [42]
    assert job.wait(3000)
