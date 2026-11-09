from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from contextlib import closing, nullcontext
from dataclasses import dataclass
from pathlib import Path

import httpx

from .paths import DATA_DIR


UPDATES_DIR: Path = DATA_DIR / "updates"
_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
_VERSION = re.compile(
    r"v?([0-9]+(?:\.[0-9]+)*)"
    r"(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
    r"(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
)
_INSTALLER = re.compile(r"CBMessengerAI_Setup_[0-9][0-9A-Za-z._-]*\.exe")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "cb-messenger-ai-updater",
}


class UpdateError(Exception):
    """사용자에게 표시할 업데이트 오류."""


@dataclass
class UpdateInfo:
    version: str
    tag: str
    name: str
    url: str
    size: int
    sha256: str
    notes: str
    html_url: str


def parse_version(s: str) -> tuple[int, ...]:
    match = _VERSION.fullmatch(s.strip()) if isinstance(s, str) else None
    if match is None:
        raise UpdateError("버전 정보를 확인할 수 없습니다.")
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError as exc:
        raise UpdateError("버전 정보를 확인할 수 없습니다.") from exc


def is_newer(remote: str, current: str) -> bool:
    remote_parts, current_parts = parse_version(remote), parse_version(current)
    length = max(len(remote_parts), len(current_parts))
    return (remote_parts + (0,) * (length - len(remote_parts))) > (
        current_parts + (0,) * (length - len(current_parts))
    )


def _verify():
    """인증서 확인 방식. Windows(운영체제) 인증서 목록을 씀.

    학교망처럼 보안 장비(예: Somansa)가 HTTPS 를 중간에서 검사하는 곳은 그 장비의 인증서가
    Windows 에만 등록돼 있어, 파이썬 기본(certifi) 목록으로는 GitHub 연결이 거부됨.
    truststore 가 없으면 기본 확인(True)으로 — 어느 쪽이든 확인을 끄지는 않음."""
    try:
        import ssl
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        return True


def _new_client() -> httpx.Client:
    return httpx.Client(verify=_verify())


def _request(
    client: httpx.Client,
    url: str,
    timeout: float,
    *,
    follow_redirects: bool = False,
    stream: bool = False,
    accept: str = _HEADERS["Accept"],
) -> httpx.Response:
    request = client.build_request("GET", url, headers={**_HEADERS, "Accept": accept}, timeout=timeout)
    # 전달받은 클라이언트의 기본 인증도 업데이트 요청에는 사용하지 않는다.
    request.headers.pop("Authorization", None)
    request.headers.pop("Proxy-Authorization", None)
    return client.send(
        request, auth=None, follow_redirects=follow_redirects, stream=stream
    )


_BINARY = "application/octet-stream"


def _asset_url(asset: dict, repo: str) -> str:
    # 브라우저용 주소(github.com/.../releases/download/...)는 학교망 보안 장비가 막음(504) →
    # API 자산 주소로 받음 (Accept: application/octet-stream → GitHub 이 실제 파일 주소로 넘겨줌)
    url = asset.get("url")
    if not isinstance(url, str) or not re.fullmatch(
        rf"https://api\.github\.com/repos/{re.escape(repo)}/releases/assets/[0-9]+", url
    ):
        raise UpdateError("업데이트 파일의 주소가 올바르지 않습니다.")
    return url


def check_latest(
    repo: str,
    current: str,
    http: httpx.Client | None = None,
    timeout: float = 15,
) -> UpdateInfo | None:
    if not isinstance(repo, str) or _REPO.fullmatch(repo) is None:
        raise UpdateError("업데이트 저장소 설정이 올바르지 않습니다.")
    try:
        with _new_client() if http is None else nullcontext(http) as client:
            response = _request(
                client, f"https://api.github.com/repos/{repo}/releases/latest", timeout
            )
            if response.status_code == 404:
                raise UpdateError(
                    "업데이트 정보를 볼 수 없습니다. 저장소가 비공개이거나 아직 배포된 버전이 없습니다."
                )
            response.raise_for_status()
            release = response.json()
            if not isinstance(release, dict):
                raise UpdateError("업데이트 정보를 읽을 수 없습니다.")
            if release.get("draft") or release.get("prerelease"):
                return None
            tag = release.get("tag_name")
            if not is_newer(tag, current):
                return None
            assets = release.get("assets", [])
            if not isinstance(assets, list):
                raise UpdateError("업데이트 정보를 읽을 수 없습니다.")
            installer = next(
                (
                    asset for asset in assets
                    if isinstance(asset, dict)
                    and isinstance(asset.get("name"), str)
                    and _INSTALLER.fullmatch(asset["name"])
                ),
                None,
            )
            if installer is None:
                raise UpdateError("업데이트 설치 파일을 찾을 수 없습니다.")
            url = _asset_url(installer, repo)
            name = installer["name"]
            digest = installer.get("digest")
            digest_match = (
                re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest)
                if isinstance(digest, str) else None
            )
            if digest_match:
                sha256 = digest_match.group(1).lower()
            else:
                companion = next(
                    (
                        asset for asset in assets
                        if isinstance(asset, dict)
                        and asset.get("name") == name + ".sha256"
                    ),
                    None,
                )
                if companion is None:
                    raise UpdateError("파일 확인 정보가 없어 업데이트할 수 없습니다.")
                checksum_response = _request(
                    client, _asset_url(companion, repo), timeout, follow_redirects=True,
                    accept=_BINARY,
                )
                checksum_response.raise_for_status()
                tokens = checksum_response.text.split()
                if not tokens or _SHA256.fullmatch(tokens[0]) is None:
                    raise UpdateError("파일 확인 정보가 올바르지 않아 업데이트할 수 없습니다.")
                sha256 = tokens[0].lower()
            size = installer.get("size", 0)
            notes = release.get("body") or ""
            html_url = release.get("html_url") or ""
            if (
                not isinstance(size, int) or isinstance(size, bool) or size < 0
                or not isinstance(notes, str) or not isinstance(html_url, str)
            ):
                raise UpdateError("업데이트 정보를 읽을 수 없습니다.")
            return UpdateInfo(
                version=tag.strip().removeprefix("v"), tag=tag,
                name=name, url=url, size=size, sha256=sha256,
                notes=notes, html_url=html_url,
            )
    except httpx.HTTPStatusError as exc:
        raise UpdateError("업데이트 정보를 가져오지 못했습니다. 잠시 후 다시 시도하세요.") from exc
    except httpx.RequestError as exc:
        raise UpdateError("업데이트 서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.") from exc
    except (ValueError, httpx.InvalidURL) as exc:
        raise UpdateError("업데이트 정보를 읽을 수 없습니다.") from exc


