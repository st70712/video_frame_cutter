import base64
import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QUrl
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInfo
from PySide6.QtWidgets import QFileDialog, QWidget

from video_frame_cutter.eeclass import EeclassCookie
from video_frame_cutter.ui.eeclass_dialog import (
    EeclassCookieBridge,
    EeclassDownloadDialog,
    EeclassRequestInterceptor,
    create_eeclass_profile,
)


class FakeCookieStore:
    class FakeSignal:
        def __init__(self):
            self.callback = None

        def connect(self, callback):
            self.callback = callback

    def __init__(self):
        self.cookieAdded = self.FakeSignal()
        self.cookieRemoved = self.FakeSignal()
        self.loaded = False
        self.deleted = False

    def loadAllCookies(self):
        self.loaded = True

    def deleteAllCookies(self):
        self.deleted = True


class FakeRequestInfo:
    def __init__(self, url, page, resource_type):
        self.url = QUrl(url)
        self.page = QUrl(page)
        self.resource_type = resource_type

    def requestUrl(self):
        return self.url

    def firstPartyUrl(self):
        return self.page

    def resourceType(self):
        return self.resource_type

    def httpHeaders(self):
        return {
            QByteArray(b"User-Agent"): QByteArray(b"Test Browser"),
            QByteArray(b"Sec-Fetch-Dest"): QByteArray(b"video"),
            QByteArray(b"Cookie"): QByteArray(b"secret=hidden"),
            QByteArray(b"Range"): QByteArray(b"bytes=10-"),
        }


class FakeHtmlPage:
    def __init__(self, url):
        self.current_url = QUrl(url)
        self.callbacks = []

    def url(self):
        return self.current_url

    def toHtml(self, callback):
        self.callbacks.append(callback)


