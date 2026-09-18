import base64
import json
import ssl
from email.message import Message
from threading import Event
from urllib.error import HTTPError

import pytest

import video_frame_cutter.eeclass as eeclass_module
from video_frame_cutter.eeclass import (
    EeclassCookie,
    EeclassDownloadError,
    EeclassIndex,
    EeclassMediaRequest,
    EeclassRedirectHandler,
    download_eeclass_mp4,
    extract_eeclass_indexes,
    extract_eeclass_mp4_source,
    is_eeclass_mp4_request,
    redacted_url,
    validate_eeclass_page_url,
)
from video_frame_cutter.video import Cancelled


class FakeResponse:
    def __init__(self, data, content_type="video/mp4", content_length=None, status=200):
        self.data = data
        self.offset = 0
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def geturl(self):
        return "https://school.ouk.edu.tw/video/lecture.mp4?token=secret"

    def read(self, size):
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if self.error:
            raise self.error
        return self.response


def media_request(**changes):
    values = {
        "url": "https://school.ouk.edu.tw/video/lecture.mp4?token=secret",
        "page_url": "https://school.ouk.edu.tw/media/doc/252890",
        "headers": (
            ("User-Agent", "Test Browser"),
            ("Referer", "https://school.ouk.edu.tw/media/doc/252890"),
            ("Sec-Fetch-Dest", "video"),
            ("Range", "bytes=10-"),
            ("Authorization", "Bearer secret"),
        ),
        "cookies": (
            EeclassCookie("session", "secret", ".ouk.edu.tw", secure=True),
            EeclassCookie("wrong_path", "ignored", ".ouk.edu.tw", "/private"),
        ),
    }
    values.update(changes)
    return EeclassMediaRequest(**values)


def media_html(sources):
    payload = base64.b64encode(
        json.dumps({"src": sources}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return f"media = JSON.parse(atob('{payload}'));"


def test_extract_indexes_preserves_milliseconds_and_deduplicates_page_variants():
        item = """
                <li class="idx curr js-index-item" data-id="1398109" data-time="0">
                    <span class="time hint">00:00</span>
                    <div class="title js-title" title="課程片頭">課程片頭</div>
                </li>
        """
        html = f"""
                <div id="indexBox-inline">{item}</div>
                <div class="indexBox">
                    <li class="idx js-index-item" data-id="1398110" data-time="60105">
                        <div class="title js-title" title="Slide &amp; 1">Slide &amp; 1</div>
                    </li>
                </div>
                <div id="info-tabs-index">{item}</div>
        """

        assert extract_eeclass_indexes(html) == (
                EeclassIndex(0, "課程片頭", "1398109"),
                EeclassIndex(60105, "Slide & 1", "1398110"),
        )


def test_extract_indexes_uses_value_fallback_and_ignores_invalid_items():
        html = """
                <li class="js-index-item" data-time="2000">
                    <div class="title js-title"> Second   title </div>
                </li>
                <li class="js-index-item" data-time="1000">
                    <div class="title js-title" title="First title"></div>
                </li>
                <li class="js-index-item" data-time="2000">
                    <div class="title js-title">Second title</div>
                </li>
                <li class="js-index-item" data-time="-1">
                    <div class="title js-title">Negative</div>
                </li>
                <li class="js-index-item"><div class="title js-title">Missing time</div></li>
                <li class="js-index-item" data-time="3000"></li>
        """

        assert extract_eeclass_indexes(html) == (
                EeclassIndex(1000, "First title"),
                EeclassIndex(2000, "Second title"),
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://school.ouk.edu.tw/media/doc/252890",
        "https://school.ouk.edu.tw/media/doc/252890/?course=1",
    ],
)
def test_validate_eeclass_page_url_accepts_document_pages(url):
    assert validate_eeclass_page_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://school.ouk.edu.tw/media/doc/252890",
        "https://school.ouk.edu.tw/course/252890",
        "https://school.ouk.edu.tw/media/doc/not-a-number",
        "https://ouk.edu.tw.example.com/media/doc/252890",
        "https://user@school.ouk.edu.tw/media/doc/252890",
    ],
)
def test_validate_eeclass_page_url_rejects_untrusted_pages(url):
    with pytest.raises(ValueError, match="EE-Class"):
        validate_eeclass_page_url(url)


