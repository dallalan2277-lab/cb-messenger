"""충북소통메신저 AI 비서 - 명령줄 실행 파일.

사용 예:
  python main.py analyze "내일까지 학교폭력 실태조사 결과 제출 바랍니다" --sender 교무부장
  python main.py test                        # samples/sample_messages.json 으로 테스트
  python main.py digest                      # 오늘 받은 메시지 브리핑
  python main.py export-ics                  # 추출된 일정을 .ics 로 내보내기
  python main.py gui                         # 테스트 화면
  python main.py messenger-check            # [Windows] 열린 쪽지 창이 제대로 읽히는지 확인
  python main.py mail-login                  # 네이버 메일 연결 (앱 비밀번호 → Windows 자격 증명 관리자)
  python main.py mail-check                  # 메일 지금 한 번 확인·분석
  python main.py watch                       # [Windows] 쪽지 감시 + 메일 확인 + 원문 보관
  python main.py archive-sync                # 드라이브에 못 올린 기록 옮기기
  python main.py calendar --watch            # [Windows] 바탕화면 캘린더 + 메신저 감시
  python main.py startup                     # [Windows] 로그인할 때 자동으로 켜기 (--off 로 해제)
  python main.py chatgpt-login               # ChatGPT 구독(Plus/Pro)으로 로그인
  python main.py chatgpt-models              # 구독으로 쓸 수 있는 모델 목록
  python main.py chatgpt-logout
  python main.py models                      # 용도별 모델 설정 보기
  python main.py usage                       # 이번 달 모델별 토큰 사용량
  (명령 앞에 --provider fake 처럼 쓰면 이번 실행만 모델을 바꿈. 예: --provider claude_cli:haiku)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from app.collectors import JsonFileCollector, message_from_text
from app.config import get_settings
from app.extensions.ics_export import export_ics
from app.llm.factory import PROVIDERS, PROVIDER_LABELS, parse_spec
from app.paths import DATA_DIR, FROZEN, RESOURCE_DIR, ROOT, is_gui_exe, self_command
from app.pipeline import build_pipeline
from app.storage import Storage

ICON = {"긴급": "🔴", "중요": "🟡", "참고": "⚪"}


def build(provider: str | None):
    """--provider 를 주면 분석·브리핑 모델을 모두 그것으로 덮어씁니다 (예: fake, claude_cli:haiku)."""
    s = get_settings()
    if provider:
        s.analyze_model = s.digest_model = provider
    storage = Storage(s.db_path)
    return s, storage, build_pipeline(s, storage)


def print_analysis(m, a):
    src = "✉ 메일" if m.source == "mail" else "💬 쪽지" if m.source == "eztalk" else "📝"
    print(f"{ICON.get(a.importance, '')} [{a.importance}/{a.category}/{a.school_relevance}] {src} {m.sender}  ({m.received_at})  · {a.provider}:{a.model}")
    if m.subject:
        print(f"   제목: {m.subject}")
    print(f"   요약: {a.summary}")
    for act in a.action_items:
        print(f"   - 할 일: {act}")
    if a.evidence:
        print(f"   근거: \"{a.evidence[:120]}\"")
    if not a.verified:
        print("   (AI 답 일부를 원문 근거가 없어 고쳤습니다)")
    if a.deadline:
        print(f"   ⏰ 마감: {a.deadline}")
    for e in a.events:
        print(f"   📅 {e.date} {e.start_time} {e.title} {('@ ' + e.location) if e.location else ''}")
    print()


def cmd_analyze(args):
    _, _, pipe = build(args.provider)
    m = message_from_text(args.text, sender=args.sender, room=args.room)
    a = pipe.handle(m)
    if a:
        print_analysis(m, a)
    else:
        print("분석하지 않았습니다 (중복·필터·오류). 로그를 확인하세요.")


def cmd_test(args):
    _, _, pipe = build(args.provider)
    for m in JsonFileCollector(args.file).collect_once():
        a = pipe.handle(m)
        if a:
            print_analysis(m, a)


def cmd_digest(args):
    s, storage, pipe = build(args.provider)
    since = args.since or datetime.now().strftime("%Y-%m-%d")
    items = storage.recent(since=since)
    d = pipe.analyzer.digest(items)
    print(f"📋 {d.get('headline', '')}\n")
    for title, key in (("🔴 긴급", "urgent"), ("🟡 이번 주", "this_week"), ("⚪ 참고", "fyi")):
        if d.get(key):
            print(title)
            for x in d[key]:
                print(f"  - {x}")
            print()


def cmd_export_ics(args):
    s, storage, _ = build("fake")  # AI 호출 불필요 (가짜 제공자로 저장소만 사용)
    events = [e for _, a in storage.recent(since=args.since, limit=1000) for e in a.events]
    if not events:
        print("내보낼 일정이 없습니다.")
        return
    p = export_ics(events, args.out)
    print(f"{len(events)}개 일정을 {p} 로 저장했습니다. 캘린더 앱에서 '가져오기' 하세요.")


def cmd_retry(args):
    _, _, pipe = build(args.provider)
    print(f"{pipe.retry_failed()}건 재분석 완료")


def cmd_list_apps(args):
    from app.collectors.windows_notification import list_notification_apps
    rows = list_notification_apps()
    if not rows:
        print("알림 센터에 알림이 없습니다. 메신저로 테스트 메시지를 받은 뒤 다시 실행하세요.")
    for app_name, texts in rows:
        print(f"[{app_name}] {' | '.join(texts)[:100]}")


def make_collector(s):
    """(이전 버전 호환) 쪽지 수집기"""
    from app.runner import make_messenger_collector
    return make_messenger_collector(s)


def _archive_status(pipe):
    a = pipe.archiver
    if not a or not a.enabled:
        return "원문 보관: 꺼짐"
    parts = []
    if a.local_enabled:
        parts.append(f"이 PC({a.local_dir})")
    if a.gdrive_enabled:
        parts.append(f"구글 드라이브({a.gdrive_dir or '폴더 못 찾음 → 이 PC에 임시 보관'})")
    return "원문 보관: " + ", ".join(parts)


def cmd_watch(args):
    """쪽지 감시 + 네이버 메일 확인 + AI 분석 (창 없이, 이 검은 창에 결과 표시)"""
    import time as _t
    from app.runner import Runner
    s, storage, pipe = build(args.provider)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    seen = set()

    def on_change():
        for m, a in storage.recent(limit=5):
            if m.id not in seen:
                seen.add(m.id)
                print_analysis(m, a)
    for m, _a in storage.recent(limit=200):
        seen.add(m.id)
    r = Runner(s, on_change=on_change, watch_messenger=not args.no_messenger, watch_mail=not args.no_mail,
               pipeline=pipe).start()
    print(f"감시 시작 · 분석 모델: {s.analyze_model}")
    print(_archive_status(pipe))
    print("쪽지 창·메일함을 읽기만 하고 클릭·입력·삭제는 하지 않습니다. Ctrl+C 로 종료\n")
    try:
        last = ""
        while True:
            _t.sleep(5)
            st = r.status
            line = f"쪽지: {st['messenger']}  |  메일: {st['mail']} (마지막 {st['mail_last_check'] or '-'})"
            errs = " / ".join(x for x in (st["messenger_error"], st["mail_error"], st["ai_last_error"]) if x)
            if line + errs != last:
                print(line + (f"\n  ⚠ {errs}" if errs else ""))
                last = line + errs
    except KeyboardInterrupt:
        r.stop()
        print("종료합니다.")


def cmd_mail_login(args):
    """네이버 메일 연결: 앱 비밀번호 확인 후 Windows 자격 증명 관리자에 저장"""
    import getpass
    from app.collectors.naver_mail import MailError, check_account, verify_login
    from app.config import save_env
    from app.secrets_store import SecretStoreError, save_password
    try:
        account = check_account(args.account or input("네이버 메일 주소 (...@naver.com): "))
        pw = getpass.getpass("애플리케이션 비밀번호 (입력해도 화면에 안 보임): ").strip()
        print("네이버에 연결해 확인하는 중…")
        verify_login(account, pw)
        save_password(account, pw)
    except (MailError, SecretStoreError) as e:
        print("실패:", e)
        return
    save_env({"MAIL_ENABLED": "true", "MAIL_ACCOUNT": account})
    print(f"연결했습니다: {account}")
    print("비밀번호는 Windows 자격 증명 관리자에 저장했습니다 (파일에는 저장하지 않음).")
    print("오늘(한국 시각 0시) 이후 도착한 메일부터 가져옵니다. 받은편지함은 읽기 전용으로만 엽니다.")


def cmd_mail_logout(args):
    from app.config import save_env
    from app.secrets_store import delete_password
    s = get_settings()
    if s.mail_account:
        delete_password(s.mail_account)
    save_env({"MAIL_ENABLED": "false"})
    print("메일 연결을 끊고 저장된 비밀번호를 지웠습니다.")


def cmd_mail_check(args):
    """메일을 지금 한 번 확인해 분석 (결과 표시)"""
    from app.runner import make_mail_collector
    s, storage, pipe = build(args.provider)
    col, why = make_mail_collector(s, storage, on_error=lambda m: print("⚠", m))
    if not col:
        print(why)
        return
    got = list(col.collect_once())
    print(f"새 메일 {len(got)}통")
    for m in got:
        a = pipe.handle(m)
        if a:
            print_analysis(m, a)


def cmd_messenger_check(args):
    """지금 떠 있는 소통메신저 쪽지 창을 한 번 읽어 제대로 뽑히는지 확인 (기록·AI 호출 없음)"""
    from app.collectors.eztalk_collector import EztalkCollector, available
    ok, why = available()
    if not ok:
        print(why)
        return
    import uiautomation as auto
    s = get_settings()
    with auto.UIAutomationInitializerInThread():
        col = EztalkCollector(process=s.messenger_process)
        rows = col.snapshot()
        print(f"메신저 창 종류 발견: {sorted(col.stats['classes']) or '없음 (메신저가 켜져 있나요?)'}")
        if not rows:
            print("열려 있는 쪽지 알림/보기 창이 없습니다. 쪽지를 하나 연 상태로 다시 실행하세요.")
        for cls, texts, ev in rows:
            print(f"\n[{cls}] 읽기 {'성공' if ev['ok'] else '실패'}{' (보안 쪽지: ' + ev['why'] + ')' if ev['secure'] else ''}")
            print(f"  보낸 사람: {ev['sender']}\n  제목: {ev.get('title', '')}\n  시각: {ev['time']}")
            print(f"  본문: {ev['body'][:200]}")
            if args.raw:
                print("  ── 창에서 읽은 글 (화면에만 표시, 저장 안 함) ──")
                for t in texts[:60]:
                    print(f"   · {t[:100]}")


def cmd_archive_sync(args):
    s, _, pipe = build("fake")
    print(_archive_status(pipe))
    n = pipe.archiver.sync_pending() if pipe.archiver else 0
    print(f"드라이브 대기 파일 {n}개를 옮겼습니다." if n else "옮길 대기 파일이 없습니다.")
    if pipe.archiver and pipe.archiver.status["last_error"]:
        print("알림:", pipe.archiver.status["last_error"])


def cmd_chatgpt_login(args):
    from app.llm.chatgpt_auth import ChatGPTSession, MANAGE_USAGE_URL
    print("브라우저에서 ChatGPT 계정으로 로그인하고 권한을 허용하세요...")
    rec = ChatGPTSession().login()
    print(f"로그인 완료: {rec.get('email')}  (ChatGPT 플랜 사용 중)")
    print(f"앱 사용량 관리: {MANAGE_USAGE_URL}")
    print(".env 에서 AI_PROVIDER=chatgpt_plan 으로 설정하면 구독으로 요약합니다.")


def cmd_chatgpt_logout(args):
    from app.llm.chatgpt_auth import ChatGPTSession
    ChatGPTSession().logout()
    print("로그아웃했습니다.")


def cmd_chatgpt_models(args):
    from app.llm.chatgpt_auth import ChatGPTSession
    from app.llm.chatgpt_plan_provider import list_models
    for m in list_models(ChatGPTSession()):
        print(f"{m['slug']:30} {m['display_name']}")
    print("\n.env 의 CHATGPT_PLAN_MODEL 에 slug 를 넣으면 그 모델을 씁니다 (비우면 맨 위 모델).")


def _slot_text(spec: str) -> str:
    if not spec:
        return "(없음)"
    p, m = parse_spec(spec)
    return f"{PROVIDER_LABELS.get(p, p)} · {m or '자동/기본 모델'}"


def cmd_update_check(args):
    from app import __version__
    from app import updater
    s = get_settings()
    print(f"현재 버전: v{__version__}  (확인할 곳: github.com/{s.update_repo})")
    try:
        info = updater.check_latest(s.update_repo, __version__)
    except updater.UpdateError as e:
        print(f"확인 실패: {e}")
        return
    if not info:
        print("최신 버전입니다.")
        return
    print(f"새 버전 v{info.version} 이 있습니다: {info.html_url}")
    print("캘린더 ⚙ 메뉴의 '새 버전 설치하기'로 설치할 수 있습니다.")


def cmd_models(args):
    s = get_settings()
    print("현재 모델 설정 (.env 또는 테스트 화면 [모델 설정]에서 변경)")
    print(f"  분석 모델 (메시지마다) : {_slot_text(s.analyze_model)}")
    print(f"  브리핑 모델 (하루 정리) : {_slot_text(s.digest_model)}")
    print(f"  대체 모델 (실패 시)     : {_slot_text(s.fallback_model)}")
    print(f"  사고 수준               : {s.reasoning}")
    print(f"  AI 생략 규칙            : {s.skip_ai_min_chars}자 미만 / 키워드 {s.skip_ai_keywords or '없음'}")


def cmd_usage(args):
    s = get_settings()
    storage = Storage(s.db_path)
    since = args.since or datetime.now().strftime("%Y-%m-01")
    rows = storage.usage_summary(since)
    print(f"📊 {since} 이후 AI 사용량")
    if not rows:
        print("  기록이 없습니다.")
        return
    print(f"  {'용도':8}{'모델':40}{'호출':>6}{'입력토큰':>10}{'출력토큰':>10}")
    for r in rows:
        name = f"{PROVIDER_LABELS.get(r['provider'], r['provider'])} · {r['model']}"
        print(f"  {('분석' if r['purpose']=='analyze' else '브리핑'):8}{name[:38]:40}{r['calls']:>6}{r['inp'] or 0:>10,}{r['outp'] or 0:>10,}")
    print("  ※ 구독(ChatGPT·Claude)은 토큰만큼 돈이 나가지 않고 플랜 사용 한도에서 차감됩니다.")


def cmd_calendar(args):
    """바탕화면 캘린더 (Windows). --watch 를 붙이면 소통메신저 감시도 같이"""
    from app.desktop_calendar.widget import run
    s = get_settings()
    if args.provider:
        s.analyze_model = s.digest_model = args.provider
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_file = DATA_DIR / "calendar.log"
    logging.getLogger().addHandler(logging.FileHandler(log_file, encoding="utf-8"))
    try:
        run(s, with_watch=args.watch)
    except BaseException as e:  # 창 없이(pythonw) 실행될 때도 오류가 보이도록
        if isinstance(e, KeyboardInterrupt):
            return
        logging.exception("바탕화면 캘린더 오류")
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"바탕화면 캘린더를 열지 못했습니다.\n\n{e}\n\n자세한 내용: {log_file}",
                                             "충북메신저 AI 캘린더", 0x10)
        raise


def cmd_startup(args):
    """Windows 시작할 때 캘린더(+메신저 감시)가 자동으로 켜지게 / 끄게"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        print("Windows 에서만 쓸 수 있습니다.")
        return
    link = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "충북메신저AI_캘린더.vbs"
    if args.off:
        if link.exists():
            link.unlink()
        print("자동 실행을 껐습니다.")
        return
    command = self_command("calendar", *([] if args.no_watch else ["--watch"]))
    # Quote command arguments for Windows, then escape quotes for the VBS string.
    command_line = " ".join(f'"{arg}"' for arg in command).replace('"', '""')
    current_directory = str(ROOT).replace('"', '""')
    vbs = (f'Set sh = CreateObject("WScript.Shell")\r\n'
           f'sh.CurrentDirectory = "{current_directory}"\r\n'
           f'sh.Run "{command_line}", 0, False\r\n')
    link.write_text(vbs, encoding="utf-16")
    print(f"Windows 시작 시 자동 실행을 켰습니다: {link}")
    print("끄려면: python main.py startup --off")


