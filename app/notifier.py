"""Windows 알림 띄우기. win11toast 가 없으면 콘솔에 출력합니다.

win11toast 의 toast() 는 알림이 닫히거나(사용자가 누르거나 시간이 지나) 실패할 때까지 기다립니다.
쪽지 분석 줄(Runner 의 AI 작업 스레드)에서 그대로 부르면 그동안 다음 쪽지 분석이 멈추므로,
알림은 따로 띄우고(백그라운드 스레드) 바로 돌아옵니다.
"""
from __future__ import annotations

import threading


def _show(title: str, body: str, sound: bool) -> None:
    try:
        from win11toast import toast  # pip install win11toast (Windows 전용)
        kw = {} if sound else {"audio": {"silent": "true"}}
        quiet = lambda *_a, **_k: None   # win11toast 기본값은 알림이 닫힐 때 결과를 print 함 → 조용히
        toast(title, body, app_id="충북메신저 AI 비서",
              on_click=quiet, on_dismissed=quiet, on_failed=quiet, **kw)
    except Exception:
        print(f"\n🔔 {title}\n   {body}\n")


def notify(title: str, body: str, sound: bool = False, wait: bool = False) -> None:
    """알림을 띄우고 바로 돌아옴 (wait=True 면 알림이 닫힐 때까지 기다림 — 시험용)."""
    if wait:
        _show(title, body, sound)
        return
    threading.Thread(target=_show, args=(title, body, sound), name="toast", daemon=True).start()