def test_media_request_requires_first_party_https_mp4_media():
    page = "https://school.ouk.edu.tw/media/doc/252890"

    assert is_eeclass_mp4_request(
        "https://school.ouk.edu.tw/video/lecture.MP4?token=secret", page, True
    )
    assert not is_eeclass_mp4_request(
        "https://cdn.school.ouk.edu.tw/video/lecture.mp4", page, True
    )
    assert not is_eeclass_mp4_request("https://cdn.example/video.mp4", page, True)
    assert not is_eeclass_mp4_request("http://school.ouk.edu.tw/video.mp4", page, True)
    assert not is_eeclass_mp4_request("https://school.ouk.edu.tw/video.m3u8", page, True)
    assert not is_eeclass_mp4_request(
        "https://school.ouk.edu.tw/video.mp4", page, False
    )


def test_extract_media_source_selects_highest_resolution_stably():
    page = "https://school.ouk.edu.tw/media/doc/252890"
    first_unknown = "https://school.ouk.edu.tw/video/unknown.mp4"
    first_hd = "https://school.ouk.edu.tw/video/first-hd.mp4?token=secret"
    sources = [
        {"title": "未知畫質", "src": first_unknown},
        {
            "title": "高畫質",
            "src": first_hd,
            "size": {"width": "1280", "height": "720"},
        },
        {
            "title": "同解析度",
            "src": "https://school.ouk.edu.tw/video/second-hd.mp4",
            "size": {"width": 1280, "height": 720},
        },
        {
            "title": "低畫質",
            "src": "https://school.ouk.edu.tw/video/sd.mp4",
            "size": {"width": "640", "height": "360"},
        },
    ]

    assert extract_eeclass_mp4_source(media_html(sources), page) == first_hd
    assert (
        extract_eeclass_mp4_source(media_html(sources[:1]), page) == first_unknown
    )


@pytest.mark.parametrize(
    "source",
    [
        {"src": "http://school.ouk.edu.tw/video/lecture.mp4"},
        {"src": "https://other.ouk.edu.tw/video/lecture.mp4"},
        {"src": "https://user@school.ouk.edu.tw/video/lecture.mp4"},
        {"src": "https://school.ouk.edu.tw/video/lecture.m3u8"},
        {"src": 123},
        "invalid",
    ],
)
def test_extract_media_source_rejects_untrusted_sources(source):
    page = "https://school.ouk.edu.tw/media/doc/252890"

    assert extract_eeclass_mp4_source(media_html([source]), page) is None


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        "media = JSON.parse(atob('not+valid'))",
        "media = JSON.parse(atob('bm90LWpzb24='))",
        media_html([]),
    ],
)
def test_extract_media_source_ignores_missing_or_malformed_payload(html):
    page = "https://school.ouk.edu.tw/media/doc/252890"

    assert extract_eeclass_mp4_source(html, page) is None


def test_redacted_url_removes_credentials_query_and_fragment():
    value = "https://user:password@school.ouk.edu.tw/video.mp4?token=secret#part"

    assert redacted_url(value) == "https://school.ouk.edu.tw/video.mp4"


def test_download_streams_with_filtered_headers_and_cookies(tmp_path, monkeypatch):
    data = b"\x00\x00\x00\x18ftypisom" + b"video-data" * 3
    opener = FakeOpener(FakeResponse(data, content_length=len(data)))
    monkeypatch.setattr(eeclass_module, "build_opener", lambda *args: opener)
    progress = []

    result = download_eeclass_mp4(
        media_request(), tmp_path / "lecture.mp4", progress=progress.append, chunk_size=8
    )

    assert result.read_bytes() == data
    assert progress[0] == 0
    assert progress[-1] == 100
    assert opener.timeout == 30
    assert opener.request.get_header("User-agent") == "Test Browser"
    assert opener.request.get_header("Referer").endswith("/media/doc/252890")
    assert opener.request.get_header("Sec-fetch-dest") == "video"
    assert opener.request.get_header("Range") is None
    assert opener.request.get_header("Authorization") == "Bearer secret"
    assert opener.request.get_header("Cookie") == "session=secret"
    assert not list(tmp_path.glob("*.part"))


def test_download_request_drops_authorization_for_different_host():
    media = media_request(
        url="https://media.ouk.edu.tw/video/lecture.mp4",
        page_url="https://school.ouk.edu.tw/media/doc/252890",
    )

    request = eeclass_module._download_request(media)

    assert request.get_header("Authorization") is None