def media_html(sources):
    payload = base64.b64encode(
        json.dumps({"src": sources}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return f"media = JSON.parse(atob('{payload}'));"


def index_html(contents="課程片頭", milliseconds=0, index_id="1398109"):
        return f"""
                <li class="idx js-index-item" data-id="{index_id}" data-time="{milliseconds}">
                    <div class="title js-title" title="{contents}">{contents}</div>
                </li>
        """


def test_cookie_bridge_snapshots_and_clears_cookie_data():
    store = FakeCookieStore()
    bridge = EeclassCookieBridge(store)
    cookie = QNetworkCookie(b"session", b"secret")
    cookie.setDomain(".ouk.edu.tw")
    cookie.setPath("/")
    cookie.setSecure(True)

    store.cookieAdded.callback(cookie)

    assert store.loaded
    assert bridge.snapshot()[0].name == "session"
    assert bridge.snapshot()[0].value == "secret"
    assert bridge.snapshot()[0].secure
    store.cookieRemoved.callback(cookie)
    assert not bridge.snapshot()
    bridge.clear()
    assert store.deleted


def test_request_interceptor_emits_only_allowed_headers(qtbot):
    interceptor = EeclassRequestInterceptor()
    captured = []
    interceptor.media_found.connect(lambda *values: captured.append(values))
    info = FakeRequestInfo(
        "https://school.ouk.edu.tw/video/lecture.mp4?token=secret",
        "https://school.ouk.edu.tw/media/doc/252890",
        QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMedia,
    )

    interceptor.interceptRequest(info)
    qtbot.waitUntil(lambda: bool(captured))

    assert captured[0][0].endswith("lecture.mp4?token=secret")
    assert captured[0][1].endswith("/media/doc/252890")
    assert captured[0][2] == (
        ("User-Agent", "Test Browser"),
        ("Sec-Fetch-Dest", "video"),
    )


def test_request_interceptor_ignores_non_media_requests():
    interceptor = EeclassRequestInterceptor()
    captured = []
    interceptor.media_found.connect(lambda *values: captured.append(values))
    info = FakeRequestInfo(
        "https://school.ouk.edu.tw/video/lecture.mp4",
        "https://school.ouk.edu.tw/media/doc/252890",
        QWebEngineUrlRequestInfo.ResourceType.ResourceTypeScript,
    )

    interceptor.interceptRequest(info)

    assert not captured


def test_download_dialog_uses_persistent_profile_and_validates_url(qtbot, tmp_path):
    owner = QWidget()
    qtbot.addWidget(owner)
    profile = create_eeclass_profile(owner, tmp_path)
    dialog = EeclassDownloadDialog(profile, owner)
    qtbot.addWidget(dialog)

    assert profile.persistentStoragePath() == str(tmp_path / "storage")
    assert not dialog.download_button.isEnabled()
    dialog.url.setText("https://example.com/media/doc/252890")
    dialog.open_page()

    assert "有效的 EE-Class" in dialog.status.text()
    assert dialog.browser.url().isEmpty()
    dialog.reject()


def test_download_dialog_redacts_candidate_and_returns_destination(
    qtbot, tmp_path, monkeypatch
):
    owner = QWidget()
    qtbot.addWidget(owner)
    profile = create_eeclass_profile(owner, tmp_path / "profile")
    dialog = EeclassDownloadDialog(profile, owner)
    page_url = "https://school.ouk.edu.tw/media/doc/252890"
    page = FakeHtmlPage(page_url)
    dialog.page = page
    media_url = "https://school.ouk.edu.tw/video/lecture.mp4?token=secret"
    dialog._media_found(
        media_url,
        page_url,
        (("User-Agent", "Test Browser"),),
    )
    page.callbacks[-1](index_html())
    destination = tmp_path / "saved"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args: (str(destination), "")
    )

    assert dialog.download_button.isEnabled()
    assert "token" not in dialog.status.text()
    assert "secret" not in dialog.status.text()
    dialog.choose_destination()

    media, selected, indexes = dialog.selection()
    assert media.url == media_url
    assert selected == Path(f"{destination}.mp4")
    assert indexes[0].title == "課程片頭"


def test_download_dialog_finds_embedded_media_without_playback(qtbot, tmp_path):
    owner = QWidget()
    qtbot.addWidget(owner)
    profile = create_eeclass_profile(owner, tmp_path / "profile")
    dialog = EeclassDownloadDialog(profile, owner)
    page_url = "https://school.ouk.edu.tw/media/doc/252890"
    page = FakeHtmlPage(page_url)
    dialog.page = page
    cookie = EeclassCookie("session", "secret", ".ouk.edu.tw", secure=True)
    dialog.cookie_bridge.cookies[(b"session", ".ouk.edu.tw", "/")] = cookie
    media_url = "https://school.ouk.edu.tw/video/lecture-hd.mp4?token=secret"

    dialog._load_started()
    dialog._load_finished(True)
    page.callbacks[-1](
        media_html(
            [
                {
                    "src": "https://school.ouk.edu.tw/video/lecture-sd.mp4",
                    "size": {"width": "640", "height": "360"},
                },
                {
                    "src": media_url,
                    "size": {"width": "1280", "height": "720"},
                },
            ]
        )
        + index_html()
    )

    media, _, indexes = dialog.selection()
    assert dialog.download_button.isEnabled()
    assert media.url == media_url
    assert media.cookies == (cookie,)
    assert ("Referer", page_url) in media.headers
    assert any(name == "User-Agent" and value for name, value in media.headers)
    assert indexes[0].milliseconds == 0
    assert indexes[0].title == "課程片頭"
    assert "token" not in dialog.status.text()
    assert "secret" not in dialog.status.text()
    dialog.reject()


def test_download_dialog_ignores_stale_html_probe(qtbot, tmp_path):
    owner = QWidget()
    qtbot.addWidget(owner)
    profile = create_eeclass_profile(owner, tmp_path / "profile")
    dialog = EeclassDownloadDialog(profile, owner)
    page_url = "https://school.ouk.edu.tw/media/doc/252890"
    page = FakeHtmlPage(page_url)
    dialog.page = page

    dialog._load_started()
    dialog._load_finished(True)
    stale_callback = page.callbacks[-1]
    dialog._load_started()
    stale_callback(
        media_html([{"src": "https://school.ouk.edu.tw/video/lecture.mp4"}])
    )

    assert dialog.media is None
    assert not dialog.download_button.isEnabled()
    dialog._load_finished(True)
    page.callbacks[-1]("<html></html>")
    assert "找不到可下載" in dialog.status.text()
    dialog.reject()


def test_download_dialog_accepts_media_without_indexes_and_ignores_stale_intercept(
    qtbot, tmp_path
):
    owner = QWidget()
    qtbot.addWidget(owner)
    profile = create_eeclass_profile(owner, tmp_path / "profile")
    dialog = EeclassDownloadDialog(profile, owner)
    page_url = "https://school.ouk.edu.tw/media/doc/252890"
    page = FakeHtmlPage(page_url)
    dialog.page = page
    media_url = "https://school.ouk.edu.tw/video/lecture.mp4"

    dialog._media_found(media_url, page_url, ())
    stale_callback = page.callbacks[-1]
    dialog._load_started()
    stale_callback(index_html())

    assert dialog.media is None
    assert not dialog.download_button.isEnabled()

    dialog._media_found(media_url, page_url, ())
    page.callbacks[-1]("<html><body>No indexes</body></html>")

    media, _, indexes = dialog.selection()
    assert media.url == media_url
    assert indexes == ()
    assert dialog.download_button.isEnabled()
    assert "0 個時間軸索引" in dialog.status.text()
    dialog.reject()