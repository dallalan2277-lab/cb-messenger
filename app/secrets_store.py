"""메일 앱 비밀번호 보관 — Windows 자격 증명 관리자(keyring). 파일에는 절대 쓰지 않음.

Windows: 제어판 > 자격 증명 관리자 > Windows 자격 증명 에 'cb-messenger-ai:naver-mail' 로 보임.
keyring 이 없거나 쓸 수 없으면 저장하지 않고(메모리에만) 알려 줌.
"""
from __future__ import annotations

SERVICE = "cb-messenger-ai:naver-mail"


class SecretStoreError(RuntimeError):
    pass


def _kr():
    try:
        import keyring
        return keyring
    except ImportError as e:
        raise SecretStoreError("keyring 이 없어 비밀번호를 저장할 수 없습니다. 1_설치하기.bat 을 다시 실행하세요.") from e


def save_password(account: str, password: str) -> None:
    try:
        _kr().set_password(SERVICE, account.lower(), password)
    except SecretStoreError:
        raise
    except Exception as e:
        raise SecretStoreError(f"Windows 자격 증명 관리자에 저장하지 못했습니다: {e}") from e


def load_password(account: str) -> str | None:
    try:
        return _kr().get_password(SERVICE, account.lower())
    except Exception:
        return None


def delete_password(account: str) -> None:
    try:
        _kr().delete_password(SERVICE, account.lower())
    except Exception:
        pass
