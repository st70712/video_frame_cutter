import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QObject, QStandardPaths, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..eeclass import (
    FORWARDED_HEADERS,
    EeclassCookie,
    EeclassMediaRequest,
    extract_eeclass_indexes,
    extract_eeclass_mp4_source,
    is_eeclass_mp4_request,
    redacted_url,
    validate_eeclass_page_url,
)


def create_eeclass_profile(parent, profile_path=None):
    profile_root = Path(
        profile_path
        or Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        )
        / "eeclass-profile"
    )
    profile_root.mkdir(parents=True, exist_ok=True)
    profile = QWebEngineProfile("eeclass", parent)
    profile.setPersistentStoragePath(str(profile_root / "storage"))
    profile.setCachePath(str(profile_root / "cache"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    return profile


class EeclassCookieBridge(QObject):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.cookies = {}
        store.cookieAdded.connect(self.add)
        store.cookieRemoved.connect(self.remove)
        store.loadAllCookies()

    @staticmethod
    def _key(cookie):
        return bytes(cookie.name()), cookie.domain(), cookie.path()

    @staticmethod
    def _convert(cookie):
        expires = None
        if not cookie.isSessionCookie():
            expires = cookie.expirationDate().toSecsSinceEpoch()
        return EeclassCookie(
            bytes(cookie.name()).decode("utf-8", errors="surrogateescape"),
            bytes(cookie.value()).decode("utf-8", errors="surrogateescape"),
            cookie.domain(),
            cookie.path() or "/",
            cookie.isSecure(),
            expires,
        )

    def add(self, cookie):
        self.cookies[self._key(cookie)] = self._convert(cookie)

    def remove(self, cookie):
        self.cookies.pop(self._key(cookie), None)

    def snapshot(self):
        return tuple(self.cookies.values())

    def clear(self):
        self.cookies.clear()
        self.store.deleteAllCookies()


class EeclassRequestInterceptor(QWebEngineUrlRequestInterceptor):
    media_found = Signal(str, str, object)

    def interceptRequest(self, info):
        media_url = info.requestUrl().toString()
        page_url = info.firstPartyUrl().toString()
        is_media = (
            info.resourceType()
            == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMedia
        )
        if not is_eeclass_mp4_request(media_url, page_url, is_media):
            return
        allowed = FORWARDED_HEADERS | {"authorization"}
        headers = tuple(
            (
                bytes(name).decode("latin-1"),
                bytes(value).decode("latin-1"),
            )
            for name, value in info.httpHeaders().items()
            if bytes(name).decode("latin-1").lower() in allowed
        )
        self.media_found.emit(media_url, page_url, headers)


class EeclassDownloadDialog(QDialog):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("從 EE-Class 下載影片")
        self.resize(980, 720)
        self.setMinimumSize(760, 560)
        self.media = None
        self.indexes = ()
        self.destination = None
        self._pending_media = None
        self._probe_token = 0
        self._closing = False

        self.profile = profile
        self.cookie_bridge = EeclassCookieBridge(self.profile.cookieStore(), self)
        self.interceptor = EeclassRequestInterceptor(self)
        self.interceptor.media_found.connect(self._media_found)
        self.profile.setUrlRequestInterceptor(self.interceptor)

        self.page = QWebEnginePage(self.profile, self)
        self.browser = QWebEngineView(self)
        self.browser.setPage(self.page)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://school.ouk.edu.tw/media/doc/252890")
        self.url.returnPressed.connect(self.open_page)
        self.open_button = QPushButton("開啟")
        self.open_button.clicked.connect(self.open_page)
        self.clear_button = QPushButton("清除登入資料")
        self.clear_button.clicked.connect(self.clear_login)
        self.status = QLabel("尚未開啟頁面")
        self.status.setWordWrap(True)

        address = QHBoxLayout()
        address.addWidget(self.url, 1)
        address.addWidget(self.open_button)
        address.addWidget(self.clear_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.download_button = buttons.addButton(
            "下載影片", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.choose_destination)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(address)
        layout.addWidget(self.browser, 1)
        layout.addWidget(self.status)
        layout.addWidget(buttons)

        self.browser.loadStarted.connect(self._load_started)
        self.browser.loadFinished.connect(self._load_finished)

    def open_page(self):
        try:
            page_url = validate_eeclass_page_url(self.url.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._probe_token += 1
        self.media = None
        self.indexes = ()
        self.destination = None
        self._pending_media = None
        self.download_button.setEnabled(False)
        self.url.setText(page_url)
        self.browser.load(QUrl(page_url))

    def _load_started(self):
        self._probe_token += 1
        self.media = None
        self.indexes = ()
        self.destination = None
        self._pending_media = None
        self.download_button.setEnabled(False)
        self.status.setText("正在載入頁面…")

    def _load_finished(self, succeeded):
        if self.media is not None or self._closing:
            return
        if not succeeded:
            self.status.setText("頁面載入失敗")
            return
        try:
            page_url = validate_eeclass_page_url(self.page.url().toString())
        except ValueError:
            self.status.setText("請在頁面完成登入後開啟 EE-Class 影片頁面")
            return
        token = self._probe_token
        self.status.setText("頁面已載入，正在尋找影片…")
        self.page.toHtml(
            lambda html, page_url=page_url, token=token: self._html_ready(
                html, page_url, token
            )
        )

    def _html_ready(self, html, page_url, token):
        if self._closing or token != self._probe_token or self.media is not None:
            return
        try:
            current_page_url = validate_eeclass_page_url(self.page.url().toString())
        except ValueError:
            return
        if current_page_url != page_url:
            return
        indexes = extract_eeclass_indexes(html)
        if self._pending_media is not None:
            pending = self._pending_media
            self._pending_media = None
            if self._accept_media(*pending, indexes):
                self.browser.stop()
            return
        media_url = extract_eeclass_mp4_source(html, page_url)
        if media_url is None:
            self.status.setText("頁面已載入，但找不到可下載的直接 MP4 影片")
            return
        self._accept_media(
            media_url,
            page_url,
            (
                ("User-Agent", self.profile.httpUserAgent()),
                ("Referer", page_url),
            ),
            indexes,
        )

    def _media_found(self, media_url, page_url, headers):
        if self.media is not None or self._pending_media is not None or self._closing:
            return
        try:
            page_url = validate_eeclass_page_url(page_url)
        except ValueError:
            return
        if not self._valid_media(media_url, page_url):
            return
        self._pending_media = (media_url, page_url, tuple(headers))
        token = self._probe_token
        self.status.setText("已找到影片，正在讀取時間軸索引…")
        self.page.toHtml(
            lambda html, page_url=page_url, token=token: self._intercepted_html_ready(
                html, page_url, token
            )
        )

    def _intercepted_html_ready(self, html, page_url, token):
        if (
            self._closing
            or token != self._probe_token
            or self.media is not None
            or self._pending_media is None
        ):
            return
        try:
            current_page_url = validate_eeclass_page_url(self.page.url().toString())
        except ValueError:
            return
        if current_page_url != page_url:
            return
        pending = self._pending_media
        self._pending_media = None
        if self._accept_media(*pending, extract_eeclass_indexes(html)):
            self.browser.stop()

    def _valid_media(self, media_url, page_url):
        if self.media is not None or self._closing:
            return False
        try:
            page_url = validate_eeclass_page_url(page_url)
        except ValueError:
            return False
        current_url = self.page.url().toString()
        if current_url:
            try:
                current_url = validate_eeclass_page_url(current_url)
            except ValueError:
                return False
            if current_url != page_url:
                return False
        return is_eeclass_mp4_request(media_url, page_url, True)

    def _accept_media(self, media_url, page_url, headers, indexes=()):
        if not self._valid_media(media_url, page_url):
            return False
        self.media = EeclassMediaRequest(
            media_url, page_url, tuple(headers), self.cookie_bridge.snapshot()
        )
        self.indexes = tuple(indexes)
        self.status.setText(
            f"已找到影片與 {len(self.indexes)} 個時間軸索引：{redacted_url(media_url)}"
        )
        self.download_button.setEnabled(True)
        return True

    def clear_login(self):
        self._probe_token += 1
        self.cookie_bridge.clear()
        self.profile.clearHttpCache()
        self.media = None
        self.indexes = ()
        self.destination = None
        self._pending_media = None
        self.download_button.setEnabled(False)
        self.status.setText("登入資料已清除")

    def choose_destination(self):
        if self.media is None:
            return
        filename = unquote(PurePosixPath(urlsplit(self.media.url).path).name)
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
        if not filename.lower().endswith(".mp4"):
            filename = "eeclass-video.mp4"
        destination, _ = QFileDialog.getSaveFileName(
            self, "儲存 EE-Class 影片", filename, "MP4 影片 (*.mp4)"
        )
        if not destination:
            return
        path = Path(destination)
        if path.suffix.lower() != ".mp4":
            path = path.with_suffix(".mp4")
        self.destination = path
        self.accept()

    def selection(self):
        return self.media, self.destination, self.indexes

    def done(self, result):
        self._closing = True
        self._probe_token += 1
        self.profile.setUrlRequestInterceptor(None)
        super().done(result)