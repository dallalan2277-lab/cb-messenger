from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from app import updater


REPO = "example-owner/example-repo"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
NAME = "CBMessengerAI_Setup_0.7.1.exe"
ASSET_URL = f"https://api.github.com/repos/{REPO}/releases/assets/101"
CHECKSUM_URL = f"https://api.github.com/repos/{REPO}/releases/assets/102"
CONTENTS = b"synthetic installer contents for updater tests"
SHA256 = hashlib.sha256(CONTENTS).hexdigest()


def _asset(**changes):
    asset = {
        "name": NAME,
        "url": ASSET_URL,
        "size": len(CONTENTS),
        "digest": f"sha256:{SHA256}",
    }
    asset.update(changes)
    return asset


def _release(**changes):
    release = {
        "tag_name": "v0.7.1",
        "draft": False,
        "prerelease": False,
        "body": "업데이트 시험용 안내입니다.",
        "html_url": f"https://github.com/{REPO}/releases/tag/v0.7.1",
        "assets": [_asset()],
    }
    release.update(changes)
    return release


def _info(**changes):
    info = updater.UpdateInfo(
        version="0.7.1",
        tag="v0.7.1",
        name=NAME,
        url=ASSET_URL,
        size=len(CONTENTS),
        sha256=SHA256,
        notes="",
        html_url=f"https://github.com/{REPO}/releases/tag/v0.7.1",
    )
    return replace(info, **changes)


class _Chunks(httpx.SyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error
        self.yielded = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk
        if self.error is not None:
            raise self.error


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("v0.7.0", (0, 7, 0)),
        ("0.7", (0, 7)),
        ("0.7.0-beta", (0, 7, 0)),
        ("v1.2.3-rc.1", (1, 2, 3)),
        ("10.20.30.40", (10, 20, 30, 40)),
    ],
)
def test_parse_version(value, expected):
    assert updater.parse_version(value) == expected


@pytest.mark.parametrize("value", ["", "v", "latest", "vone.2", "1..2", "1.2junk"])
def test_parse_version_invalid(value):
    with pytest.raises(updater.UpdateError):
        updater.parse_version(value)


@pytest.mark.parametrize(
    ("remote", "current", "expected"),
    [
        ("v0.7.1", "0.7.0", True),
        ("0.10", "0.9.9", True),
        ("0.7.0.1", "0.7", True),
        ("0.7", "v0.7.0", False),
        ("0.7.0", "0.7", False),
        ("0.7.0-beta", "0.7.0", False),
        ("0.6.9", "0.7.0", False),
    ],
)
def test_is_newer(remote, current, expected):
    assert updater.is_newer(remote, current) is expected


def test_latest_newer_with_digest():
    payload = _release(assets=[_asset(digest=f"sha256:{SHA256.upper()}")])

    def handle(request):
        assert request.method == "GET"
        assert str(request.url) == API_URL
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.headers["User-Agent"] == "cb-messenger-ai-updater"
        assert "Authorization" not in request.headers
        assert "Proxy-Authorization" not in request.headers
        assert request.extensions["timeout"]["read"] == 7
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        info = updater.check_latest(REPO, "0.7.0", http=client, timeout=7)
        assert not client.is_closed

    assert info == updater.UpdateInfo(
        version="0.7.1",
        tag="v0.7.1",
        name=NAME,
        url=ASSET_URL,
        size=len(CONTENTS),
        sha256=SHA256,
        notes=payload["body"],
        html_url=payload["html_url"],
    )


@pytest.mark.parametrize("digest", [None, "sha512:invalid", "sha256:invalid"])
def test_latest_with_sha256_companion_and_redirect(digest):
    checksum_url = CHECKSUM_URL
    redirected_url = "https://release-assets.githubusercontent.com/example/checksum"
    visited = []
    payload = _release(
        body=None,
        assets=[
            _asset(digest=digest),
            {"name": NAME + ".sha256", "url": checksum_url},
        ],
    )

    def handle(request):
        url = str(request.url)
        visited.append(url)
        assert "Authorization" not in request.headers
        if url == API_URL:
            return httpx.Response(200, json=payload)
        if url == checksum_url:
            return httpx.Response(302, headers={"Location": redirected_url})
        assert url == redirected_url
        return httpx.Response(200, text=f"{SHA256.upper()}  {NAME}\n")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        info = updater.check_latest(REPO, "0.7.0", http=client)

    assert info is not None
    assert info.sha256 == SHA256
    assert info.notes == ""
    assert visited == [API_URL, checksum_url, redirected_url]


