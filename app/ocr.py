"""Windows.Media.Ocr 를 이용한 한국어 글자 인식.

Windows 전용 모듈은 모두 함수 안에서 불러옵니다. 따라서 다른 운영체제에서도
이 모듈 자체는 안전하게 import 할 수 있습니다.
"""
from __future__ import annotations

import asyncio
import os
import queue
import tempfile
import threading
from pathlib import Path
from typing import Callable, TypeVar


class OcrError(RuntimeError):
    """사용자에게 보여 줄 수 있는 글자 인식 오류."""


_T = TypeVar("_T")


def available() -> tuple[bool, str]:
    """이 PC에서 Windows 한국어 글자 인식을 쓸 수 있는지 확인합니다."""
    if os.name != "nt":
        return False, "Windows 에서만 글자 인식이 됩니다."
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
    except (ImportError, OSError) as e:
        return False, f"Windows 글자 인식 기능을 불러오지 못했습니다: {e}"

    try:
        engine = OcrEngine.try_create_from_language(Language("ko"))
    except Exception as e:  # WinRT 초기화 실패도 화면에 이유를 알려 줌
        return False, f"Windows 글자 인식을 시작하지 못했습니다: {e}"
    if engine is None:
        return False, "한국어 글자 인식기가 없습니다. Windows 설정 > 언어에서 한국어를 추가해 주세요."
    return True, ""


def _run_async(factory: Callable[[], object]) -> _T:
    """코루틴을 실행합니다. 이미 event loop가 도는 스레드라면 새 스레드를 씁니다."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())  # type: ignore[arg-type, return-value]

    results: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            results.put((True, asyncio.run(factory())))  # type: ignore[arg-type]
        except BaseException as e:  # 결과를 반드시 부모 스레드로 돌려보냄
            results.put((False, e))

    thread = threading.Thread(target=worker, name="cbmsg-ocr", daemon=True)
    thread.start()
    ok, value = results.get()
    thread.join()
    if not ok:
        raise value  # type: ignore[misc]
    return value  # type: ignore[return-value]


def _resize_for_ocr(path: Path, max_dimension: int) -> tuple[Path, Path | None]:
    """WinRT 제한보다 큰 이미지를 임시 PNG로 줄입니다."""
    if max_dimension <= 0:
        return path, None
    try:
        from PIL import Image, ImageOps
    except ImportError as e:
        raise OcrError("큰 이미지를 읽으려면 Pillow가 필요합니다.") from e

    try:
        with Image.open(path) as opened:
            if max(opened.size) <= max_dimension:
                return path, None
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            if image.mode not in ("1", "L", "LA", "P", "RGB", "RGBA"):
                image = image.convert("RGB")
            handle = tempfile.NamedTemporaryFile(prefix="cbmsg-ocr-", suffix=".png", delete=False)
            temp_path = Path(handle.name)
            handle.close()
            try:
                image.save(temp_path, format="PNG", optimize=True)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
            return temp_path, temp_path
    except OcrError:
        raise
    except Exception as e:
        raise OcrError(f"이미지를 읽지 못했습니다: {e}") from e


async def _recognize_async(path: Path) -> str:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile

    engine = OcrEngine.try_create_from_language(Language("ko"))
    if engine is None:
        raise OcrError("한국어 글자 인식기가 없습니다. Windows 설정 > 언어에서 한국어를 추가해 주세요.")

    try:
        max_dimension = int(getattr(OcrEngine, "max_image_dimension", 0) or 0)
    except (TypeError, ValueError):
        max_dimension = 0
    work_path, temporary = _resize_for_ocr(path, max_dimension)
    stream = None
    bitmap = None
    try:
        storage_file = await StorageFile.get_file_from_path_async(str(work_path))
        stream = await storage_file.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        return "\n".join(str(line.text) for line in result.lines)
    finally:
        if bitmap is not None:
            close = getattr(bitmap, "close", None)
            if callable(close):
                close()
        if stream is not None:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def recognize_file(path) -> str:
    """이미지 파일의 한국어 글자를 줄 단위 문자열로 돌려줍니다."""
    ok, reason = available()
    if not ok:
        raise OcrError(reason)
    image_path = Path(path).expanduser().resolve()
    if not image_path.is_file():
        raise OcrError("글자를 읽을 이미지 파일을 찾지 못했습니다.")
    try:
        return _run_async(lambda: _recognize_async(image_path))
    except OcrError:
        raise
    except Exception as e:
        raise OcrError(f"글자 인식에 실패했습니다: {e}") from e


def _image_suffix(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    return ".img"


def recognize_bytes(data: bytes) -> str:
    """이미지 바이트를 임시 파일로 인식하고, 임시 파일은 항상 지웁니다."""
    if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
        raise OcrError("글자를 읽을 이미지가 비어 있습니다.")
    raw = bytes(data)
    handle = tempfile.NamedTemporaryFile(prefix="cbmsg-ocr-", suffix=_image_suffix(raw), delete=False)
    temp_path = Path(handle.name)
    try:
        handle.write(raw)
        handle.close()
        return recognize_file(temp_path)
    finally:
        if not handle.closed:
            handle.close()
        temp_path.unlink(missing_ok=True)
