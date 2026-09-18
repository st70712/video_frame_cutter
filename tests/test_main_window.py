import shutil
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

import video_frame_cutter.ui.main_window as main_window_module
from video_frame_cutter.analysis import AnalysisResult
from video_frame_cutter.eeclass import EeclassIndex
from video_frame_cutter.models import CropRect, ExportSettings, Marker
from video_frame_cutter.ui.main_window import MainWindow
from video_frame_cutter.ui.widgets import AnalysisSettingsDialog, CropDialog, ExportSettingsDialog


def test_marker_panel_fills_sidebar_and_actions_share_toolbar(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.marker_list.viewport().height() > 0)

    assert window.toolBarArea(window.toolbar) == Qt.ToolBarArea.TopToolBarArea
    assert [action.text() for action in window.toolbar.actions()] == [
        "開啟影片",
        "從 EE-Class 下載",
        "開啟專案",
        "儲存專案",
        "",
        "復原",
        "重做",
        "",
        "分析畫面變化",
        "匯出",
        "",
        "外觀",
    ]
    assert window.marker_list.viewport().height() >= 8 * 62

    window.resize(window.minimumSize())
    qtbot.wait(1)
    assert window.marker_list.height() >= window.marker_list.minimumHeight()


def test_eeclass_download_loads_video_in_one_background_job(
    qtbot, video_path, tmp_path, monkeypatch
):
    destination = tmp_path / "downloaded.mp4"
    media = object()
    indexes = (
        EeclassIndex(0, "課程片頭", "1"),
        EeclassIndex(50, "同影格索引", "2"),
        EeclassIndex(100, "Slide 1", "3"),
    )

    class FakeDialog:
        def __init__(self, profile, parent):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selection(self):
            return media, destination, indexes

        def deleteLater(self):
            pass

    def fake_download(request, output, cancel, progress):
        assert request is media
        progress(50)
        shutil.copyfile(video_path, output)
        progress(100)
        return Path(output)

    monkeypatch.setattr(main_window_module, "EeclassDownloadDialog", FakeDialog)
    monkeypatch.setattr(main_window_module, "create_eeclass_profile", lambda parent: object())
    monkeypatch.setattr(main_window_module, "download_eeclass_mp4", fake_download)
    window = MainWindow()
    window.confirm_discard = lambda: True
    qtbot.addWidget(window)

    window.choose_eeclass_video()
    qtbot.waitUntil(lambda: window.info is not None and not window.jobs, timeout=15000)

    assert window.info.path == destination
    assert [(marker.frame.index, marker.label) for marker in window.markers] == [
        (0, "課程片頭"),
        (1, "Slide 1"),
    ]
    assert all(marker.source == "eeclass" for marker in window.markers)
    assert "EE-Class｜課程片頭" in window.marker_list.item(0).text()
    assert window.dirty
    assert window.progress.value() == 100
    assert window.statusBar().currentMessage() == "影片已載入"
    window.dirty = False
    window.close()


