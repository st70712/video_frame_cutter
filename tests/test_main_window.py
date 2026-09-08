from PySide6.QtWidgets import QMessageBox

from video_frame_cutter.ui.main_window import MainWindow


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
    assert window.frame.index == 0
    window.add_marker()
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
