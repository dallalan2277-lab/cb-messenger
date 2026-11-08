"""Windows 알림 띄우기. win11toast 가 없으면 콘솔에 출력합니다."""
from __future__ import annotations


def notify(title: str, body: str, sound: bool = False) -> None:
    try:
        from win11toast import toast  # pip install win11toast (Windows 전용)
        kw = {} if sound else {"audio": {"silent": "true"}}
        quiet = lambda *_a, **_k: None   # win11toast 기본값은 알림이 닫힐 때 결과를 print 함 → 조용히
        toast(title, body, app_id="충북메신저 AI 비서",
              on_click=quiet, on_dismissed=quiet, on_failed=quiet, **kw)
    except Exception:
        print(f"\n🔔 {title}\n   {body}\n")