def download(
    info: UpdateInfo,
    dest_dir: Path | None = None,
    http: httpx.Client | None = None,
    progress=None,
) -> Path:
    if (
        not isinstance(info.name, str) or not info.name
        or info.name in (".", "..")
        or any(char in info.name for char in ("/", "\\", ":", "\0"))
    ):
        raise UpdateError("업데이트 파일 이름이 올바르지 않습니다.")
    if not isinstance(info.sha256, str) or _SHA256.fullmatch(info.sha256) is None:
        raise UpdateError("파일 확인 정보가 올바르지 않아 업데이트할 수 없습니다.")
    if not isinstance(info.size, int) or isinstance(info.size, bool) or info.size < 0:
        raise UpdateError("업데이트 파일 크기 정보가 올바르지 않습니다.")
    directory = UPDATES_DIR if dest_dir is None else dest_dir
    destination = directory / info.name
    partial = directory / (info.name + ".part")
    limit = min(max(info.size, 1) * 1.1 + 1024 * 1024, _MAX_DOWNLOAD_BYTES)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        done = 0
        with _new_client() if http is None else nullcontext(http) as client:
            with closing(_request(
                client, info.url, 30, follow_redirects=True, stream=True, accept=_BINARY,
            )) as response:
                response.raise_for_status()
                with partial.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        done += len(chunk)
                        if done > limit:
                            raise UpdateError("업데이트 파일이 허용된 크기를 초과했습니다.")
                        output.write(chunk)
                        hasher.update(chunk)
                        if progress is not None:
                            progress(done, info.size)
        if hasher.hexdigest() != info.sha256.lower():
            raise UpdateError("받은 파일이 손상되었거나 바뀌었습니다. 다시 시도하세요.")
        os.replace(partial, destination)
        return destination
    except httpx.HTTPStatusError as exc:
        raise UpdateError("업데이트 파일을 받지 못했습니다. 잠시 후 다시 시도하세요.") from exc
    except httpx.RequestError as exc:
        raise UpdateError("업데이트 파일을 받지 못했습니다. 인터넷 연결을 확인하세요.") from exc
    except (ValueError, httpx.InvalidURL) as exc:
        raise UpdateError("업데이트 파일의 주소가 올바르지 않습니다.") from exc
    except OSError as exc:
        raise UpdateError("업데이트 파일을 저장할 수 없습니다. 저장 공간과 폴더 권한을 확인하세요.") from exc
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError as exc:
            raise UpdateError("받던 파일을 지울 수 없습니다. 폴더 권한을 확인한 뒤 다시 시도하세요.") from exc


def launch_installer(path: Path, watch: bool = True) -> None:
    """설치 파일을 조용히 실행. watch=False 면 설치 뒤 캘린더만(감시 없이) 다시 켜도록 알림."""
    if sys.platform != "win32":
        raise UpdateError("업데이트 설치는 Windows에서만 실행할 수 있습니다.")
    try:
        resolved = path.resolve()
        if (
            path.suffix.lower() != ".exe" or resolved.parent != UPDATES_DIR.resolve()
            or resolved.suffix.lower() != ".exe" or not resolved.is_file()
        ):
            raise UpdateError("업데이트 폴더 안의 설치 파일만 실행할 수 있습니다.")
        args = [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
        if not watch:
            args.append("/RELAUNCHMODE=nowatch")   # installer.iss 의 RelaunchNoWatch 가 읽음
        subprocess.Popen(
            args,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    except (OSError, RuntimeError) as exc:
        raise UpdateError("업데이트 설치 파일을 실행할 수 없습니다. 다시 시도하세요.") from exc
