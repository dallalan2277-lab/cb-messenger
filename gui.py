"""테스트 화면 (tkinter, 추가 설치 불필요).

메시지를 붙여넣고 [분석]을 누르면 중요도·요약·할 일·일정을 보여줍니다.
[모델 설정]에서 용도별 모델(분석·브리핑·대체), 사고 수준, AI 생략 규칙을 바꿀 수 있습니다.
"""
from __future__ import annotations

import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox, filedialog

from app.collectors import message_from_text
from app.config import get_settings, save_env, REASONING_LEVELS
from app.extensions.ics_export import export_ics
from app.llm.factory import PROVIDERS, PROVIDER_LABELS, parse_spec
from app.pipeline import build_pipeline
from app.storage import Storage

COLORS = {"긴급": "#d93025", "중요": "#e37400", "참고": "#5f6368"}
FONT = ("맑은 고딕", 10)
LABEL_TO_PROVIDER = {v: k for k, v in PROVIDER_LABELS.items()}
REASONING_LABELS = {"default": "기본값", "low": "낮음", "medium": "중간", "high": "높음"}
AUTO = "자동/기본 모델"

# 제공자별 모델 예시 (직접 입력도 가능, ChatGPT 구독은 [목록 불러오기]로 실제 목록 확인)
MODEL_HINTS = {
    "chatgpt_plan": [AUTO],
    "claude_cli": [AUTO, "haiku", "sonnet", "opus"],
    "openai": [AUTO, "gpt-5-mini", "gpt-5"],
    "claude": [AUTO, "claude-haiku-5-5", "claude-sonnet-5-5"],
    "fake": [AUTO],
}


def slot_text(spec: str) -> str:
    if not spec:
        return "없음"
    p, m = parse_spec(spec)
    return f"{PROVIDER_LABELS.get(p, p)} · {m or AUTO}"


