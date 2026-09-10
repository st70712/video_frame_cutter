import logging
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QIcon, QKeySequence, QUndoCommand, QUndoStack
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..analysis import analyze, merge_markers
from ..export import export_markers
from ..models import AnalysisSettings, ExportSettings, Marker, timecode
from ..project import fingerprint, read_project, save_project, validate_source
from ..video import FrameReader, check_cancel, index_video
from ..workers import Job
from .widgets import (
    AnalysisSettingsDialog,
    CropDialog,
    ExportSettingsDialog,
    ImageView,
    Timeline,
    pixmap,
)


class EditMarkers(QUndoCommand):
    def __init__(self, window, after, title):
        super().__init__(title)
        self.window = window
        self.before = deepcopy(window.markers)
        self.after = deepcopy(after)

    def redo(self):
        self.window.set_markers(deepcopy(self.after))

    def undo(self):
        self.window.set_markers(deepcopy(self.before))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Frame Cutter")
        self.resize(1280, 860)
        self.setMinimumSize(880, 650)
        self.info = None
        self.identity = None
        self.markers = []
        self.frame = None
        self.frame_image = None
        self.project_path = None
        self.dirty = False
        self.loading = False
        self.generation = 0
        self.preview_id = 0
        self.thumb_id = 0
        self.jobs = set()
        self.busy_job = None
        self.preview_job = None
        self.thumb_job = None
        self.thumb_cache = {}
        self.closing = False
        self.refreshing = False
        self._analysis_settings = AnalysisSettings()
        self._export_settings = ExportSettings()
        self.export_kind = "jpg"
        self.export_selected_only = False
        self.undo_stack = QUndoStack(self)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self.player.setAudioOutput(self.audio)
        self._build_ui()
        self.player.positionChanged.connect(self.playback_position)
        self.player.playbackStateChanged.connect(self.playback_state)
        self.player.errorOccurred.connect(
            lambda error, text: self.statusBar().showMessage(f"播放失敗：{text}")
        )
        self.player.mediaStatusChanged.connect(self.media_status)
        self._update_controls()

    def _action(self, toolbar, text, icon, callback, shortcut=None):
        action = QAction(self.style().standardIcon(icon), text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        toolbar.addAction(action)
        return action

    def _build_ui(self):
        standard = QStyle.StandardPixmap
        self.toolbar = QToolBar("專案")
        self.toolbar.setMovable(False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(self.toolbar)
        self.open_action = self._action(
            self.toolbar, "開啟影片", standard.SP_DialogOpenButton, self.choose_video, "Ctrl+O"
        )
        self.project_action = self._action(
            self.toolbar,
            "開啟專案",
            standard.SP_DirOpenIcon,
            self.choose_project,
            "Ctrl+Shift+O",
        )
        self.save_action = self._action(
            self.toolbar, "儲存專案", standard.SP_DialogSaveButton, self.save, "Ctrl+S"
        )
        self.toolbar.addSeparator()
        undo = self.undo_stack.createUndoAction(self, "復原")
        undo.setIcon(self.style().standardIcon(standard.SP_ArrowBack))
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.toolbar.addAction(undo)
        redo = self.undo_stack.createRedoAction(self, "重做")
        redo.setIcon(self.style().standardIcon(standard.SP_ArrowForward))
        redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.toolbar.addAction(redo)
        self.toolbar.addSeparator()
        self.analyze_action = self._action(
            self.toolbar,
            "分析畫面變化",
            standard.SP_BrowserReload,
            self.start_analysis,
        )
        self.export_action = self._action(
            self.toolbar,
            "匯出",
            standard.SP_DialogSaveButton,
            self.choose_export,
        )

        self.video_widget = QVideoWidget()
        self.player.setVideoOutput(self.video_widget)
        self.image_view = ImageView()
        self.placeholder = QLabel("開啟影片")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("background: #202222; color: #d6dedd; font-size: 24px;")
        self.preview = QStackedWidget()
        self.preview.addWidget(self.video_widget)
        self.preview.addWidget(self.image_view)
        self.preview.addWidget(self.placeholder)
        self.preview.setCurrentIndex(2)
        self.preview.setMinimumSize(300, 200)
        self.play_button = QPushButton()
        self.play_button.setFixedSize(40, 32)
        self.play_button.setIcon(self.style().standardIcon(standard.SP_MediaPlay))
        self.play_button.setToolTip("播放／暫停 (Space)")
        self.play_button.clicked.connect(self.toggle_play)
        previous = QPushButton()
        previous.setIcon(self.style().standardIcon(standard.SP_MediaSkipBackward))
        previous.setToolTip("上一幀 (Left)")
        previous.clicked.connect(lambda: self.step(-1))
        following = QPushButton()
        following.setIcon(self.style().standardIcon(standard.SP_MediaSkipForward))
        following.setToolTip("下一幀 (Right)")
        following.clicked.connect(lambda: self.step(1))
        self.clock = QLabel("00:00:00.000 / 00:00:00.000")
        self.clock.setMinimumWidth(210)
        self.mute = QPushButton()
        self.mute.setCheckable(True)
        self.mute.setIcon(self.style().standardIcon(standard.SP_MediaVolume))
        self.mute.setToolTip("靜音")
        self.mute.toggled.connect(self.set_muted)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setMaximumWidth(100)
        self.volume.setToolTip("音量")
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        playback = QHBoxLayout()
        for widget in (self.play_button, previous, following, self.clock):
            playback.addWidget(widget)
        playback.addStretch()
        playback.addWidget(self.mute)
        playback.addWidget(self.volume)
        self.filename = QLabel("未載入影片")
        self.filename.setWordWrap(True)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.filename)
        left_layout.addWidget(self.preview, 1)
        left_layout.addLayout(playback)

        self.marker_list = QListWidget()
        self.marker_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.marker_list.setIconSize(QSize(96, 54))
        self.marker_list.setMinimumHeight(8 * 62 + 2 * self.marker_list.frameWidth() + 2)
        self.marker_list.itemSelectionChanged.connect(self.selection_changed)
        self.marker_list.itemChanged.connect(self.inclusion_changed)
        self.marker_list.itemDoubleClicked.connect(lambda item: self.edit_crop())
        self.marker_heading = QLabel("標記 0")
        marker_tools = QToolBar()
        marker_tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.add_action = self._action(
            marker_tools, "新增標記 (M)", standard.SP_FileDialogNewFolder, self.add_marker, "M"
        )
        self.delete_action = self._action(
            marker_tools,
            "刪除選取標記 (Delete)",
            standard.SP_TrashIcon,
            self.delete_markers,
            "Delete",
        )
        self.crop_action = self._action(
            marker_tools,
            "編輯裁切 (Enter)",
            standard.SP_FileDialogDetailedView,
            self.edit_crop,
            "Return",
        )
        self._action(
            marker_tools,
            "上一個標記",
            standard.SP_ArrowBack,
            lambda: self.adjacent_marker(-1),
            "Ctrl+Left",
        )
        self._action(
            marker_tools,
            "下一個標記",
            standard.SP_ArrowForward,
            lambda: self.adjacent_marker(1),
            "Ctrl+Right",
        )
        self.position_input = QDoubleSpinBox()
        self.position_input.setRange(0, 999999)
        self.position_input.setDecimals(3)
        self.position_input.setSuffix(" 秒")
        self.position_input.setSingleStep(0.1)
        self.position_input.editingFinished.connect(lambda: self.seek(self.position_input.value()))
        move_button = QPushButton("移動選取標記至此")
        move_button.clicked.connect(self.move_selected)
        position_layout = QHBoxLayout()
        position_layout.addWidget(self.position_input)
        position_layout.addWidget(move_button)

        self.marker_panel = QWidget()
        marker_layout = QVBoxLayout(self.marker_panel)
        marker_layout.setContentsMargins(0, 0, 0, 0)
        marker_layout.setSpacing(4)
        marker_layout.addWidget(self.marker_heading)
        marker_layout.addWidget(marker_tools)
        marker_layout.addWidget(self.marker_list, 1)
        marker_layout.addLayout(position_layout)
        right = QWidget()
        right.setMinimumWidth(330)
        right.setMaximumWidth(440)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.marker_panel)
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([860, 380])

        self.timeline = Timeline()
        self.timeline.seek.connect(self.seek)
        self.timeline.selected.connect(self.select_uid)
        self.timeline.moved.connect(self.move_marker)
        self.timeline.viewportChanged.connect(self.sync_pan)
        self.pan = QSlider(Qt.Orientation.Horizontal)
        self.pan.setRange(0, 1000)
        self.pan.valueChanged.connect(self.pan_timeline)
        self.pan.setToolTip("時間軸平移")
        zoom_out = QPushButton()
        zoom_out.setIcon(self.style().standardIcon(standard.SP_ArrowLeft))
        zoom_out.setToolTip("縮小時間軸")
        zoom_out.clicked.connect(lambda: self.timeline.set_zoom(self.timeline.zoom / 1.5))
        zoom_in = QPushButton()
        zoom_in.setIcon(self.style().standardIcon(standard.SP_ArrowRight))
        zoom_in.setToolTip("放大時間軸")
        zoom_in.clicked.connect(lambda: self.timeline.set_zoom(self.timeline.zoom * 1.5))
        fit = QPushButton("全片")
        fit.clicked.connect(lambda: self.timeline.set_zoom(1))
        timeline_tools = QHBoxLayout()
        timeline_tools.addWidget(zoom_out)
        timeline_tools.addWidget(self.pan, 1)
        timeline_tools.addWidget(zoom_in)
        timeline_tools.addWidget(fit)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(170)
        self.progress.hide()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel_work)
        self.cancel_button.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_button)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.timeline)
        layout.addLayout(timeline_tools)
        self.setCentralWidget(central)
        for key, callback in (
            ("Space", self.toggle_play),
            ("Left", lambda: self.step(-1)),
            ("Right", lambda: self.step(1)),
        ):
            action = QAction(self)
            action.setShortcut(key)
            action.triggered.connect(callback)
            self.addAction(action)

    def analysis_settings(self):
        return deepcopy(self._analysis_settings)

    def export_settings(self):
        return deepcopy(self._export_settings)

    def _update_controls(self):
        loaded = self.info is not None and not self.loading
        available = loaded and self.busy_job is None
        self.open_action.setEnabled(self.busy_job is None)
        self.project_action.setEnabled(self.busy_job is None)
        self.save_action.setEnabled(available)
        self.analyze_action.setEnabled(available)
        self.export_action.setEnabled(
            available and any(marker.included for marker in self.markers)
        )
        self.play_button.setEnabled(loaded)
        self.add_action.setEnabled(loaded)
        selected = bool(self.selected_ids())
        self.delete_action.setEnabled(loaded and selected)
        self.crop_action.setEnabled(loaded and selected)
        self.mute.setEnabled(loaded and self.info.audio)
        self.volume.setEnabled(loaded and self.info.audio)

    def error(self, message):
        if not self.closing:
            QMessageBox.warning(self, "無法完成操作", message)

    def launch_job(self, function, callback, title=None):
        job = Job(function, self)
        generation = self.generation
        self.jobs.add(job)
        if title:
            self.busy_job = job
            self.progress.setValue(0)
            self.progress.show()
            self.cancel_button.show()
            self.statusBar().showMessage(title)
            job.progress.connect(self.progress.setValue)

        def deliver(value):
            if generation == self.generation and not self.closing and not job.cancel.is_set():
                try:
                    callback(value)
                except Exception as error:
                    logging.getLogger(__name__).exception("Result delivery failed")
                    self.error(f"{type(error).__name__}: {error}")

        def failure(message):
            if generation == self.generation and not job.cancel.is_set():
                self.error(message)

        def finished():
            self.jobs.discard(job)
            if self.busy_job is job:
                self.busy_job = None
                self.loading = False
                self.progress.hide()
                self.cancel_button.hide()
                if job.cancel.is_set():
                    self.statusBar().showMessage("已取消")
            if self.preview_job is job:
                self.preview_job = None
            if self.thumb_job is job:
                self.thumb_job = None
            job.deleteLater()
            self._update_controls()
            if self.closing and not self.jobs:
                QTimer.singleShot(0, self.close)

        job.result.connect(deliver)
        job.failed.connect(failure)
        job.finished.connect(finished)
        self._update_controls()
        job.start()
        return job

    def cancel_work(self):
        if self.busy_job:
            self.busy_job.cancel.set()
            self.statusBar().showMessage("正在取消…")

    def confirm_discard(self):
        if not self.dirty:
            return True
        return (
            QMessageBox.question(
                self,
                "尚未儲存",
                "捨棄尚未儲存的專案變更？",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            == QMessageBox.StandardButton.Discard
        )

    def choose_video(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "開啟影片", "", "影片 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v);;所有檔案 (*)"
        )
        if path:
            self.load_video(path)

    def load_video(self, path, saved=None, project_path=None):
        if self.busy_job is not None:
            return
        self.player.stop()
        self.loading = True
        self.generation += 1
        for job in self.jobs:
            job.cancel.set()

        def load(cancel, progress):
            info = index_video(path, cancel, progress)
            check_cancel(cancel)
            identity = fingerprint(path)
            check_cancel(cancel)
            if saved:
                validate_source(saved[0], info, saved[1], identity)
            return info, identity

        def loaded(result):
            self.loading = False
            self.info, self.identity = result
            self.project_path = Path(project_path) if project_path else None
            self.markers = deepcopy(saved[1]) if saved else []
            self.undo_stack.clear()
            self.thumb_cache.clear()
            self.timeline.duration = self.info.duration
            self.timeline.position = 0
            self.timeline.start = 0
            self.timeline.zoom = 1
            self.timeline.curve = []
            self.timeline.thumbnails = [
                (seconds, pixmap(image)) for seconds, image in self.info.thumbnails
            ]
            self.position_input.setMaximum(self.info.duration)
            self.filename.setText(
                f"{self.info.path.name}  ·  {self.info.width} × {self.info.height}"
                f"  ·  {len(self.info.frames):,} 幀"
                + ("  ·  含音訊" if self.info.audio else "  ·  無音訊")
            )
            self.filename.setToolTip(str(self.info.path))
            self.player.setSource(QUrl.fromLocalFile(str(self.info.path)))
            if saved:
                self.restore_settings(saved[2], saved[3])
            self.refresh_markers()
            self.seek(self.info.frames[0].seconds)
            self.sync_pan()
            self.dirty = False
            self.statusBar().showMessage("影片已載入")

        self.launch_job(load, loaded, "建立影格索引…")

    def restore_settings(self, analysis, export):
        self._analysis_settings = deepcopy(analysis)
        self._export_settings = deepcopy(export)

    def choose_project(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "開啟專案", "", "擷取專案 (*.vfc.json *.json)")
        if not path:
            return
        try:
            saved = read_project(path)
            video = (Path(path).parent / saved[0]["video"]).resolve()
            if not video.exists():
                replacement, _ = QFileDialog.getOpenFileName(self, "重新指定原始影片")
                if not replacement:
                    return
                video = Path(replacement)
            self.load_video(video, saved, path)
        except Exception as error:
            logging.getLogger(__name__).exception("Project load failed")
            self.error(str(error))

    def save(self):
        if self.info is None or self.busy_job:
            return
        destination = self.project_path
        if destination is None:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "儲存專案",
                str(self.info.path.with_suffix(".vfc.json")),
                "擷取專案 (*.vfc.json)",
            )
            if not path:
                return
            destination = Path(path)
        try:
            save_project(
                destination,
                self.info,
                self.markers,
                self.analysis_settings(),
                self.export_settings(),
                self.identity,
            )
            self.project_path = destination
            self.dirty = False
            self.statusBar().showMessage(f"已儲存：{destination}")
        except Exception as error:
            logging.getLogger(__name__).exception("Project save failed")
            self.error(str(error))

    def media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.info:
            self.seek(self.info.frames[-1].seconds)

    def set_muted(self, muted):
        self.audio.setMuted(muted)
        icon = (
            QStyle.StandardPixmap.SP_MediaVolumeMuted
            if muted
            else QStyle.StandardPixmap.SP_MediaVolume
        )
        self.mute.setIcon(self.style().standardIcon(icon))

    def playback_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        icon = (
            QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        )
        self.play_button.setIcon(self.style().standardIcon(icon))

    def playback_position(self, milliseconds):
        if self.info and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            seconds = milliseconds / 1000
            self.frame = self.info.at(seconds)
            self.frame_image = None
            self.update_position(seconds)

    def update_position(self, seconds):
        self.timeline.position = seconds
        self.timeline.update()
        self.clock.setText(f"{timecode(seconds)} / {timecode(self.info.duration)}")
        self.position_input.blockSignals(True)
        self.position_input.setValue(max(0, seconds))
        self.position_input.blockSignals(False)

    def toggle_play(self):
        if not self.info or self.loading:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.seek(self.player.position() / 1000)
        else:
            self.preview_id += 1
            if self.preview_job:
                self.preview_job.cancel.set()
            self.preview.setCurrentIndex(0)
            self.player.setPosition(round(max(0, self.frame.seconds) * 1000))
            self.player.play()

    def seek(self, seconds, callback=None):
        if not self.info or self.loading:
            return
        self.player.pause()
        reference = self.info.at(seconds)
        self.frame = reference
        self.frame_image = None
        self.update_position(reference.seconds)
        self.preview.setCurrentIndex(1)
        self.image_view.image = None
        self.image_view.update()
        self.preview_id += 1
        request = self.preview_id
        info = self.info
        if self.preview_job:
            self.preview_job.cancel.set()

        def decode(cancel, progress):
            with FrameReader(info) as reader:
                return reader.get(reference, cancel)

        def ready(image):
            if request != self.preview_id:
                return
            self.frame_image = image
            self.image_view.set_image(image)
            if callback:
                callback(image)

        self.preview_job = self.launch_job(decode, ready)

    def step(self, delta):
        if self.info and self.frame:
            index = max(0, min(len(self.info.frames) - 1, self.frame.index + delta))
            self.seek(self.info.frames[index].seconds)

    def selected_ids(self):
        return {item.data(Qt.ItemDataRole.UserRole) for item in self.marker_list.selectedItems()}

    def current_marker(self):
        item = self.marker_list.currentItem()
        if item is None:
            return None
        uid = item.data(Qt.ItemDataRole.UserRole)
        return next((marker for marker in self.markers if marker.uid == uid), None)

    def set_markers(self, markers):
        self.markers = sorted(markers, key=lambda marker: marker.frame.seconds)
        self.dirty = True
        self.refresh_markers()

    def commit(self, markers, title):
        self.undo_stack.push(EditMarkers(self, markers, title))

    def refresh_markers(self):
        selected = self.selected_ids()
        current = self.current_marker()
        self.refreshing = True
        self.marker_list.clear()
        for index, marker in enumerate(self.markers):
            origin = "手動" if marker.source == "manual" else "自動"
            state = "待檢查" if marker.review else origin
            item = QListWidgetItem(f"{index + 1:03}  {timecode(marker.frame.seconds)}\n{state}")
            item.setData(Qt.ItemDataRole.UserRole, marker.uid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if marker.included else Qt.CheckState.Unchecked
            )
            item.setToolTip(
                f"變化時間：{timecode(marker.change_seconds)}\n差異分數：{marker.score:.4f}"
            )
            item.setSizeHint(QSize(250, 62))
            key = (marker.frame.pts, marker.crop)
            if key in self.thumb_cache:
                item.setIcon(self.thumb_cache[key])
            self.marker_list.addItem(item)
            if marker.uid in selected:
                item.setSelected(True)
            if current and marker.uid == current.uid:
                self.marker_list.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.refreshing = False
        self.marker_heading.setText(
            f"標記 {len(self.markers)}  ·  匯出 {sum(marker.included for marker in self.markers)}"
        )
        self.timeline.markers = self.markers
        self.timeline.selected_ids = self.selected_ids()
        self.timeline.update()
        self._update_controls()
        self.request_thumbnails()

    def request_thumbnails(self):
        if self.info is None:
            return
        self.thumb_id += 1
        request = self.thumb_id
        if self.thumb_job:
            self.thumb_job.cancel.set()
        active_keys = {(marker.frame.pts, marker.crop) for marker in self.markers}
        self.thumb_cache = {
            key: value for key, value in self.thumb_cache.items() if key in active_keys
        }
        pending = deepcopy(
            [
                marker
                for marker in self.markers
                if (marker.frame.pts, marker.crop) not in self.thumb_cache
            ]
        )
        if not pending:
            return
        info = self.info

        def decode(cancel, progress):
            images = []
            with FrameReader(info, cache_size=1) as reader:
                for marker in pending:
                    image = reader.get(marker.frame, cancel)
                    image = image.crop(marker.crop.pixels(image.size))
                    image.thumbnail((96, 54))
                    images.append(((marker.frame.pts, marker.crop), image))
            return images

        def ready(images):
            if request != self.thumb_id:
                return
            for key, image in images:
                self.thumb_cache[key] = QIcon(pixmap(image))
            self.refreshing = True
            for index, marker in enumerate(self.markers):
                key = (marker.frame.pts, marker.crop)
                if key in self.thumb_cache:
                    self.marker_list.item(index).setIcon(self.thumb_cache[key])
            self.refreshing = False

        self.thumb_job = self.launch_job(decode, ready)

    def selection_changed(self):
        if self.refreshing:
            return
        self.timeline.selected_ids = self.selected_ids()
        self.timeline.update()
        marker = self.current_marker()
        if marker and marker.uid in self.selected_ids():
            self.seek(marker.frame.seconds)
        self._update_controls()

    def select_uid(self, uid):
        for index in range(self.marker_list.count()):
            item = self.marker_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == uid:
                self.marker_list.setCurrentItem(
                    item, QItemSelectionModel.SelectionFlag.ClearAndSelect
                )
                return

    def inclusion_changed(self, item):
        if self.refreshing:
            return
        uid = item.data(Qt.ItemDataRole.UserRole)
        markers = deepcopy(self.markers)
        for marker in markers:
            if marker.uid == uid:
                marker.included = item.checkState() == Qt.CheckState.Checked
                marker.modified = True
        self.commit(markers, "變更匯出選取")

    def add_marker(self):
        if not self.info or self.loading:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.seek(self.player.position() / 1000)
        if any(marker.frame.pts == self.frame.pts for marker in self.markers):
            self.statusBar().showMessage("這個影格已經有標記")
            return
        marker = Marker(self.frame)
        self.commit([*self.markers, marker], "新增標記")
        self.select_uid(marker.uid)

    def delete_markers(self):
        selected = self.selected_ids()
        if selected:
            self.commit(
                [marker for marker in self.markers if marker.uid not in selected], "刪除標記"
            )

    def move_marker(self, uid, seconds):
        if not self.info or self.loading:
            return
        frame = self.info.at(seconds)
        if any(marker.uid != uid and marker.frame.pts == frame.pts for marker in self.markers):
            self.statusBar().showMessage("目標影格已經有標記")
            return
        markers = deepcopy(self.markers)
        for marker in markers:
            if marker.uid == uid:
                marker.frame = frame
                marker.modified = True
                marker.review = False
        self.commit(markers, "移動標記")
        self.select_uid(uid)
        self.seek(frame.seconds)

    def move_selected(self):
        marker = self.current_marker()
        if marker and len(self.selected_ids()) == 1:
            self.move_marker(marker.uid, self.position_input.value())

    def adjacent_marker(self, direction):
        if not self.frame:
            return
        candidates = [
            marker
            for marker in self.markers
            if (marker.frame.seconds - self.frame.seconds) * direction > 0
        ]
        if candidates:
            self.select_uid(candidates[0 if direction > 0 else -1].uid)

    def edit_crop(self):
        marker = self.current_marker()
        if marker is None:
            return
        selected = self.selected_ids()
        uid = marker.uid

        def edit(image):
            dialog = CropDialog(image, marker.crop, self.export_settings(), len(selected), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            targets = selected if dialog.batch.isChecked() else {uid}
            markers = deepcopy(self.markers)
            for edited in markers:
                if edited.uid in targets:
                    edited.crop = dialog.canvas.crop
                    edited.modified = True
            self.commit(markers, "編輯裁切範圍")

        self.seek(marker.frame.seconds, edit)

    def start_analysis(self):
        if not self.info or self.busy_job:
            return
        dialog = AnalysisSettingsDialog(self.analysis_settings(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.settings()
        if settings != self._analysis_settings:
            self._analysis_settings = settings
            self.dirty = True
        info = self.info

        def completed(result):
            answer = QMessageBox.question(
                self,
                "分析完成",
                f"找到 {len(result.markers)} 個候選標記。\n套用結果？手動與已修改的標記會保留。",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.timeline.curve = result.curve
            self.commit(merge_markers(self.markers, result.markers), "套用分析結果")
            self.statusBar().showMessage("已套用分析結果")

        self.launch_job(
            lambda cancel, progress: analyze(info, settings, cancel, progress),
            completed,
            "逐幀分析中…",
        )

    def choose_export(self):
        if not self.info or self.busy_job:
            return
        selected = self.selected_ids()
        dialog = ExportSettingsDialog(
            self.export_settings(),
            len(selected),
            self.export_kind,
            self.export_selected_only,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        settings = dialog.settings()
        if settings != self._export_settings:
            self._export_settings = settings
            self.dirty = True
        kind = dialog.kind()
        selected_only = dialog.selected_only.isChecked()
        self.export_kind = kind
        self.export_selected_only = selected_only
        markers = deepcopy(
            [
                marker
                for marker in self.markers
                if marker.included
                and (not selected_only or marker.uid in selected)
            ]
        )
        if not markers:
            self.error("沒有可匯出的標記。")
            return
        overwrite = False
        if kind == "jpg":
            destination = QFileDialog.getExistingDirectory(
                self, "選擇輸出位置（建立新的截圖子資料夾）"
            )
        else:
            destination, _ = QFileDialog.getSaveFileName(
                self, "匯出簡報", "screenshots.pptx", "PowerPoint (*.pptx)"
            )
            overwrite = bool(destination and Path(destination).exists())
        if not destination:
            return
        info = self.info
        self.launch_job(
            lambda cancel, progress: export_markers(
                info, markers, settings, destination, kind, cancel, progress, overwrite
            ),
            lambda path: self.export_finished(path),
            "匯出截圖中…",
        )

    def export_finished(self, path):
        self.statusBar().showMessage(f"匯出完成：{path}")
        QMessageBox.information(self, "匯出完成", str(path))

    def sync_pan(self):
        available = self.timeline.duration - self.timeline.span
        self.pan.blockSignals(True)
        self.pan.setValue(round(self.timeline.start / available * 1000) if available else 0)
        self.pan.setEnabled(available > 0)
        self.pan.blockSignals(False)

    def pan_timeline(self, value):
        self.timeline.start = (self.timeline.duration - self.timeline.span) * value / 1000
        self.timeline.update()

    def closeEvent(self, event):
        if not self.closing and not self.confirm_discard():
            event.ignore()
            return
        self.closing = True
        self.player.stop()
        for job in self.jobs:
            job.cancel.set()
        if self.jobs:
            self.setEnabled(False)
            self.statusBar().showMessage("正在結束背景工作…")
            event.ignore()
        else:
            event.accept()
