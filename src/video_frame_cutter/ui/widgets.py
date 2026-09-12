from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..models import AnalysisSettings, CropRect, ExportSettings, timecode
from . import theme


def pixmap(image):
    return QPixmap.fromImage(ImageQt(image.convert("RGB")))


class ImageView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.setMinimumSize(240, 160)

    def set_image(self, image):
        self.image = pixmap(image)
        self.update()

    def image_rect(self):
        if self.image is None:
            return QRectF(self.rect())
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF(
            (self.width() - size.width()) / 2,
            (self.height() - size.height()) / 2,
            size.width(),
            size.height(),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202222"))
        if self.image is not None:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.image_rect(), self.image, QRectF(self.image.rect()))


class Timeline(QWidget):
    seek = Signal(float)
    selected = Signal(str)
    moved = Signal(str, float)
    viewportChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(155)
        self.setMaximumHeight(200)
        self.duration = 1.0
        self.position = 0.0
        self.zoom = 1.0
        self.start = 0.0
        self.markers = []
        self.curve = []
        self.thumbnails = []
        self.selected_ids = set()
        self.drag_uid = None
        self.drag_seconds = None
        self.drag_origin = None
        self.setMouseTracking(True)
        self.setToolTip("時間軸：拖曳標記；Ctrl + 滾輪縮放；滾輪平移")

    @property
    def span(self):
        return self.duration / self.zoom

    def screen_x(self, seconds):
        return 12 + (seconds - self.start) / self.span * max(1, self.width() - 24)

    def seconds_at(self, position):
        return max(
            0.0,
            min(
                self.duration, self.start + (position - 12) / max(1, self.width() - 24) * self.span
            ),
        )

    def set_zoom(self, zoom):
        anchor = (
            self.position
            if self.start <= self.position <= self.start + self.span
            else (self.start + self.span / 2)
        )
        self.zoom = max(1.0, min(100.0, zoom))
        self.start = max(0.0, min(self.duration - self.span, anchor - self.span / 2))
        self.viewportChanged.emit()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = theme.current()
        painter.fillRect(self.rect(), QColor(colors.timeline_bg))
        painter.setPen(QColor(colors.timeline_fg))
        for index in range(6):
            seconds = self.start + self.span * index / 5
            position = self.screen_x(seconds)
            painter.drawLine(QPointF(position, 25), QPointF(position, self.height() - 12))
            painter.drawText(
                QRectF(min(position, self.width() - 104), 2, 104, 20),
                Qt.AlignmentFlag.AlignLeft,
                timecode(seconds),
            )
        for seconds, thumbnail in self.thumbnails:
            position = self.screen_x(seconds)
            if -100 < position < self.width():
                painter.drawPixmap(
                    QRectF(position, 32, 92, 52), thumbnail, QRectF(thumbnail.rect())
                )
        columns = {}
        for seconds, score in self.curve:
            position = int(self.screen_x(seconds))
            if 0 <= position < self.width():
                columns[position] = max(columns.get(position, 0), min(1, score))
        painter.setPen(QPen(QColor(colors.curve), 1))
        for position, score in columns.items():
            painter.drawLine(QPointF(position, 130), QPointF(position, 130 - score * 40))
        for marker in self.markers:
            seconds = (
                self.drag_seconds
                if marker.uid == self.drag_uid and self.drag_seconds is not None
                else marker.frame.seconds
            )
            position = self.screen_x(seconds)
            if 0 <= position < self.width():
                color = QColor(colors.marker_review if marker.review else colors.marker)
                painter.setPen(QPen(color, 3 if marker.uid in self.selected_ids else 1))
                painter.drawLine(QPointF(position, 28), QPointF(position, 142))
                painter.setBrush(color if marker.included else QColor(colors.timeline_bg))
                painter.drawEllipse(QPointF(position, 94), 6, 6)
        position = self.screen_x(self.position)
        painter.setPen(QPen(QColor(colors.playhead), 2))
        painter.drawLine(QPointF(position, 22), QPointF(position, self.height()))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        position = event.position().x()
        nearby = sorted(
            self.markers, key=lambda marker: abs(self.screen_x(marker.frame.seconds) - position)
        )
        if nearby and abs(self.screen_x(nearby[0].frame.seconds) - position) < 9:
            self.drag_uid = nearby[0].uid
            self.drag_origin = position
            self.drag_seconds = nearby[0].frame.seconds
            self.selected.emit(self.drag_uid)
        else:
            self.seek.emit(self.seconds_at(position))

    def mouseMoveEvent(self, event):
        if self.drag_uid is not None:
            self.drag_seconds = self.seconds_at(event.position().x())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.drag_uid is not None and abs(event.position().x() - self.drag_origin) > 3:
            self.moved.emit(self.drag_uid, self.seconds_at(event.position().x()))
        self.drag_uid = self.drag_seconds = self.drag_origin = None
        self.update()

    def wheelEvent(self, event):
        direction = 1 if event.angleDelta().y() > 0 else -1
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.zoom * (1.4 if direction > 0 else 1 / 1.4))
        else:
            self.start = max(
                0, min(self.duration - self.span, self.start - direction * self.span * 0.1)
            )
            self.viewportChanged.emit()
            self.update()
        event.accept()


