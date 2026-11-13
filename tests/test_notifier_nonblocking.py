"""알림이 닫힐 때까지 분석 줄이 멈추지 않는지 (가짜 win11toast)."""
import sys
import threading
import time
import types

import app.notifier as notifier


def test_notify_returns_immediately_even_if_toast_blocks(monkeypatch):
    gate, called = threading.Event(), []
    fake = types.SimpleNamespace(toast=lambda *a, **k: (called.append(a[0]), gate.wait(3)))
    monkeypatch.setitem(sys.modules, "win11toast", fake)
    t0 = time.time()
    notifier.notify("가상 제목", "가상 내용")
    assert time.time() - t0 < 0.5               # 알림이 닫히기를 기다리지 않음
    for _ in range(50):
        if called:
            break
        time.sleep(0.02)
    assert called == ["가상 제목"]
    gate.set()


def test_notify_wait_true_blocks_until_closed(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "win11toast", types.SimpleNamespace(toast=lambda *a, **k: calls.append(1)))
    notifier.notify("가상", "내용", wait=True)
    assert calls == [1]
