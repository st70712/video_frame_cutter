from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

import video_frame_cutter.ui.main_window as main_window_module
from video_frame_cutter.models import ExportSettings
from video_frame_cutter.ui.main_window import MainWindow
from video_frame_cutter.ui.widgets import AnalysisSettingsDialog, ExportSettingsDialog


def test_marker_panel_fills_sidebar_and_actions_share_toolbar(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.marker_list.viewport().height() > 0)

    assert window.toolBarArea(window.toolbar) == Qt.ToolBarArea.TopToolBarArea
    assert [action.text() for action in window.toolbar.actions()] == [
        "開啟影片",
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


def test_parameter_dialog_cancellation_has_no_side_effects(qtbot, monkeypatch):
    monkeypatch.setattr(
        AnalysisSettingsDialog, "exec", lambda self: QDialog.DialogCode.Rejected
    )
    monkeypatch.setattr(ExportSettingsDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    window = MainWindow()
    qtbot.addWidget(window)
    window.info = object()
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