def test_eeclass_download_respects_discard_cancellation(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.confirm_discard = lambda: False
    created = []
    monkeypatch.setattr(
        main_window_module, "create_eeclass_profile", lambda parent: created.append(parent)
    )

    window.choose_eeclass_video()

    assert not created


def test_marker_workflow(qtbot, video_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    window = MainWindow()
    window.confirm_discard = lambda: True
    qtbot.addWidget(window)
    window.show()
    window.load_video(video_path)
    qtbot.waitUntil(
        lambda: window.info is not None and window.frame_image is not None, timeout=15000
    )
    assert not window.export_action.isEnabled()
    assert window.frame.index == 0
    window.add_marker()
    assert window.export_action.isEnabled()
    assert len(window.markers) == 1
    window.seek(window.info.frames[12].seconds)
    qtbot.waitUntil(lambda: window.frame_image is not None)
    window.add_marker()
    assert len(window.markers) == 2
    uid = window.markers[1].uid
    window.move_marker(uid, window.info.frames[20].seconds)
    assert window.markers[1].frame.index == 20
    window.undo_stack.undo()
    assert window.markers[1].frame.index == 12
    window.undo_stack.redo()
    assert window.markers[1].frame.index == 20
    window.select_uid(uid)
    window.delete_markers()
    assert len(window.markers) == 1
    window.undo_stack.undo()
    assert len(window.markers) == 2
    window.dirty = False
    window.close()
    qtbot.waitUntil(lambda: not window.jobs, timeout=10000)


def test_analysis_action_preserves_manual_markers_and_undo(qtbot, video_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        AnalysisSettingsDialog, "exec", lambda self: QDialog.DialogCode.Accepted
    )
    window = MainWindow()
    window.confirm_discard = lambda: True
    errors = []
    window.error = errors.append
    qtbot.addWidget(window)
    window.show()
    window.load_video(video_path)
    qtbot.waitUntil(
        lambda: window.frame_image is not None and not window.jobs, timeout=15000,
    )
    window.add_marker()
    manual_uid = window.markers[0].uid
    window.start_analysis()
    qtbot.waitUntil(lambda: not window.jobs, timeout=15000)
    assert not errors
    assert len(window.timeline.curve) == len(window.info.frames)
    assert manual_uid in [marker.uid for marker in window.markers]
    assert any(marker.source == "automatic" for marker in window.markers)
    applied = [marker.uid for marker in window.markers]
    window.undo_stack.undo()
    assert [marker.uid for marker in window.markers] == [manual_uid]
    window.undo_stack.redo()
    assert [marker.uid for marker in window.markers] == applied
    window.dirty = False
    window.close()
    qtbot.waitUntil(lambda: not window.jobs, timeout=10000)


def test_scoped_analysis_preserves_results_outside_range(qtbot, video_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)

    def accept_range(dialog):
        dialog.start.setValue(dialog.parent().info.frames[10].seconds)
        dialog.end.setValue(dialog.parent().info.frames[20].seconds)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AnalysisSettingsDialog, "exec", accept_range)
    window = MainWindow()
    window.confirm_discard = lambda: True
    qtbot.addWidget(window)
    window.show()
    window.load_video(video_path)
    qtbot.waitUntil(
        lambda: window.frame_image is not None and not window.jobs, timeout=15000,
    )
    before = Marker(window.info.frames[2], source="automatic")
    replaced = Marker(window.info.frames[12], source="automatic")
    after = Marker(window.info.frames[25], source="automatic")
    candidate = Marker(window.info.frames[15], source="automatic")
    window.markers = [before, replaced, after]
    window.refresh_markers()
    window.timeline.curve = [
        (before.frame.seconds, 0.1),
        (replaced.frame.seconds, 0.2),
        (after.frame.seconds, 0.3),
    ]

    def fake_analyze(info, settings, cancel, progress):
        progress(100)
        assert settings.includes(candidate.frame.seconds)
        return AnalysisResult([candidate], [(candidate.frame.seconds, 0.9)])

    monkeypatch.setattr(main_window_module, "analyze", fake_analyze)
    window.start_analysis()
    qtbot.waitUntil(lambda: not window.jobs, timeout=10000)

    assert [marker.uid for marker in window.markers] == [before.uid, candidate.uid, after.uid]
    assert window.timeline.curve == [
        (before.frame.seconds, 0.1),
        (candidate.frame.seconds, 0.9),
        (after.frame.seconds, 0.3),
    ]
    window.dirty = False
    window.close()
    qtbot.waitUntil(lambda: not window.jobs, timeout=10000)


def test_edit_crop_applies_to_all_markers_as_one_undo(qtbot, video_path, monkeypatch):
    window = MainWindow()
    window.confirm_discard = lambda: True
    qtbot.addWidget(window)
    window.show()
    window.load_video(video_path)
    qtbot.waitUntil(
        lambda: window.frame_image is not None and not window.jobs, timeout=15000,
    )
    window.markers = [Marker(window.info.frames[index]) for index in (2, 12, 25)]
    window.refresh_markers()
    source_image = window.frame_image
    window.select_uid(window.markers[0].uid)
    crop = CropRect(0.1, 0.2, 0.8, 0.9)

    def accept_all(dialog):
        dialog.set_crop(crop)
        dialog.apply_all.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CropDialog, "exec", accept_all)
    monkeypatch.setattr(
        window,
        "seek",
        lambda seconds, callback=None: callback(source_image) if callback else None,
    )
    window.undo_stack.clear()

    window.edit_crop()
    assert [marker.crop for marker in window.markers] == [crop, crop, crop]
    assert all(marker.modified for marker in window.markers)
    assert window.undo_stack.count() == 1
    window.undo_stack.undo()
    assert [marker.crop for marker in window.markers] == [CropRect()] * 3
    window.dirty = False
    window.close()
    qtbot.waitUntil(lambda: not window.jobs, timeout=10000)