class CropCanvas(ImageView):
    changed = Signal(object)

    def __init__(self, image, crop, parent=None):
        super().__init__(parent)
        self.set_image(image)
        self.crop = crop
        self.mode = None
        self.origin = None
        self.original = None
        self.setMinimumSize(320, 200)

    def crop_rect(self):
        image = self.image_rect()
        return QRectF(
            image.left() + self.crop.left * image.width(),
            image.top() + self.crop.top * image.height(),
            (self.crop.right - self.crop.left) * image.width(),
            (self.crop.bottom - self.crop.top) * image.height(),
        )

    def handles(self):
        rect = self.crop_rect()
        return {
            "lt": rect.topLeft(),
            "rt": rect.topRight(),
            "lb": rect.bottomLeft(),
            "rb": rect.bottomRight(),
            "l": QPointF(rect.left(), rect.center().y()),
            "r": QPointF(rect.right(), rect.center().y()),
            "t": QPointF(rect.center().x(), rect.top()),
            "b": QPointF(rect.center().x(), rect.bottom()),
        }

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        image, crop = self.image_rect(), self.crop_rect()
        shade = QColor(0, 0, 0, 120)
        painter.fillRect(
            QRectF(image.left(), image.top(), image.width(), crop.top() - image.top()), shade
        )
        painter.fillRect(
            QRectF(image.left(), crop.bottom(), image.width(), image.bottom() - crop.bottom()),
            shade,
        )
        painter.fillRect(
            QRectF(image.left(), crop.top(), crop.left() - image.left(), crop.height()), shade
        )
        painter.fillRect(
            QRectF(crop.right(), crop.top(), image.right() - crop.right(), crop.height()), shade
        )
        painter.setPen(QPen(QColor("#50e2c6"), 2))
        painter.drawRect(crop)
        painter.setBrush(QColor("#ffffff"))
        for position in self.handles().values():
            painter.drawRect(QRectF(position.x() - 4, position.y() - 4, 8, 8))

    def normalized(self, position):
        rect = self.image_rect()
        return QPointF(
            max(0, min(1, (position.x() - rect.left()) / rect.width())),
            max(0, min(1, (position.y() - rect.top()) / rect.height())),
        )

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.mode = None
        for name, point in self.handles().items():
            if (point - event.position()).manhattanLength() < 14:
                self.mode = name
                break
        if self.mode is None:
            if not self.image_rect().contains(event.position()):
                return
            self.mode = "move" if self.crop_rect().contains(event.position()) else "new"
        self.origin = self.normalized(event.position())
        self.original = self.crop

    def mouseMoveEvent(self, event):
        if self.mode is None:
            return
        point = self.normalized(event.position())
        left, top, right, bottom = (
            self.original.left,
            self.original.top,
            self.original.right,
            self.original.bottom,
        )
        minimum_x = 1 / self.image.width()
        minimum_y = 1 / self.image.height()
        if self.mode == "move":
            delta_x = max(-left, min(1 - right, point.x() - self.origin.x()))
            delta_y = max(-top, min(1 - bottom, point.y() - self.origin.y()))
            left, right, top, bottom = (
                left + delta_x,
                right + delta_x,
                top + delta_y,
                bottom + delta_y,
            )
        elif self.mode == "new":
            left, right = sorted((self.origin.x(), point.x()))
            top, bottom = sorted((self.origin.y(), point.y()))
        else:
            if "l" in self.mode:
                left = min(right - minimum_x, point.x())
            if "r" in self.mode:
                right = max(left + minimum_x, point.x())
            if "t" in self.mode:
                top = min(bottom - minimum_y, point.y())
            if "b" in self.mode:
                bottom = max(top + minimum_y, point.y())
        if right - left >= minimum_x and bottom - top >= minimum_y:
            self.crop = CropRect(left, top, right, bottom)
            self.changed.emit(self.crop)
            self.update()

    def mouseReleaseEvent(self, event):
        self.mode = None


class AnalysisSettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("分析畫面變化")
        self.threshold = self._decimal(0.001, 1, settings.threshold, 0.01)
        self.area = self._decimal(0, 100, settings.area * 100, 0.1)
        self.stable = self._decimal(0.05, 10, settings.stable_seconds, 0.05)
        self.duplicate_window = self._decimal(
            0, 60, settings.duplicate_window_seconds, 0.1
        )
        self.duplicate_window.setSpecialValueText("關閉")
        self.interval = self._decimal(0, 60, settings.min_interval, 0.1)
        self.width = QComboBox()
        widths = [160, 320, 480, 640, 960]
        if settings.width not in widths:
            widths.append(settings.width)
            widths.sort()
        for width in widths:
            self.width.addItem(str(width), width)
        self.width.setCurrentIndex(self.width.findData(settings.width))
        form = QFormLayout()
        form.addRow("變化門檻", self.threshold)
        form.addRow("最小變化面積 (%)", self.area)
        form.addRow("穩定時間 (秒)", self.stable)
        form.addRow("重複畫面忽略時間 (秒)", self.duplicate_window)
        form.addRow("最小間隔 (秒)", self.interval)
        form.addRow("分析寬度 (px)", self.width)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("開始分析")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _decimal(self, minimum, maximum, value, step):
        control = QDoubleSpinBox()
        control.setDecimals(3)
        control.setRange(minimum, maximum)
        control.setSingleStep(step)
        control.setValue(value)
        return control

    def settings(self):
        return AnalysisSettings(
            threshold=self.threshold.value(),
            area=self.area.value() / 100,
            stable_seconds=self.stable.value(),
            min_interval=self.interval.value(),
            width=self.width.currentData(),
            duplicate_window_seconds=self.duplicate_window.value(),
        )


class ExportSettingsDialog(QDialog):
    def __init__(self, settings, selected_count, kind="jpg", selected_only=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("匯出截圖")
        self.format = QComboBox()
        self.format.addItem("JPG 圖片", "jpg")
        self.format.addItem("PowerPoint 簡報", "pptx")
        self.format.setCurrentIndex(max(0, self.format.findData(kind)))
        self.output_width = QSpinBox()
        self.output_height = QSpinBox()
        for control, value in (
            (self.output_width, settings.width),
            (self.output_height, settings.height),
        ):
            control.setRange(16, 8192)
            control.setValue(value)
        dimensions = QHBoxLayout()
        dimensions.addWidget(self.output_width)
        dimensions.addWidget(QLabel("×"))
        dimensions.addWidget(self.output_height)
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.quality.setValue(settings.quality)
        self.timestamp = QCheckBox("投影片顯示時間戳")
        self.timestamp.setChecked(settings.timestamp)
        self.selected_only = QCheckBox(f"僅匯出選取的標記（{selected_count}）")
        self.selected_only.setChecked(selected_only and selected_count > 0)
        self.selected_only.setEnabled(selected_count > 0)
        form = QFormLayout()
        form.addRow("格式", self.format)
        form.addRow("輸出尺寸 (px)", dimensions)
        form.addRow("JPEG 品質", self.quality)
        form.addRow("", self.timestamp)
        form.addRow("", self.selected_only)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("選擇輸出位置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.format.currentIndexChanged.connect(self._update_format_controls)
        self._update_format_controls()

    def _update_format_controls(self):
        self.timestamp.setEnabled(self.kind() == "pptx")

    def kind(self):
        return self.format.currentData()

    def settings(self):
        return ExportSettings(
            self.output_width.value(),
            self.output_height.value(),
            self.quality.value(),
            self.timestamp.isChecked(),
        )


class CropDialog(QDialog):
    def __init__(self, image, crop, output, selected_count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("編輯截圖範圍")
        self.resize(1000, 640)
        self.source = image
        self.output = output
        self.canvas = CropCanvas(image, crop)
        self.preview = QLabel()
        self.preview.setFixedSize(240, 150)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fields = []
        form = QFormLayout()
        for label in ("左 (%)", "上 (%)", "右 (%)", "下 (%)"):
            control = QDoubleSpinBox()
            control.setRange(0, 100)
            control.setDecimals(2)
            control.valueChanged.connect(self.from_fields)
            form.addRow(label, control)
            self.fields.append(control)
        reset = QPushButton("重設全圖")
        reset.clicked.connect(lambda: self.set_crop(CropRect()))
        self.batch = QCheckBox(f"套用至選取的 {selected_count} 個標記")
        self.batch.setEnabled(selected_count > 1)
        side = QVBoxLayout()
        side.addLayout(form)
        side.addWidget(reset)
        side.addWidget(self.preview)
        side.addWidget(self.batch)
        side.addStretch()
        content = QHBoxLayout()
        content.addWidget(self.canvas, 1)
        content.addLayout(side)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("套用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(content, 1)
        layout.addWidget(buttons)
        self.canvas.changed.connect(self.set_crop)
        self.set_crop(crop)

    def set_crop(self, crop):
        self.canvas.crop = crop
        self.canvas.update()
        for field, value in zip(self.fields, (crop.left, crop.top, crop.right, crop.bottom)):
            field.blockSignals(True)
            field.setValue(value * 100)
            field.blockSignals(False)
        image = self.source.crop(crop.pixels(self.source.size))
        size = (self.output.width, self.output.height)
        scale = min(240 / size[0], 150 / size[1])
        image = image.resize(
            (max(1, round(size[0] * scale)), max(1, round(size[1] * scale))),
            Image.Resampling.LANCZOS,
        )
        self.preview.setPixmap(pixmap(image))

    def from_fields(self):
        try:
            crop = CropRect(*(field.value() / 100 for field in self.fields))
        except ValueError:
            return
        self.set_crop(crop)