def test_download_opener_uses_native_certificate_store(monkeypatch):
    native_context = object()
    https_handler = object()
    opener = object()
    protocols = []
    contexts = []
    handlers = []

    def fake_ssl_context(protocol):
        protocols.append(protocol)
        return native_context

    def fake_https_handler(*, context):
        contexts.append(context)
        return https_handler

    def fake_build_opener(*values):
        handlers.extend(values)
        return opener

    monkeypatch.setattr(eeclass_module.truststore, "SSLContext", fake_ssl_context)
    monkeypatch.setattr(eeclass_module, "HTTPSHandler", fake_https_handler)
    monkeypatch.setattr(eeclass_module, "build_opener", fake_build_opener)

    assert eeclass_module._download_opener() is opener
    assert protocols == [ssl.PROTOCOL_TLS_CLIENT]
    assert contexts == [native_context]
    assert isinstance(handlers[0], EeclassRedirectHandler)
    assert handlers[1] is https_handler


def test_redirect_handler_only_allows_same_https_host():
    handler = EeclassRedirectHandler()
    request = eeclass_module.Request(
        "https://school.ouk.edu.tw/video/old.mp4",
        headers={"Authorization": "Bearer secret", "Cookie": "session=secret"},
    )
    headers = Message()

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        headers,
        "https://school.ouk.edu.tw/video/new.mp4",
    )

    assert redirected.full_url.endswith("/video/new.mp4")
    assert redirected.get_header("Authorization") == "Bearer secret"
    with pytest.raises(EeclassDownloadError, match="不受信任"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            headers,
            "https://media.ouk.edu.tw/video/new.mp4",
        )
    with pytest.raises(EeclassDownloadError, match="不受信任"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            headers,
            "http://school.ouk.edu.tw/video/new.mp4",
        )


def test_download_rejects_partial_content(tmp_path, monkeypatch):
    data = b"\x00\x00\x00\x18ftypisomvideo-data"
    opener = FakeOpener(FakeResponse(data, content_length=len(data), status=206))
    monkeypatch.setattr(eeclass_module, "build_opener", lambda *args: opener)

    with pytest.raises(EeclassDownloadError, match="HTTP 206"):
        download_eeclass_mp4(media_request(), tmp_path / "lecture.mp4")

    assert not list(tmp_path.glob("*.part"))


def test_download_preserves_existing_file_when_length_is_wrong(tmp_path, monkeypatch):
    destination = tmp_path / "lecture.mp4"
    destination.write_bytes(b"existing")
    data = b"\x00\x00\x00\x18ftypisomshort"
    opener = FakeOpener(FakeResponse(data, content_length=100))
    monkeypatch.setattr(eeclass_module, "build_opener", lambda *args: opener)

    with pytest.raises(EeclassDownloadError, match="不完整"):
        download_eeclass_mp4(media_request(), destination)

    assert destination.read_bytes() == b"existing"
    assert not list(tmp_path.glob("*.part"))


def test_download_rejects_html_and_cleans_partial(tmp_path, monkeypatch):
    opener = FakeOpener(FakeResponse(b"login", content_type="text/html"))
    monkeypatch.setattr(eeclass_module, "build_opener", lambda *args: opener)

    with pytest.raises(EeclassDownloadError, match="登入可能已過期"):
        download_eeclass_mp4(media_request(), tmp_path / "lecture.mp4")

    assert not list(tmp_path.glob("*.part"))


def test_download_cancellation_cleans_partial(tmp_path, monkeypatch):
    cancel = Event()
    opener = FakeOpener(FakeResponse(b"video-data"))
    monkeypatch.setattr(eeclass_module, "build_opener", lambda *args: opener)
    cancel.set()

    with pytest.raises(Cancelled):
        download_eeclass_mp4(media_request(), tmp_path / "lecture.mp4", cancel=cancel)

    assert not list(tmp_path.glob("*.part"))


def test_download_error_does_not_expose_signed_query(tmp_path, monkeypatch):
    error = HTTPError(media_request().url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(
        eeclass_module, "build_opener", lambda *args: FakeOpener(error=error)
    )

    with pytest.raises(EeclassDownloadError) as captured:
        download_eeclass_mp4(media_request(), tmp_path / "lecture.mp4")

    assert "token" not in str(captured.value)
    assert "secret" not in str(captured.value)
    assert "HTTP 403" in str(captured.value)


def test_download_reports_certificate_verification_failure(tmp_path, monkeypatch):
    reason = ssl.SSLCertVerificationError(
        1, "certificate verify failed: Missing Subject Key Identifier"
    )
    monkeypatch.setattr(
        eeclass_module,
        "build_opener",
        lambda *args: FakeOpener(error=eeclass_module.URLError(reason)),
    )

    with pytest.raises(EeclassDownloadError, match="TLS 憑證驗證失敗"):
        download_eeclass_mp4(media_request(), tmp_path / "lecture.mp4")