def test_magic_crop_applies_a_separate_crop_per_marker_as_one_undo(
    qtbot, video_path, monkeypatch
):
    window = MainWindow()
    window.confirm_discard = lambda: True
    qtbot.addWidget(window)
    window.show()
    window.load_video(video_path)
    qtbot.waitUntil(
        lambda: window.frame_image is not None and not window.jobs, timeout=15000,
    )
    window.markers = [Marker(window.info.frames[index]) for index in (2, 12, 25)]
    window.refresh_markers()
    source_image = window.frame_image
    window.select_uid(window.markers[0].uid)
    seed = CropRect(0.1, 0.1, 0.9, 0.9)
    current = CropRect(0.2, 0.2, 0.7, 0.7)
    detected = []

    def magic_then_apply_all(dialog):
        dialog.set_crop(current)
        dialog.magic_seed = seed
        dialog.magic_settings = None
        dialog.apply_all.setChecked(True)
        return QDialog.DialogCode.Accepted

    def fake_detect(image, crop, settings):
        # A different rect per decoded frame, so a shared crop would be visible as a tie.
        detected.append(crop)
        step = 0.05 * len(detected)
        return SimpleNamespace(crop=CropRect(step, step, 0.8, 0.8), kind="text", lines=1)

    monkeypatch.setattr(CropDialog, "exec", magic_then_apply_all)
    monkeypatch.setattr(main_window_module, "detect_crop", fake_detect)
    monkeypatch.setattr(
        window,
        "seek",
        lambda seconds, callback=None: callback(source_image) if callback else None,
    )
    window.undo_stack.clear()

    window.edit_crop()
    qtbot.waitUntil(lambda: not window.jobs, timeout=15000)

    crops = [marker.crop for marker in window.markers]
    assert crops[0] == current
    assert crops[1] == CropRect(0.05, 0.05, 0.8, 0.8)
    assert crops[2] == CropRect(0.1, 0.1, 0.8, 0.8)
    assert len(set(crops)) == 3
    assert detected == [seed, seed]
    assert all(marker.modified for marker in window.markers)
    assert window.undo_stack.count() == 1
    window.undo_stack.undo()
    assert [marker.crop for marker in window.markers] == [CropRect()] * 3
    window.dirty = False
    window.close()
    qtbot.waitUntil(lambda: not window.jobs, timeout=10000)


def test_parameter_dialog_cancellation_has_no_side_effects(qtbot, monkeypatch):
    monkeypatch.setattr(
        AnalysisSettingsDialog, "exec", lambda self: QDialog.DialogCode.Rejected
    )
    monkeypatch.setattr(ExportSettingsDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    window = MainWindow()
    qtbot.addWidget(window)
    window.info = SimpleNamespace(duration=3.0)
    launched = []
    window.launch_job = lambda *args: launched.append(args)
    analysis = window.analysis_settings()
    export = window.export_settings()

    window.start_analysis()
    window.choose_export()
    assert not launched
    assert window.analysis_settings() == analysis
    assert window.export_settings() == export
    assert not window.dirty


def test_export_dialog_filters_selected_markers(qtbot, video_path, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    window = MainWindow()
    window.confirm_discard = lambda: True
    qtbot.addWidget(window)
    window.show()
    window.load_video(video_path)
    qtbot.waitUntil(
        lambda: window.frame_image is not None and not window.jobs, timeout=15000,
    )
    window.add_marker()
    selected_uid = window.markers[0].uid
    window.seek(window.info.frames[12].seconds)
    qtbot.waitUntil(lambda: window.frame_image is not None)
    window.add_marker()
    window.select_uid(selected_uid)
    output = tmp_path / "selected.pptx"
    captured = {}

    def accept_export(dialog):
        dialog.format.setCurrentIndex(dialog.format.findData("pptx"))
        dialog.output_width.setValue(640)
        dialog.output_height.setValue(360)
        dialog.quality.setValue(90)
        dialog.timestamp.setChecked(True)
        dialog.selected_only.setChecked(True)
        return QDialog.DialogCode.Accepted

    def fake_export(info, markers, settings, destination, kind, cancel, progress, overwrite):
        captured.update(
            markers=markers,
            settings=settings,
            destination=Path(destination),
            kind=kind,
            overwrite=overwrite,
        )
        return Path(destination)

    monkeypatch.setattr(ExportSettingsDialog, "exec", accept_export)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    monkeypatch.setattr(main_window_module, "export_markers", fake_export)
    window.dirty = False

    window.choose_export()
    qtbot.waitUntil(lambda: not window.jobs, timeout=10000)

    assert [marker.uid for marker in captured["markers"]] == [selected_uid]
    assert captured["settings"] == ExportSettings(640, 360, 90, True)
    assert captured["destination"] == output
    assert captured["kind"] == "pptx"
    assert not captured["overwrite"]
    assert window.export_kind == "pptx"
    assert window.export_selected_only
    assert window.dirty
    window.dirty = False
    window.close()
    qtbot.waitUntil(lambda: not window.jobs, timeout=10000)
