import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox

from video_frame_cutter.models import AnalysisSettings, CropRect, ExportSettings
from video_frame_cutter.ui import widgets
from video_frame_cutter.ui.widgets import (
    AnalysisSettingsDialog,
    CropDialog,
    ExportSettingsDialog,
    Timeline,
)
from video_frame_cutter.workers import Job


def test_analysis_settings_dialog_roundtrip(qtbot):
    original = AnalysisSettings(0.125, 0.034, 0.65, 1.2, 640, 3.0, 65, 125)
    dialog = AnalysisSettingsDialog(original, 180)
    qtbot.addWidget(dialog)

    assert dialog.settings() == original
    assert dialog.start.text() == "00:01:05"
    assert dialog.end.text() == "00:02:05"
    dialog.threshold.setValue(0.25)
    dialog.reject()
    assert original == AnalysisSettings(0.125, 0.034, 0.65, 1.2, 640, 3.0, 65, 125)


def test_analysis_time_point_accepts_hh_mm_ss_beyond_24_hours(qtbot):
    dialog = AnalysisSettingsDialog(AnalysisSettings(), 30 * 3600)
    qtbot.addWidget(dialog)

    dialog.start.lineEdit().setText("25:01:02")
    dialog.start.interpretText()

    assert dialog.start.value() == 25 * 3600 + 62
    assert dialog.start.text() == "25:01:02"


def test_analysis_settings_dialog_validates_video_range(qtbot):
    dialog = AnalysisSettingsDialog(AnalysisSettings(start_seconds=10.0), 3.0)
    qtbot.addWidget(dialog)
    start_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)

    assert dialog.start.value() < dialog.end.value()
    assert dialog.settings().end_seconds is None
    assert start_button.isEnabled()
    dialog.start.setValue(dialog.end.value())
    assert not start_button.isEnabled()


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
    dialog = CropDialog(
        Image.new("RGB", (320, 180), "red"), CropRect(), ExportSettings(), 3, 5
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.fields[0].setValue(25)
    assert dialog.canvas.crop.left == 0.25
    assert dialog.apply_current.isChecked()
    assert dialog.apply_selected.isEnabled()
    assert dialog.apply_all.isEnabled()
    dialog.apply_all.setChecked(True)
    assert dialog.apply_all.isChecked()
    assert not dialog.apply_current.isChecked()
    assert not dialog.apply_selected.isChecked()
    assert not dialog.preview.pixmap().isNull()
    dialog.reject()


def test_crop_dialog_magic_button_updates_crop_and_fields(qtbot, slide_image):
    image, box = slide_image
    dialog = CropDialog(image, CropRect(), ExportSettings(), 1, 5)
    qtbot.addWidget(dialog)
    dialog.show()

    qtbot.mouseClick(dialog.magic, Qt.MouseButton.LeftButton)

    crop = dialog.canvas.crop
    assert crop != CropRect()
    assert crop.left * image.width < box[0] and crop.right * image.width > box[2]
    for field, value in zip(dialog.fields, (crop.left, crop.top, crop.right, crop.bottom)):
        assert field.value() == pytest.approx(value * 100, abs=0.01)
    assert not dialog.preview.pixmap().isNull()
    assert dialog.hint.text()
    dialog.reject()


def test_crop_dialog_magic_button_is_a_no_op_when_pressed_again(qtbot, slide_image):
    image, _ = slide_image
    dialog = CropDialog(image, CropRect(), ExportSettings(), 1, 5)
    qtbot.addWidget(dialog)

    qtbot.mouseClick(dialog.magic, Qt.MouseButton.LeftButton)
    once = dialog.canvas.crop
    qtbot.mouseClick(dialog.magic, Qt.MouseButton.LeftButton)

    assert dialog.canvas.crop == once
    dialog.reject()


def test_crop_dialog_magic_plan_is_cleared_by_a_manual_edit(qtbot, slide_image):
    image, _ = slide_image
    dialog = CropDialog(image, CropRect(), ExportSettings(), 1, 5)
    qtbot.addWidget(dialog)

    assert dialog.magic_plan() is None
    qtbot.mouseClick(dialog.magic, Qt.MouseButton.LeftButton)
    seed, settings = dialog.magic_plan()
    assert seed == CropRect()
    assert settings.aspect == (1920, 1080)

    dialog.fields[0].setValue(5)

    assert dialog.magic_plan() is None
    dialog.reject()


def test_crop_dialog_reports_when_no_text_is_found(qtbot):
    dialog = CropDialog(Image.new("RGB", (320, 180), "red"), CropRect(), ExportSettings(), 1, 5)
    qtbot.addWidget(dialog)

    qtbot.mouseClick(dialog.magic, Qt.MouseButton.LeftButton)

    assert dialog.canvas.crop == CropRect()
    assert dialog.hint.text() == "沒有偵測到可縮小的範圍"
    dialog.reject()


def test_crop_dialog_magic_failure_is_reported_inline(qtbot, monkeypatch, slide_image):
    image, _ = slide_image
    dialog = CropDialog(image, CropRect(), ExportSettings(), 1, 5)
    qtbot.addWidget(dialog)

    def boom(*args, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(widgets, "detect_crop", boom)
    qtbot.mouseClick(dialog.magic, Qt.MouseButton.LeftButton)

    assert dialog.canvas.crop == CropRect()
    assert dialog.magic.isEnabled()
    assert dialog.magic_plan() is None
    assert "RuntimeError" in dialog.hint.text()
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