@pytest.mark.parametrize("current", ["0.7.1", "0.8", "v0.7.1.0"])
def test_latest_not_newer(current):
    def handle(request):
        assert str(request.url) == API_URL
        return httpx.Response(200, json=_release(assets=[]))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert updater.check_latest(REPO, current, http=client) is None


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_latest_ignores_drafts_and_prereleases(flag):
    payload = _release(**{flag: True, "assets": []})
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        assert updater.check_latest(REPO, "0.7.0", http=client) is None


def test_latest_404_message():
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as client:
        with pytest.raises(updater.UpdateError) as exc:
            updater.check_latest(REPO, "0.7.0", http=client)
    assert str(exc.value) == (
        "업데이트 정보를 볼 수 없습니다. 저장소가 비공개이거나 아직 배포된 버전이 없습니다."
    )


@pytest.mark.parametrize("status", [403, 429, 500])
def test_latest_http_errors_are_user_facing(status):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status))
    ) as client:
        with pytest.raises(updater.UpdateError) as exc:
            updater.check_latest(REPO, "0.7.0", http=client)
    assert any("가" <= char <= "힣" for char in str(exc.value))


def test_latest_network_error_is_user_facing():
    def handle(request):
        raise httpx.ConnectError("synthetic connection failure", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(updater.UpdateError) as exc:
            updater.check_latest(REPO, "0.7.0", http=client)
    assert any("가" <= char <= "힣" for char in str(exc.value))


def test_latest_missing_checksum_refused():
    payload = _release(assets=[_asset(digest=None)])
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(updater.UpdateError):
            updater.check_latest(REPO, "0.7.0", http=client)


@pytest.mark.parametrize("checksum", ["", "not-a-checksum", "a" * 63, "g" * 64])
def test_latest_invalid_companion_checksum_refused(checksum):
    checksum_url = CHECKSUM_URL
    payload = _release(
        assets=[
            _asset(digest=None),
            {"name": NAME + ".sha256", "url": checksum_url},
        ]
    )

    def handle(request):
        if str(request.url) == API_URL:
            return httpx.Response(200, json=payload)
        assert str(request.url) == checksum_url
        return httpx.Response(200, text=checksum)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(updater.UpdateError):
            updater.check_latest(REPO, "0.7.0", http=client)


@pytest.mark.parametrize(
    "repo",
    ["", "owner", "owner/repo/extra", "../repo", "owner/repo?query", "owner_/repo", "owner/repo\n"],
)
def test_latest_bad_repo_rejected_without_request(repo):
    def handle(request):
        pytest.fail("An invalid repository must be rejected before making a request")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(updater.UpdateError):
            updater.check_latest(repo, "0.7.0", http=client)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/installer.exe",
        f"http://api.github.com/repos/{REPO}/releases/assets/101",
        f"https://api.github.com/repos/another-owner/example-repo/releases/assets/101",
        f"https://api.github.com/repos/{REPO}/releases/assets/101/extra",
        f"https://github.com/{REPO}/releases/download/v0.7.1/{NAME}",
    ],
)
def test_latest_wrong_url_prefix_refused(url):
    payload = _release(assets=[_asset(url=url)])
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(updater.UpdateError):
            updater.check_latest(REPO, "0.7.0", http=client)


@pytest.mark.parametrize("name", ["unrelated.exe", "CBMessengerAI_Setup_latest.exe", NAME + ".zip"])
def test_latest_requires_matching_installer_asset(name):
    payload = _release(assets=[_asset(name=name)])
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(updater.UpdateError):
            updater.check_latest(REPO, "0.7.0", http=client)