class ModelSlot(ttk.Frame):
    """제공자 + 모델 선택 한 줄."""

    def __init__(self, master, title: str, desc: str, spec: str, allow_none: bool = False):
        super().__init__(master, padding=(0, 6))
        self.allow_none = allow_none
        left = ttk.Frame(self)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text=title, font=("맑은 고딕", 10, "bold")).pack(anchor="w")
        ttk.Label(left, text=desc, foreground="#5f6368", wraplength=300).pack(anchor="w")

        p, m = parse_spec(spec)
        choices = (["없음"] if allow_none else []) + [PROVIDER_LABELS[x] for x in PROVIDERS]
        self.p_var = tk.StringVar(value=PROVIDER_LABELS.get(p, "없음") if spec else "없음")
        self.m_var = tk.StringVar(value=m or AUTO)
        right = ttk.Frame(self)
        right.pack(side="right")
        cb = ttk.Combobox(right, textvariable=self.p_var, values=choices, width=15, state="readonly")
        cb.pack(side="left", padx=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_models())
        self.m_box = ttk.Combobox(right, textvariable=self.m_var, width=22)
        self.m_box.pack(side="left", padx=2)
        self.list_btn = ttk.Button(right, text="목록 불러오기", command=self._load_chatgpt_models)
        self._refresh_models(keep=True)

    def _provider(self) -> str:
        return LABEL_TO_PROVIDER.get(self.p_var.get(), "")

    def _refresh_models(self, keep: bool = False):
        p = self._provider()
        self.m_box.configure(values=MODEL_HINTS.get(p, [AUTO]), state="normal" if p else "disabled")
        if not keep:
            self.m_var.set(AUTO)
        if p == "chatgpt_plan":
            self.list_btn.pack(side="left", padx=2)
        else:
            self.list_btn.pack_forget()

    def _load_chatgpt_models(self):
        try:
            from app.llm.chatgpt_auth import ChatGPTSession
            from app.llm.chatgpt_plan_provider import list_models
            models = list_models(ChatGPTSession())
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("모델 목록", f"불러오지 못했습니다. 먼저 ChatGPT 로그인을 하세요.\n\n{e}")
            return
        self.m_box.configure(values=[AUTO] + [m["slug"] for m in models])
        messagebox.showinfo("모델 목록", "이 계정으로 쓸 수 있는 모델:\n\n" +
                            "\n".join(f"{m['slug']}  ({m['display_name']})" for m in models))

    def spec(self) -> str:
        p = self._provider()
        if not p:
            return ""
        m = self.m_var.get().strip()
        return p if (not m or m == AUTO) else f"{p}:{m}"


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, on_saved):
        super().__init__(master)
        self.title("모델 설정")
        self.geometry("720x560")
        self.on_saved = on_saved
        s = get_settings(reload=True)
        self.geometry("760x660")
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        body = ttk.Frame(nb, padding=14)
        nb.add(body, text="  모델  ")
        store = ttk.Frame(nb, padding=14)
        nb.add(store, text="  저장  ")
        self._build_storage(store, s)
        mailf = ttk.Frame(nb, padding=14)
        nb.add(mailf, text="  메일  ")
        self._build_mail(mailf, s)
        notif = ttk.Frame(nb, padding=14)
        nb.add(notif, text="  알림  ")
        self._build_notify(notif, s)
        school = ttk.Frame(nb, padding=14)
        nb.add(school, text="  학교 정보  ")
        self._build_school(school, s)

        ttk.Label(body, text="용도별 모델", font=("맑은 고딕", 12, "bold")).pack(anchor="w")
        ttk.Label(body, foreground="#5f6368",
                  text="구독(ChatGPT·Claude)은 플랜 사용 한도에서, API 키는 사용한 토큰만큼 결제됩니다.").pack(anchor="w")
        self.analyze = ModelSlot(body, "분석 모델", "메시지가 올 때마다 분류·요약합니다. 호출이 가장 많으니 가벼운 모델을 권장합니다.",
                                 s.analyze_model)
        self.digest = ModelSlot(body, "브리핑 모델", "하루 정리용입니다. 호출이 적어 좋은 모델을 써도 부담이 작습니다.",
                                s.digest_model)
        self.fallback = ModelSlot(body, "대체 모델", "위 모델이 한도 초과·로그인 만료 등으로 실패하면 대신 사용합니다.",
                                  s.fallback_model, allow_none=True)
        for w in (self.analyze, self.digest, self.fallback):
            w.pack(fill="x")
            ttk.Separator(body).pack(fill="x")

        row = ttk.Frame(body, padding=(0, 8))
        row.pack(fill="x")
        ttk.Label(row, text="사고 수준", font=("맑은 고딕", 10, "bold")).pack(side="left")
        ttk.Label(row, text="  (GPT 계열에만 적용, 낮을수록 빠르고 토큰 절약)", foreground="#5f6368").pack(side="left")
        self.reasoning = tk.StringVar(value=s.reasoning if s.reasoning in REASONING_LEVELS else "default")
        for lv in REASONING_LEVELS:
            ttk.Radiobutton(row, text=REASONING_LABELS[lv], value=lv, variable=self.reasoning).pack(side="right")
        ttk.Separator(body).pack(fill="x")

        ttk.Label(body, text="토큰 절약: AI 생략 규칙", font=("맑은 고딕", 10, "bold")).pack(anchor="w", pady=(8, 0))
        ttk.Label(body, text="해당 메시지는 AI를 부르지 않고 '참고'로 저장합니다.", foreground="#5f6368").pack(anchor="w")
        r1 = ttk.Frame(body)
        r1.pack(fill="x", pady=2)
        ttk.Label(r1, text="생략 키워드 (쉼표로 구분):", width=24).pack(side="left")
        self.skip_kw = tk.StringVar(value=",".join(s.skip_ai_keywords))
        ttk.Entry(r1, textvariable=self.skip_kw).pack(side="left", fill="x", expand=True)
        r2 = ttk.Frame(body)
        r2.pack(fill="x", pady=2)
        ttk.Label(r2, text="이 글자 수보다 짧으면 생략:", width=24).pack(side="left")
        self.skip_len = tk.StringVar(value=str(s.skip_ai_min_chars))
        ttk.Spinbox(r2, from_=0, to=200, textvariable=self.skip_len, width=6).pack(side="left")
        ttk.Label(r2, text=" (0 = 사용 안 함)", foreground="#5f6368").pack(side="left")

        btns = ttk.Frame(self, padding=(14, 0, 14, 12))
        btns.pack(fill="x", side="bottom")
        ttk.Button(btns, text="저장", command=self.save).pack(side="right")
        ttk.Button(btns, text="취소", command=self.destroy).pack(side="right", padx=6)

    # ---------- 저장 탭 ----------
    def _folder_row(self, parent, var: tk.StringVar, hint: str):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=(24, 0), pady=2)
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="찾아보기", command=lambda: var.set(filedialog.askdirectory() or var.get())).pack(side="left", padx=4)
        ttk.Label(parent, text=hint, foreground="#5f6368", wraplength=640).pack(anchor="w", padx=(24, 0))

    def _build_storage(self, f, s):
        from app.extensions.archive import find_gdrive_root, DRIVE_SUBDIR
        ttk.Label(f, text="쪽지 원문·요약 보관", font=("맑은 고딕", 12, "bold")).pack(anchor="w")
        ttk.Label(f, foreground="#5f6368", wraplength=680,
                  text="메신저에서 읽은 쪽지를 월별 파일(쪽지원문_2026-11.txt, 쪽지요약_2026-11.txt)에 덧붙여 남깁니다. "
                       "보안 쪽지는 기록하지 않습니다. 두 곳을 따로 켜고 끌 수 있습니다.").pack(anchor="w", pady=(0, 8))

        self.local_on = tk.BooleanVar(value=s.archive_local_enabled)
        ttk.Checkbutton(f, text="이 PC에 저장", variable=self.local_on).pack(anchor="w")
        self.local_dir = tk.StringVar(value=s.archive_local_dir)
        self._folder_row(f, self.local_dir, "원문을 그대로 저장합니다 (개인정보 포함). 이 PC에서만 볼 수 있습니다.")
        ttk.Separator(f).pack(fill="x", pady=10)

        self.gdrive_on = tk.BooleanVar(value=s.archive_gdrive_enabled)
        ttk.Checkbutton(f, text="구글 드라이브에 저장 (Google Drive 데스크톱 동기화 폴더)", variable=self.gdrive_on).pack(anchor="w")
        root = find_gdrive_root()
        self.gdrive_dir = tk.StringVar(value=s.archive_gdrive_dir)
        self._folder_row(f, self.gdrive_dir,
                         ("비워 두면 자동: " + str(Path(root) / DRIVE_SUBDIR)) if root else
                         "드라이브 폴더를 자동으로 찾지 못했습니다. Google Drive 데스크톱을 설치하거나 폴더를 직접 고르세요.")
        self.gdrive_redact = tk.BooleanVar(value=s.archive_gdrive_redact)
        ttk.Checkbutton(f, text="드라이브에는 개인정보를 가리고 저장 (전화번호·주민번호·등록한 이름 등) — 권장",
                        variable=self.gdrive_redact).pack(anchor="w", padx=(24, 0), pady=(4, 0))
        ttk.Label(f, foreground="#5f6368", wraplength=640,
                  text="드라이브에 못 쓰면(동기화 꺼짐 등) 이 PC의 '드라이브_대기' 폴더에 임시로 쌓았다가 다음 감시 시작 때 옮깁니다.").pack(anchor="w", padx=(24, 0))
        ttk.Separator(f).pack(fill="x", pady=10)

        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="파일 형식:").pack(side="left")
        self.fmt = tk.StringVar(value=s.archive_format if s.archive_format in ("txt", "md") else "txt")
        ttk.Radiobutton(row, text="txt (메모장)", value="txt", variable=self.fmt).pack(side="left", padx=6)
        ttk.Radiobutton(row, text="md (마크다운)", value="md", variable=self.fmt).pack(side="left")
        self.summary_on = tk.BooleanVar(value=s.archive_include_summary)
        ttk.Checkbutton(f, text="AI 요약도 함께 저장 (쪽지요약_YYYY-MM 파일)", variable=self.summary_on).pack(anchor="w", pady=(6, 0))

    # ---------- 메일 탭 ----------
    def _build_mail(self, f, s):
        from app.secrets_store import load_password
        ttk.Label(f, text="네이버 메일", font=("맑은 고딕", 12, "bold")).pack(anchor="w")
        ttk.Label(f, foreground="#5f6368", wraplength=680, justify="left",
                  text="받은편지함을 읽기 전용으로만 확인합니다 (읽음 표시·삭제·보내기 없음). 처음 연결한 날 0시 이후 도착한 메일부터 가져옵니다.\n"
                       "준비: 네이버 메일 환경설정 → POP3/IMAP 설정 → IMAP/SMTP 사용함 저장 → 2단계 인증 후 '애플리케이션 비밀번호' 발급").pack(anchor="w", pady=(0, 10))
        self.mail_on = tk.BooleanVar(value=s.mail_enabled)
        ttk.Checkbutton(f, text="메일 확인 켜기", variable=self.mail_on).pack(anchor="w")
        g = ttk.Frame(f)
        g.pack(fill="x", pady=6)
        ttk.Label(g, text="네이버 메일 주소", width=18).grid(row=0, column=0, sticky="w", pady=3)
        self.mail_acc = tk.StringVar(value=s.mail_account)
        ttk.Entry(g, textvariable=self.mail_acc, width=34).grid(row=0, column=1, sticky="w")
        ttk.Label(g, text="애플리케이션 비밀번호", width=18).grid(row=1, column=0, sticky="w", pady=3)
        self.mail_pw = tk.StringVar()
        ttk.Entry(g, textvariable=self.mail_pw, width=34, show="●").grid(row=1, column=1, sticky="w")
        saved = bool(s.mail_account and load_password(s.mail_account))
        self.pw_hint = ttk.Label(g, foreground="#188038" if saved else "#5f6368",
                                 text="저장됨 (바꿀 때만 입력)" if saved else "아직 저장된 비밀번호 없음")
        self.pw_hint.grid(row=1, column=2, sticky="w", padx=8)
        ttk.Label(g, text="확인 간격", width=18).grid(row=2, column=0, sticky="w", pady=3)
        self.mail_iv = tk.StringVar(value=f"{s.mail_interval_min:g}")
        ttk.Combobox(g, textvariable=self.mail_iv, values=["1", "2", "5", "10"], width=6, state="readonly").grid(row=2, column=1, sticky="w")
        ttk.Label(g, text="분").grid(row=2, column=1, sticky="w", padx=(70, 0))
        row = ttk.Frame(f)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="연결 확인 후 비밀번호 저장", command=self._mail_connect).pack(side="left")
        ttk.Button(row, text="연결 끊기 (비밀번호 지우기)", command=self._mail_disconnect).pack(side="left", padx=6)
        ttk.Label(f, foreground="#5f6368", wraplength=680, justify="left",
                  text="비밀번호는 파일에 쓰지 않고 Windows 자격 증명 관리자에만 저장합니다 (제어판 > 자격 증명 관리자에서 확인·삭제 가능). "
                       "학교 업무망 PC 에서는 메일 서버 접속이 막혀 있을 수 있습니다.").pack(anchor="w", pady=(6, 0))

    def _mail_connect(self):
        from app.collectors.naver_mail import MailError, check_account, verify_login
        from app.secrets_store import SecretStoreError, save_password
        try:
            acc = check_account(self.mail_acc.get())
            pw = self.mail_pw.get().strip()
            if not pw:
                messagebox.showinfo("메일", "애플리케이션 비밀번호를 입력하세요.")
                return
            self.config(cursor="watch"); self.update()
            verify_login(acc, pw)
            save_password(acc, pw)
        except (MailError, SecretStoreError) as e:
            messagebox.showerror("메일 연결", str(e))
            return
        finally:
            self.config(cursor="")
        self.mail_pw.set("")
        self.mail_on.set(True)
        self.mail_acc.set(acc)
        self.pw_hint.configure(text="저장됨 (바꿀 때만 입력)", foreground="#188038")
        save_env({"MAIL_ENABLED": "true", "MAIL_ACCOUNT": acc})
        messagebox.showinfo("메일 연결", f"연결했습니다: {acc}\n비밀번호를 Windows 자격 증명 관리자에 저장했습니다.\n"
                                       "캘린더(감시)를 다시 켜면 메일 확인이 시작됩니다.")

    def _mail_disconnect(self):
        from app.secrets_store import delete_password
        acc = self.mail_acc.get().strip().lower()
        if acc:
            delete_password(acc)
        self.mail_on.set(False)
        self.pw_hint.configure(text="아직 저장된 비밀번호 없음", foreground="#5f6368")
        save_env({"MAIL_ENABLED": "false"})
        messagebox.showinfo("메일", "연결을 끊고 저장된 비밀번호를 지웠습니다.")

    # ---------- 알림 탭 ----------
    def _build_notify(self, f, s):
        from app.notify_policy import SCOPE_LABELS
        ttk.Label(f, text="알림 (쪽지·메일 공통)", font=("맑은 고딕", 12, "bold")).pack(anchor="w")
        ttk.Label(f, foreground="#5f6368", wraplength=680,
                  text="알림을 꺼도, 방해금지 시간이어도 수집과 AI 분석은 계속됩니다. 막힌 알림은 나중에 몰아서 보내지 않습니다.").pack(anchor="w", pady=(0, 8))
        self.n_on = tk.BooleanVar(value=s.notify_enabled)
        ttk.Checkbutton(f, text="알림 받기", variable=self.n_on).pack(anchor="w")
        r = ttk.Frame(f); r.pack(fill="x", pady=4)
        ttk.Label(r, text="알림 범위:").pack(side="left")
        self.n_scope = tk.StringVar(value=s.notify_scope if s.notify_scope in SCOPE_LABELS else "school")
        for k, v in SCOPE_LABELS.items():
            ttk.Radiobutton(r, text=v, value=k, variable=self.n_scope).pack(side="left", padx=6)
        self.n_high = tk.BooleanVar(value=s.notify_high_only)
        ttk.Checkbutton(f, text="그 범위에서 '긴급'만 알림", variable=self.n_high).pack(anchor="w")
        src = ttk.Frame(f); src.pack(fill="x", pady=4)
        ttk.Label(src, text="알릴 것:").pack(side="left")
        self.n_ez = tk.BooleanVar(value="eztalk" in s.notify_sources)
        self.n_mail = tk.BooleanVar(value="mail" in s.notify_sources)
        ttk.Checkbutton(src, text="쪽지", variable=self.n_ez).pack(side="left", padx=6)
        ttk.Checkbutton(src, text="메일", variable=self.n_mail).pack(side="left")
        self.n_sound = tk.BooleanVar(value=s.notify_sound)
        ttk.Checkbutton(f, text="알림 소리", variable=self.n_sound).pack(anchor="w", pady=(6, 0))
        self.n_prev = tk.BooleanVar(value=s.notify_preview)
        ttk.Checkbutton(f, text="알림에 보낸 사람·제목·요약 보이기 (끄면 '새 메일' 처럼만 표시 — 옆 사람이 볼 수 있는 화면이면 끄기 권장)",
                        variable=self.n_prev).pack(anchor="w")
        q = ttk.Frame(f); q.pack(fill="x", pady=(8, 0))
        self.n_quiet = tk.BooleanVar(value=s.notify_quiet)
        ttk.Checkbutton(q, text="방해금지", variable=self.n_quiet).pack(side="left")
        self.n_qs = tk.StringVar(value=s.notify_quiet_start)
        self.n_qe = tk.StringVar(value=s.notify_quiet_end)
        ttk.Entry(q, textvariable=self.n_qs, width=7).pack(side="left", padx=(8, 2))
        ttk.Label(q, text="~").pack(side="left")
        ttk.Entry(q, textvariable=self.n_qe, width=7).pack(side="left", padx=2)
        ttk.Label(q, text="(한국 시각 HH:MM, 예: 22:00 ~ 08:00)", foreground="#5f6368").pack(side="left", padx=6)
        ttk.Button(f, text="테스트 알림", command=lambda: __import__("app.notifier", fromlist=["notify"]).notify(
            "[테스트] 충북메신저 AI 비서", "알림이 이렇게 보입니다.", sound=self.n_sound.get())).pack(anchor="w", pady=10)

    # ---------- 학교 정보 탭 ----------
    def _build_school(self, f, s):
        ttk.Label(f, text="학교 업무 기준 (모두 선택)", font=("맑은 고딕", 12, "bold")).pack(anchor="w")
        ttk.Label(f, foreground="#5f6368", wraplength=680,
                  text="AI 가 '학교 업무인지' 판단할 때 참고합니다. 참고 발신처는 판단을 돕는 정보일 뿐, 그 발신처만 받거나 무조건 학교 업무로 정하지 않습니다. "
                       "다음 분석부터 적용됩니다.").pack(anchor="w", pady=(0, 8))
        self.sch = {}
        for key, label, val, h in (("SCHOOL_NAME", "소속 학교", s.school_name, 1),
                                   ("SCHOOL_DUTIES", "담당 업무", s.school_duties, 3),
                                   ("SCHOOL_SENDERS", "참고 발신처", s.school_senders, 3),
                                   ("USER_ROLE", "내 역할", s.user_role, 1)):
            ttk.Label(f, text=label).pack(anchor="w", pady=(6, 0))
            t = tk.Text(f, height=h, wrap="word", font=FONT)
            t.insert("1.0", val)
            t.pack(fill="x")
            self.sch[key] = t
        ttk.Label(f, foreground="#5f6368", text="예) 담당 업무: 3학년 2반 담임, 정보부 정보업무 / 참고 발신처: 충북교육청, @cbe.go.kr, 교무부").pack(anchor="w", pady=6)

    def save(self):
        from app.notify_policy import validate
        err = validate(self.n_scope.get(), self.n_qs.get().strip(), self.n_qe.get().strip()) if self.n_quiet.get() else \
            validate(self.n_scope.get(), "22:00", "08:00")
        if err:
            messagebox.showerror("알림 설정", err)
            return
        analyze = self.analyze.spec()
        if not analyze:
            messagebox.showerror("모델 설정", "분석 모델을 선택하세요.")
            return
        try:
            n = int(self.skip_len.get() or 0)
        except ValueError:
            n = 0
        save_env({
            "AI_ANALYZE": analyze,
            "AI_DIGEST": self.digest.spec() or analyze,
            "AI_FALLBACK": self.fallback.spec(),
            "AI_REASONING": self.reasoning.get(),
            "SKIP_AI_KEYWORDS": ",".join(k.strip() for k in self.skip_kw.get().split(",") if k.strip()),
            "SKIP_AI_MIN_CHARS": str(max(0, n)),
            "ARCHIVE_LOCAL": "true" if self.local_on.get() else "false",
            "ARCHIVE_LOCAL_DIR": self.local_dir.get().strip(),
            "ARCHIVE_GDRIVE": "true" if self.gdrive_on.get() else "false",
            "ARCHIVE_GDRIVE_DIR": self.gdrive_dir.get().strip(),
            "ARCHIVE_GDRIVE_REDACT": "true" if self.gdrive_redact.get() else "false",
            "ARCHIVE_FORMAT": self.fmt.get(),
            "ARCHIVE_SUMMARY": "true" if self.summary_on.get() else "false",
            "MAIL_ENABLED": "true" if self.mail_on.get() else "false",
            "MAIL_ACCOUNT": self.mail_acc.get().strip().lower(),
            "MAIL_INTERVAL_MIN": self.mail_iv.get(),
            "NOTIFY_ENABLED": "true" if self.n_on.get() else "false",
            "NOTIFY_SCOPE": self.n_scope.get(),
            "NOTIFY_HIGH_ONLY": "true" if self.n_high.get() else "false",
            "NOTIFY_SOURCES": ",".join(x for x, v in (("eztalk", self.n_ez.get()), ("mail", self.n_mail.get())) if v),
            "NOTIFY_SOUND": "true" if self.n_sound.get() else "false",
            "NOTIFY_PREVIEW": "true" if self.n_prev.get() else "false",
            "NOTIFY_QUIET": "true" if self.n_quiet.get() else "false",
            "NOTIFY_QUIET_START": self.n_qs.get().strip(),
            "NOTIFY_QUIET_END": self.n_qe.get().strip(),
            **{k: " ".join(t.get("1.0", "end").split()) if k in ("SCHOOL_NAME", "USER_ROLE") else
               t.get("1.0", "end").strip().replace("\n", ", ") for k, t in self.sch.items()},
        })
        self.on_saved()
        self.destroy()


