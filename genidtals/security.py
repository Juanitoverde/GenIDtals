"""Brute-force protection and audit events."""

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from .crypto import Sealer
from .errors import TemplateError
from .stores import TemplateStore

log = logging.getLogger("genidtals")

MAX_LOCKOUT_SECONDS = 86_400.0


@dataclass(frozen=True)
class AuditEvent:
    """A security-relevant event. Contains no biometric data and no raw user ID.

    ``subject`` is a keyed pseudonym: stable for one user under one key, so
    events can be correlated, but it cannot be reversed to the user ID.
    """

    event: str      # enroll | verify | delete | lockout | rotate
    outcome: str    # success | failure | error
    subject: str
    reason: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class Auditor:
    def __init__(self, sealer: Sealer, hook: Optional[Callable[[AuditEvent], None]] = None):
        self._sealer = sealer
        self._hook = hook

    def subject_ref(self, user_id: str) -> str:
        return self._sealer.index("audit:" + user_id)[:24]

    def emit(self, event: str, outcome: str, user_id: str, reason: str = "") -> None:
        ev = AuditEvent(event, outcome, self.subject_ref(user_id), reason)
        log.info("audit %s", json.dumps(ev.to_dict(), sort_keys=True))
        if self._hook is not None:
            try:
                self._hook(ev)
            except Exception:  # an audit sink must never break authentication
                log.exception("audit hook raised")


@dataclass
class LockoutStatus:
    locked: bool
    locked_until: Optional[float] = None
    failures: int = 0


class AttemptLimiter:
    """Locks a user out after repeated failed verifications.

    State is sealed and kept in the template store, so it survives restarts
    and is shared by every process using the same store. Lockout duration
    doubles with each lockout, up to one day. Note that lockouts can be
    triggered deliberately by someone presenting bad samples for a victim's
    user ID; pair this with application-level rate limiting per client.
    """

    def __init__(self, store: TemplateStore, sealer: Sealer, max_failures: int,
                 window: float, lockout: float):
        self.store, self.sealer = store, sealer
        self.max_failures, self.window, self.lockout = max_failures, window, lockout
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.max_failures > 0

    def _key(self, user_id: str) -> str:
        return "a/" + self.sealer.index("attempts:" + user_id)

    def _context(self, user_id: str) -> bytes:
        return b"attempts\0" + user_id.encode()

    def _load(self, user_id: str) -> dict:
        blob = self.store.get(self._key(user_id))
        if blob is None:
            return {"failures": [], "lockouts": 0, "locked_until": 0.0}
        try:
            return json.loads(self.sealer.open(blob, self._context(user_id))[0])
        except (TemplateError, ValueError):
            # Unreadable state (e.g. after key rotation) fails closed for one lockout period.
            log.warning("Unreadable attempt state; applying a precautionary lockout")
            return {"failures": [], "lockouts": 1, "locked_until": time.time() + self.lockout}

    def _save(self, user_id: str, state: dict) -> None:
        blob = self.sealer.seal(json.dumps(state).encode(), self._context(user_id))
        self.store.put(self._key(user_id), blob)

    def status(self, user_id: str) -> LockoutStatus:
        if not self.enabled:
            return LockoutStatus(False)
        state = self._load(user_id)
        now = time.time()
        if state["locked_until"] > now:
            return LockoutStatus(True, state["locked_until"], len(state["failures"]))
        return LockoutStatus(False, None, len([t for t in state["failures"] if now - t < self.window]))

    def record_failure(self, user_id: str) -> LockoutStatus:
        if not self.enabled:
            return LockoutStatus(False)
        with self._lock:
            state = self._load(user_id)
            now = time.time()
            state["failures"] = [t for t in state["failures"] if now - t < self.window] + [now]
            if len(state["failures"]) >= self.max_failures:
                duration = min(self.lockout * 2 ** state["lockouts"], MAX_LOCKOUT_SECONDS)
                state["lockouts"] += 1
                state["locked_until"] = now + duration
                state["failures"] = []
            self._save(user_id, state)
        locked = state["locked_until"] > now
        return LockoutStatus(locked, state["locked_until"] if locked else None, len(state["failures"]))

    def record_success(self, user_id: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.store.delete(self._key(user_id))

    def reset(self, user_id: str) -> None:
        """Administrative unlock."""
        with self._lock:
            self.store.delete(self._key(user_id))