def test_download_ok_follows_redirects_and_reports_progress(tmp_path, monkeypatch):
    destination = tmp_path / "updates" / "nested"
    monkeypatch.setattr(updater, "UPDATES_DIR", destination)
    redirect_url = "https://release-assets.githubusercontent.com/example/installer"
    visited = []
    reported = []

    def handle(request):
        url = str(request.url)
        visited.append(url)
        assert "Authorization" not in request.headers
        if url == ASSET_URL:
            return httpx.Response(302, headers={"Location": redirect_url})
        assert url == redirect_url
        return httpx.Response(200, stream=_Chunks([CONTENTS[:8], CONTENTS[8:]]))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        path = updater.download(
            _info(), http=client, progress=lambda done, total: reported.append((done, total))
        )
        assert not client.is_closed

    assert path == destination / NAME
    assert path.read_bytes() == CONTENTS
    assert not (destination / (NAME + ".part")).exists()
    assert visited == [ASSET_URL, redirect_url]
    assert reported[-1] == (len(CONTENTS), len(CONTENTS))
    assert all(total == len(CONTENTS) for _, total in reported)
    assert [done for done, _ in reported] == sorted(done for done, _ in reported)


def test_download_sha_mismatch_deletes_partial_file(tmp_path):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=CONTENTS))
    ) as client:
        with pytest.raises(updater.UpdateError) as exc:
            updater.download(_info(sha256="0" * 64), tmp_path, http=client)

    assert str(exc.value) == "받은 파일이 손상되었거나 바뀌었습니다. 다시 시도하세요."
    assert not (tmp_path / NAME).exists()
    assert not (tmp_path / (NAME + ".part")).exists()


def test_download_failure_preserves_existing_installer(tmp_path):
    existing = tmp_path / NAME
    existing.write_bytes(b"previous verified installer")
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=CONTENTS))
    ) as client:
        with pytest.raises(updater.UpdateError):
            updater.download(_info(sha256="0" * 64), tmp_path, http=client)

    assert existing.read_bytes() == b"previous verified installer"
    assert not (tmp_path / (NAME + ".part")).exists()


@pytest.mark.parametrize("advertised_size,chunk_count", [(1, 2), (400 * 1024 * 1024, 302)])
def test_download_size_caps_cleanup(tmp_path, advertised_size, chunk_count):
    chunk = b"x" * (1024 * 1024)
    stream = _Chunks(chunk for _ in range(chunk_count))
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    ) as client:
        with pytest.raises(updater.UpdateError) as exc:
            updater.download(_info(size=advertised_size), tmp_path, http=client)

    assert str(exc.value) != "받은 파일이 손상되었거나 바뀌었습니다. 다시 시도하세요."
    assert stream.yielded <= (2 if advertised_size == 1 else 301)
    assert not (tmp_path / NAME).exists()
    assert not (tmp_path / (NAME + ".part")).exists()


def test_download_network_failure_cleans_partial_file(tmp_path):
    def handle(request):
        error = httpx.ReadError("synthetic interrupted transfer", request=request)
        return httpx.Response(200, stream=_Chunks([CONTENTS[:8]], error=error))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(updater.UpdateError):
            updater.download(_info(), tmp_path, http=client)

    assert not (tmp_path / NAME).exists()
    assert not (tmp_path / (NAME + ".part")).exists()


def test_download_cleanup_error_is_user_facing(tmp_path):
    (tmp_path / (NAME + ".part")).mkdir()
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=CONTENTS))
    ) as client:
        with pytest.raises(updater.UpdateError) as exc:
            updater.download(_info(), tmp_path, http=client)

    assert any("가" <= char <= "힣" for char in str(exc.value))
    assert not (tmp_path / NAME).exists()


@pytest.mark.parametrize("name", ["../setup.exe", "..\\setup.exe", "/setup.exe", "C:\\setup.exe"])
def test_download_refuses_path_separators(tmp_path, name):
    def handle(request):
        pytest.fail("An invalid file name must be rejected before downloading")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(updater.UpdateError):
            updater.download(_info(name=name), tmp_path, http=client)
    assert list(tmp_path.iterdir()) == []