class App(tk.Tk):
    def __init__(self, provider: str | None):
        super().__init__()
        self.title("충북소통메신저 AI 비서 (테스트)")
        self.geometry("900x660")
        self.override = provider
        self.s = get_settings()
        self.storage = Storage(self.s.db_path)
        self._pipe = None
        self._watch_stop = None
        self._build()
        self._show_slots()
        self.refresh()

    # ---------- UI ----------
    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="모델 설정", command=self.on_settings).pack(side="left")
        ttk.Button(top, text="ChatGPT 로그인", command=self.on_chatgpt_login).pack(side="left", padx=4)
        ttk.Button(top, text="사용량", command=self.on_usage).pack(side="left")
        self.watch_btn = ttk.Button(top, text="▶ 쪽지·메일 감시 시작", command=self.on_watch_toggle)
        self.watch_btn.pack(side="left", padx=12)
        ttk.Button(top, text="브리핑", command=self.on_digest).pack(side="right")
        ttk.Button(top, text="일정 .ics 저장", command=self.on_ics).pack(side="right", padx=4)
        self.slot_label = ttk.Label(self, foreground="#5f6368", padding=(10, 0))
        self.slot_label.pack(fill="x")

        sender_row = ttk.Frame(self, padding=(8, 4))
        sender_row.pack(fill="x")
        ttk.Label(sender_row, text="보낸 사람:").pack(side="left")
        self.sender_var = tk.StringVar(value="(직접 입력)")
        ttk.Entry(sender_row, textvariable=self.sender_var, width=18).pack(side="left", padx=4)

        self.input = tk.Text(self, height=7, wrap="word", font=FONT)
        self.input.pack(fill="x", padx=8)
        self.input.insert("1.0", "여기에 메신저 메시지를 붙여넣으세요.")
        btns = ttk.Frame(self, padding=(8, 4))
        btns.pack(fill="x")
        self.analyze_btn = ttk.Button(btns, text="분석", command=self.on_analyze)
        self.analyze_btn.pack(side="left")
        self.status = ttk.Label(btns, text="")
        self.status.pack(side="left", padx=8)

        self.out = tk.Text(self, wrap="word", font=FONT, state="disabled")
        self.out.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        for k, c in COLORS.items():
            self.out.tag_configure(k, foreground=c, font=("맑은 고딕", 10, "bold"))
        self.out.tag_configure("dim", foreground="#9aa0a6")

    def _show_slots(self):
        s = self.s
        if self.override:
            text = f"이번 실행 모델: {slot_text(self.override)}"
        else:
            text = (f"분석: {slot_text(s.analyze_model)}   |   브리핑: {slot_text(s.digest_model)}   |   "
                    f"대체: {slot_text(s.fallback_model)}   |   사고: {REASONING_LABELS.get(s.reasoning, s.reasoning)}")
            if "chatgpt_plan" in (s.analyze_model + s.digest_model + s.fallback_model):
                text += "   ·  Using ChatGPT plan"
        self.slot_label.configure(text=text)

    def _write(self, chunks: list[tuple[str, str | None]]):
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        for text, tag in chunks:
            self.out.insert("end", text, tag or ())
        self.out.configure(state="disabled")

    def _pipeline(self):
        if self._pipe is None:
            s = get_settings(reload=True)
            if self.override:
                s.analyze_model = s.digest_model = self.override
            self._pipe = build_pipeline(s, self.storage)
        return self._pipe

    # ---------- actions ----------
    def refresh(self):
        chunks: list[tuple[str, str | None]] = []
        for m, a in self.storage.recent(limit=50):
            chunks.append((f"[{a.importance}] ", a.importance))
            chunks.append((f"{m.sender} · {m.received_at}", None))
            chunks.append((f"   ({a.provider}:{a.model})\n", "dim"))
            chunks.append((f"  {a.summary}\n", None))
            for act in a.action_items:
                chunks.append((f"  - {act}\n", None))
            if a.deadline:
                chunks.append((f"  ⏰ 마감 {a.deadline}\n", None))
            for e in a.events:
                chunks.append((f"  📅 {e.date} {e.start_time} {e.title}\n", None))
            chunks.append(("\n", None))
        self._write(chunks or [("아직 분석한 메시지가 없습니다.", None)])

    def _run_bg(self, work, done, status="AI 분석 중…"):
        self.analyze_btn.configure(state="disabled")
        self.status.configure(text=status)

        def target():
            try:
                result, err = work(), None
            except Exception as e:  # noqa: BLE001
                result, err = None, e
            self.after(0, lambda: self._finish(done, result, err))
        threading.Thread(target=target, daemon=True).start()

    def _finish(self, done, result, err):
        self.analyze_btn.configure(state="normal")
        self.status.configure(text="")
        if err:
            messagebox.showerror("오류", str(err))
        else:
            done(result)

    def on_watch_toggle(self):
        if self._watch_stop is not None:          # 끄기
            self._watch_stop.set()
            self._watch_stop = None
            self.watch_btn.configure(text="▶ 쪽지·메일 감시 시작")
            self.status.configure(text="감시를 멈췄습니다.")
            return
        stop = threading.Event()
        self._watch_stop = stop
        self.watch_btn.configure(text="■ 쪽지·메일 감시 중지")
        self.status.configure(text="쪽지·메일 감시 중 (읽기만 함)…")

        def loop():
            try:
                from app.runner import Runner
                runner = Runner(get_settings(reload=True), on_change=lambda: self.after(0, self.refresh),
                                pipeline=self._pipeline()).start()
                while not stop.is_set():
                    time.sleep(1)
                runner.stop()
            except Exception as e:  # noqa: BLE001
                err = str(e)
                self.after(0, lambda: (messagebox.showerror("감시", f"감시를 시작하지 못했습니다:\n{err}"),
                                       self.on_watch_toggle() if self._watch_stop is stop else None))
        threading.Thread(target=loop, daemon=True).start()

    def on_settings(self):
        def saved():
            self.s = get_settings(reload=True)
            self.override = None
            self._pipe = None          # 다음 분석 때 새 설정으로 다시 조립
            self._show_slots()
        SettingsDialog(self, saved)

    def on_analyze(self):
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        m = message_from_text(text, sender=self.sender_var.get())
        self._run_bg(lambda: self._pipeline().handle(m),
                     lambda a: self.refresh() if a else messagebox.showinfo("알림", "이미 분석했거나 분석에 실패했습니다."))

    def on_digest(self):
        def work():
            return self._pipeline().analyzer.digest(self.storage.recent(limit=100))

        def done(d):
            lines = [(f"📋 {d.get('headline', '')}\n\n", None)]
            for title, key, tag in (("긴급", "urgent", "긴급"), ("이번 주", "this_week", "중요"), ("참고", "fyi", "참고")):
                if d.get(key):
                    lines.append((f"{title}\n", tag))
                    lines += [(f"  - {x}\n", None) for x in d[key]]
                    lines.append(("\n", None))
            self._write(lines)
        self._run_bg(work, done, "브리핑 만드는 중…")

    def on_usage(self):
        since = datetime.now().strftime("%Y-%m-01")
        rows = self.storage.usage_summary(since)
        if not rows:
            messagebox.showinfo("사용량", "이번 달 AI 사용 기록이 없습니다.")
            return
        lines = [f"{since} 이후\n"]
        for r in rows:
            purpose = "분석" if r["purpose"] == "analyze" else "브리핑"
            lines.append(f"[{purpose}] {PROVIDER_LABELS.get(r['provider'], r['provider'])} · {r['model']}\n"
                         f"    호출 {r['calls']}회 · 입력 {r['inp'] or 0:,} · 출력 {r['outp'] or 0:,} 토큰")
        lines.append("\n※ 구독은 토큰만큼 돈이 나가지 않고 플랜 사용 한도에서 차감됩니다.")
        messagebox.showinfo("AI 사용량", "\n".join(lines))

    def on_chatgpt_login(self):
        from app.llm.chatgpt_auth import ChatGPTSession, MANAGE_USAGE_URL

        def done(rec):
            self._pipe = None
            messagebox.showinfo("ChatGPT", f"로그인 완료: {rec.get('email')}\n"
                                f"[모델 설정]에서 'ChatGPT 구독'을 고르면 플랜 사용량으로 요약합니다.\n"
                                f"사용량 관리: {MANAGE_USAGE_URL}")
        self._run_bg(lambda: ChatGPTSession().login(), done, "브라우저에서 로그인하세요…")

    def on_ics(self):
        events = [e for _, a in self.storage.recent(limit=1000) for e in a.events]
        if not events:
            messagebox.showinfo("일정", "추출된 일정이 없습니다.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".ics", filetypes=[("캘린더", "*.ics")])
        if path:
            export_ics(events, path)
            messagebox.showinfo("일정", f"{len(events)}개 일정을 저장했습니다.\n캘린더 앱에서 '가져오기' 하세요.")


def run_gui(provider: str | None = None):
    App(provider).mainloop()


if __name__ == "__main__":
    run_gui()
