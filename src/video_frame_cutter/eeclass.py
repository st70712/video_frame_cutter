import os
import re
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .video import check_cancel

EECLASS_DOMAIN = "ouk.edu.tw"
DOCUMENT_PATH = re.compile(r"^/media/doc/[0-9]+/?$")
FORWARDED_HEADERS = {
    "accept",
    "accept-language",
    "origin",
    "referer",
    "sec-fetch-dest",
    "user-agent",
}
MP4_CONTENT_TYPES = {"application/mp4", "application/octet-stream", "video/mp4"}


class EeclassDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class EeclassCookie:
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    expires: int | None = None


@dataclass(frozen=True)
class EeclassMediaRequest:
    url: str
    page_url: str
    headers: tuple[tuple[str, str], ...] = ()
    cookies: tuple[EeclassCookie, ...] = ()


def is_eeclass_host(hostname):
    hostname = (hostname or "").lower().rstrip(".")
    return hostname == EECLASS_DOMAIN or hostname.endswith(f".{EECLASS_DOMAIN}")


def validate_eeclass_page_url(value):
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() != "https"
        or not is_eeclass_host(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or not DOCUMENT_PATH.fullmatch(parsed.path)
    ):
        raise ValueError("請輸入有效的 EE-Class 影片頁面網址。")
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, parsed.query, ""))


def is_eeclass_mp4_request(url, page_url, is_media_resource):
    media = urlsplit(url)
    page = urlsplit(page_url)
    return (
        bool(is_media_resource)
        and media.scheme.lower() == "https"
        and is_eeclass_host(media.hostname)
        and media.username is None
        and media.password is None
        and is_eeclass_host(page.hostname)
        and media.hostname == page.hostname
        and PurePosixPath(media.path).suffix.lower() == ".mp4"
    )


def redacted_url(value):
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit(SplitResult(parsed.scheme, netloc, parsed.path, "", ""))


def _cookie_jar(cookies):
    jar = CookieJar()
    for item in cookies:
        domain = item.domain.lower()
        jar.set_cookie(
            Cookie(
                version=0,
                name=item.name,
                value=item.value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=bool(domain),
                domain_initial_dot=domain.startswith("."),
                path=item.path or "/",
                path_specified=True,
                secure=item.secure,
                expires=item.expires,
                discard=item.expires is None,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )
    return jar


def _download_request(media):
    media_host = (urlsplit(media.url).hostname or "").lower()
    page_host = (urlsplit(media.page_url).hostname or "").lower()
    headers = {}
    for name, value in media.headers:
        lowered = name.lower()
        if lowered in FORWARDED_HEADERS or (
            lowered == "authorization" and media_host == page_host
        ):
            headers[name] = value
    headers.setdefault("Sec-Fetch-Dest", "video")
    request = Request(media.url, headers=headers)
    _cookie_jar(media.cookies).add_cookie_header(request)
    return request


class EeclassRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        parsed = urlsplit(new_url)
        source_host = (urlsplit(request.full_url).hostname or "").lower()
        target_host = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() != "https"
            or not is_eeclass_host(target_host)
            or target_host != source_host
        ):
            raise EeclassDownloadError("影片下載被重新導向至不受信任的位置。")
        return super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )


def _validated_length(headers):
    value = headers.get("Content-Length")
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError):
        raise EeclassDownloadError("影片伺服器回傳了無效的檔案大小。") from None
    if length < 0:
        raise EeclassDownloadError("影片伺服器回傳了無效的檔案大小。")
    return length


def _validate_response(response):
    if response.status != 200:
        raise EeclassDownloadError(f"影片伺服器回傳 HTTP {response.status}，預期為 200。")
    final_url = response.geturl()
    parsed = urlsplit(final_url)
    if parsed.scheme.lower() != "https" or not is_eeclass_host(parsed.hostname):
        raise EeclassDownloadError("影片下載回應來自不受信任的位置。")
    content_type = response.headers.get_content_type().lower()
    if content_type not in MP4_CONTENT_TYPES:
        raise EeclassDownloadError("伺服器未回傳 MP4 影片，登入可能已過期。")
    return _validated_length(response.headers), content_type


def _looks_like_mp4(data):
    return len(data) >= 12 and 4 <= data.find(b"ftyp", 4, 36) <= 32


def download_eeclass_mp4(
    media,
    destination,
    cancel=None,
    progress=lambda value: None,
    timeout=30,
    chunk_size=1024 * 1024,
):
    destination = Path(destination).resolve()
    if destination.suffix.lower() != ".mp4":
        raise EeclassDownloadError("下載檔案必須使用 .mp4 副檔名。")
    if not destination.parent.is_dir():
        raise EeclassDownloadError("下載資料夾不存在。")
    if not is_eeclass_mp4_request(media.url, media.page_url, True):
        raise EeclassDownloadError("影片下載網址不受信任。")

    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
    request = _download_request(media)
    opener = build_opener(EeclassRedirectHandler())
    downloaded = 0
    progress(0)
    try:
        check_cancel(cancel)
        with opener.open(request, timeout=timeout) as response:
            total, _ = _validate_response(response)
            with temporary.open("xb") as output:
                prefix = bytearray()
                signature_checked = False
                while True:
                    check_cancel(cancel)
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    if not signature_checked:
                        prefix.extend(chunk[: 36 - len(prefix)])
                        if len(prefix) >= 36:
                            if not _looks_like_mp4(prefix):
                                raise EeclassDownloadError("下載內容不是有效的 MP4 影片。")
                            signature_checked = True
                    output.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        progress(min(99, downloaded * 100 // total))
                if not signature_checked and not _looks_like_mp4(prefix):
                    raise EeclassDownloadError("下載內容不是有效的 MP4 影片。")
                output.flush()
                os.fsync(output.fileno())
        check_cancel(cancel)
        if not downloaded:
            raise EeclassDownloadError("下載的影片是空檔案。")
        if total is not None and downloaded != total:
            raise EeclassDownloadError("影片下載不完整，請重新嘗試。")
        os.replace(temporary, destination)
        progress(100)
        return destination
    except (HTTPError, URLError, TimeoutError) as error:
        detail = f"HTTP {error.code}" if isinstance(error, HTTPError) else "網路連線失敗"
        raise EeclassDownloadError(
            f"無法下載影片（{redacted_url(media.url)}）：{detail}。"
        ) from None
    finally:
        temporary.unlink(missing_ok=True)