def test_supplied_client_auth_is_not_sent(tmp_path):
    def handle(request):
        assert "Authorization" not in request.headers
        assert "Proxy-Authorization" not in request.headers
        if str(request.url) == API_URL:
            return httpx.Response(200, json=_release())
        assert str(request.url) == ASSET_URL
        return httpx.Response(200, content=CONTENTS)

    with httpx.Client(
        transport=httpx.MockTransport(handle), auth=("example", "example")
    ) as client:
        info = updater.check_latest(REPO, "0.7.0", http=client)
        assert info is not None
        assert updater.download(info, tmp_path, http=client).read_bytes() == CONTENTS


@pytest.fixture
def windows_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "UPDATES_DIR", tmp_path)
    monkeypatch.setattr(updater.subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)
    monkeypatch.setattr(updater.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    popen = Mock()
    monkeypatch.setattr(updater.subprocess, "Popen", popen)
    return popen


def test_launch_installer_valid_path(tmp_path, windows_launcher):
    path = tmp_path / NAME
    path.write_bytes(CONTENTS)

    updater.launch_installer(path)

    windows_launcher.assert_called_once_with(
        [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
        creationflags=0x00000008 | 0x00000200,
        close_fds=True,
    )


def test_verify_uses_os_trust_store():
    import ssl
    ctx = updater._verify()
    assert isinstance(ctx, ssl.SSLContext)   # truststore 설치 시 운영체제 인증서 목록 사용 (확인을 끄지 않음)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_launch_installer_calendar_only_mode(tmp_path, windows_launcher):
    path = tmp_path / NAME
    path.write_bytes(CONTENTS)

    updater.launch_installer(path, watch=False)

    windows_launcher.assert_called_once_with(
        [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
         "/RELAUNCHMODE=nowatch"],
        creationflags=0x00000008 | 0x00000200,
        close_fds=True,
    )


def test_launch_installer_preserves_relative_path(tmp_path, monkeypatch, windows_launcher):
    monkeypatch.chdir(tmp_path)
    path = Path(NAME)
    path.write_bytes(CONTENTS)

    updater.launch_installer(path)

    windows_launcher.assert_called_once_with(
        [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
        creationflags=0x00000008 | 0x00000200,
        close_fds=True,
    )


def test_launch_installer_refuses_outside_updates_dir(tmp_path, monkeypatch, windows_launcher):
    updates = tmp_path / "updates"
    updates.mkdir()
    monkeypatch.setattr(updater, "UPDATES_DIR", updates)
    outside = tmp_path / NAME
    outside.write_bytes(CONTENTS)

    with pytest.raises(updater.UpdateError):
        updater.launch_installer(updates / ".." / NAME)
    windows_launcher.assert_not_called()


def test_launch_installer_refuses_nested_file(tmp_path, windows_launcher):
    nested = tmp_path / "nested"
    nested.mkdir()
    path = nested / NAME
    path.write_bytes(CONTENTS)

    with pytest.raises(updater.UpdateError):
        updater.launch_installer(path)
    windows_launcher.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "wrong_suffix", "directory"])
def test_launch_installer_requires_existing_exe_file(tmp_path, windows_launcher, kind):
    path = tmp_path / ("setup.txt" if kind == "wrong_suffix" else NAME)
    if kind == "wrong_suffix":
        path.write_bytes(CONTENTS)
    elif kind == "directory":
        path.mkdir()

    with pytest.raises(updater.UpdateError):
        updater.launch_installer(path)
    windows_launcher.assert_not_called()


def test_launch_installer_refuses_non_windows(tmp_path, monkeypatch, windows_launcher):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    path = tmp_path / NAME
    path.write_bytes(CONTENTS)

    with pytest.raises(updater.UpdateError):
        updater.launch_installer(path)
    windows_launcher.assert_not_called()


def test_download_uses_api_asset_url_with_binary_accept(tmp_path):
    seen = []

    def handle(request):
        seen.append((str(request.url), request.headers.get("Accept")))
        return httpx.Response(200, content=CONTENTS)

    info = updater.UpdateInfo(version="0.7.1", tag="v0.7.1", name=NAME, url=ASSET_URL,
                              size=len(CONTENTS), sha256=SHA256, notes="", html_url="")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        path = updater.download(info, dest_dir=tmp_path, http=client)
    assert path.read_bytes() == CONTENTS
    assert seen == [(ASSET_URL, "application/octet-stream")]