def cmd_settings(args):
    from app.desktop_calendar.settings_window import run_standalone
    run_standalone(get_settings())


def cmd_gui(args):
    from gui import run_gui
    run_gui(args.provider)


def main(argv=None):
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # 한글·이모지 깨짐 방지
        except Exception:
            pass
    if sys.stdout is None:   # pythonw(창 없음)로 실행된 경우
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="충북소통메신저 AI 비서")
    p.add_argument("--provider", help="이번 실행만 모델 지정 (예: fake, chatgpt_plan, claude_cli:haiku, openai:gpt-5-mini)")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="메시지 1건 분석")
    a.add_argument("text")
    a.add_argument("--sender", default="(직접 입력)")
    a.add_argument("--room", default="")
    a.set_defaults(func=cmd_analyze)

    t = sub.add_parser("test", help="샘플 파일로 테스트")
    t.add_argument("--file", default=str(RESOURCE_DIR / "samples" / "sample_messages.json"))
    t.set_defaults(func=cmd_test)

    d = sub.add_parser("digest", help="브리핑 만들기")
    d.add_argument("--since", help="YYYY-MM-DD (기본: 오늘)")
    d.set_defaults(func=cmd_digest)

    e = sub.add_parser("export-ics", help="일정을 .ics 로 내보내기")
    e.add_argument("--since")
    e.add_argument("--out", default=str(DATA_DIR / "events.ics"))
    e.set_defaults(func=cmd_export_ics)

    sub.add_parser("retry", help="분석 실패한 메시지 재분석").set_defaults(func=cmd_retry)
    sub.add_parser("list-apps", help="[Windows] 알림 앱 이름 확인").set_defaults(func=cmd_list_apps)
    w = sub.add_parser("watch", help="[Windows] 쪽지 감시 + 메일 확인")
    w.add_argument("--no-messenger", action="store_true", help="쪽지 감시 끄기")
    w.add_argument("--no-mail", action="store_true", help="메일 확인 끄기")
    w.set_defaults(func=cmd_watch)
    ml = sub.add_parser("mail-login", help="네이버 메일 연결 (앱 비밀번호 저장)")
    ml.add_argument("account", nargs="?", help="...@naver.com")
    ml.set_defaults(func=cmd_mail_login)
    sub.add_parser("mail-logout", help="네이버 메일 연결 끊기").set_defaults(func=cmd_mail_logout)
    sub.add_parser("mail-check", help="메일 지금 한 번 확인").set_defaults(func=cmd_mail_check)
    mc = sub.add_parser("messenger-check", help="[Windows] 열린 쪽지 창 읽기 확인")
    mc.add_argument("--raw", action="store_true", help="창에서 읽은 글 전체를 화면에 표시")
    mc.set_defaults(func=cmd_messenger_check)
    c = sub.add_parser("calendar", help="[Windows] 바탕화면 캘린더")
    c.add_argument("--watch", action="store_true", help="소통메신저 감시도 같이 실행")
    c.set_defaults(func=cmd_calendar)
    st = sub.add_parser("startup", help="[Windows] 시작 프로그램 등록/해제")
    st.add_argument("--off", action="store_true")
    st.add_argument("--no-watch", action="store_true", help="캘린더만 (메신저 감시 없이)")
    st.set_defaults(func=cmd_startup)
    sub.add_parser("archive-sync", help="드라이브에 못 올린 기록 옮기기").set_defaults(func=cmd_archive_sync)
    sub.add_parser("gui", help="테스트 화면").set_defaults(func=cmd_gui)
    sub.add_parser("settings", help="[Windows] 설정 창 (AI 연결·모델·저장·메일·알림·학교 정보)").set_defaults(func=cmd_settings)
    sub.add_parser("models", help="현재 모델 설정 보기").set_defaults(func=cmd_models)
    u = sub.add_parser("usage", help="AI 사용량(토큰) 보기")
    u.add_argument("--since", help="YYYY-MM-DD (기본: 이번 달 1일)")
    u.set_defaults(func=cmd_usage)
    sub.add_parser("chatgpt-login", help="ChatGPT 구독으로 로그인").set_defaults(func=cmd_chatgpt_login)
    sub.add_parser("chatgpt-logout", help="ChatGPT 로그아웃").set_defaults(func=cmd_chatgpt_logout)
    sub.add_parser("chatgpt-models", help="구독으로 쓸 수 있는 모델").set_defaults(func=cmd_chatgpt_models)
    sub.add_parser("update-check", help="GitHub 에 새 버전이 있는지 확인").set_defaults(func=cmd_update_check)

    # 설치판 CBMessengerAI.exe 를 더블클릭(인자 없음) → 캘린더 + 감시. cbm.exe(콘솔)는 도움말
    if FROZEN and is_gui_exe() and not (sys.argv[1:] if argv is None else argv):
        argv = ["calendar", "--watch"]
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
