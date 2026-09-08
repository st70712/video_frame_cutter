from PIL import Image
from PySide6.QtCore import Qt

from video_frame_cutter.models import CropRect, ExportSettings
from video_frame_cutter.ui.widgets import CropDialog, Timeline
from video_frame_cutter.workers import Job


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
