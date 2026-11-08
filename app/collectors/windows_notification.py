"""[실험적] Windows 11 알림 센터에서 메신저 알림을 읽는 수집기.

⚠ 아직 실제 충북소통메신저로 검증하지 않았습니다.
   메신저가 Windows 기본 알림(토스트)을 사용해야만 동작합니다.
   메신저가 자체 팝업 창을 띄운다면 이 방식은 안 되고,
   UI 자동화(pywinauto) 수집기를 새로 만들어야 합니다. (HANDOFF.md 참고)

준비:
  1) pip install winsdk
  2) Windows 설정 > 개인 정보 및 보안 > 알림 > "앱이 알림에 액세스하도록 허용" 켜기
  3) python main.py list-apps  로 메신저 앱 표시 이름 확인 후 .env 의 MESSENGER_APP_KEYWORDS 수정
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Iterable

from ..models import Message
from .base import Collector


def _require_winsdk():
    try:
        from winsdk.windows.ui.notifications.management import (  # noqa: F401
            UserNotificationListener, UserNotificationListenerAccessStatus)
        from winsdk.windows.ui.notifications import (  # noqa: F401
            NotificationKinds, KnownNotificationBindings)
    except ImportError as e:
        raise RuntimeError("winsdk 가 필요합니다: pip install winsdk (Windows 전용)") from e


async def _read_notifications() -> list[dict]:
    from winsdk.windows.ui.notifications.management import (
        UserNotificationListener, UserNotificationListenerAccessStatus)
    from winsdk.windows.ui.notifications import NotificationKinds, KnownNotificationBindings

    listener = UserNotificationListener.current
    status = await listener.request_access_async()
    if status != UserNotificationListenerAccessStatus.ALLOWED:
        raise RuntimeError("알림 접근 권한이 없습니다. Windows 설정에서 알림 액세스를 허용하세요.")

    out = []
    for n in await listener.get_notifications_async(NotificationKinds.TOAST):
        try:
            app_name = n.app_info.display_info.display_name if n.app_info else ""
        except Exception:
            app_name = ""
        texts: list[str] = []
        try:
            binding = n.notification.visual.get_binding(KnownNotificationBindings.toast_generic)
            if binding:
                texts = [t.text for t in binding.get_text_elements()]
        except Exception:
            pass
        try:
            created = n.creation_time.astimezone().isoformat(timespec="seconds")  # type: ignore[attr-defined]
        except Exception:
            created = datetime.now().isoformat(timespec="seconds")
        out.append({"id": n.id, "app": app_name, "texts": texts, "created": created})
    return out


class WindowsNotificationCollector(Collector):
    name = "notification"

    def __init__(self, app_keywords: list[str]):
        _require_winsdk()
        self.app_keywords = app_keywords
        self.seen: set[int] = set()

    def _match(self, app_name: str) -> bool:
        return not self.app_keywords or any(k in app_name for k in self.app_keywords)

    def collect_once(self) -> Iterable[Message]:
        for n in asyncio.run(_read_notifications()):
            if n["id"] in self.seen or not self._match(n["app"]):
                continue
            self.seen.add(n["id"])
            texts = n["texts"]
            if not texts:
                continue
            # 보통 첫 줄 = 제목(보낸 사람), 나머지 = 본문. 메신저마다 다를 수 있어 검증 필요.
            sender = texts[0]
            body = "\n".join(texts[1:]) or texts[0]
            yield Message(sender=sender, body=body, received_at=n["created"],
                          source=self.name, external_id=str(n["id"]))


def list_notification_apps() -> list[tuple[str, list[str]]]:
    """현재 알림 센터에 있는 알림의 (앱 이름, 텍스트) 목록. 메신저 이름 확인용."""
    _require_winsdk()
    return [(n["app"], n["texts"]) for n in asyncio.run(_read_notifications())]
