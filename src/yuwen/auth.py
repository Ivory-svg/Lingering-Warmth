"""登录、会话校验和按来源 IP 限制试错。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from threading import Lock

from yuwen.config import AuthConfig
from yuwen.store import Store


COOKIE_NAME = "yuwen_session"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(
        self,
        store: Store,
        config: AuthConfig,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self.config = config
        self.clock = clock
        self._fingerprint = _sha256("yuwen-session:" + config.password)
        self._failures: dict[str, list[datetime]] = {}
        self._locked_until: dict[str, datetime] = {}
        self._failure_lock = Lock()

    def check_password(self, supplied: str) -> bool:
        return hmac.compare_digest(_sha256(supplied), _sha256(self.config.password))

    def retry_after(self, ip: str) -> int:
        with self._failure_lock:
            now = self.clock()
            locked_until = self._locked_until.get(ip)
            if locked_until is not None and locked_until > now:
                return max(1, int((locked_until - now).total_seconds()))
            self._locked_until.pop(ip, None)
            attempts = [
                moment for moment in self._failures.get(ip, [])
                if now - moment < timedelta(minutes=15)
            ]
            if attempts:
                self._failures[ip] = attempts
            else:
                self._failures.pop(ip, None)
            return 0

    def record_failure(self, ip: str) -> int:
        with self._failure_lock:
            now = self.clock()
            attempts = [
                moment for moment in self._failures.get(ip, [])
                if now - moment < timedelta(minutes=15)
            ]
            attempts.append(now)
            self._failures[ip] = attempts
            if len(attempts) >= 5:
                self._locked_until[ip] = now + timedelta(minutes=15)
        return self.retry_after(ip)

    def clear_failures(self, ip: str) -> None:
        with self._failure_lock:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        expires = now + timedelta(days=self.config.session_days)
        self.store.create_auth_session(
            _sha256(token), self._fingerprint, now.isoformat(), expires.isoformat()
        )
        return token

    def is_authenticated(self, token: str | None) -> bool:
        if not token:
            return False
        token_hash = _sha256(token)
        session = self.store.get_auth_session(token_hash)
        if session is None:
            return False
        now = self.clock()
        if (
            datetime.fromisoformat(session["expires_at"]) <= now
            or not hmac.compare_digest(session["password_fingerprint"], self._fingerprint)
        ):
            self.store.delete_auth_session(token_hash)
            return False
        if now - datetime.fromisoformat(session["last_seen_at"]) >= timedelta(hours=1):
            self.store.touch_auth_session(token_hash, now.isoformat())
        return True

    def logout(self, token: str | None) -> None:
        if token:
            self.store.delete_auth_session(_sha256(token))
