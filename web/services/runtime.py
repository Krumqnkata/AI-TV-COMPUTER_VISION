from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ActiveSession:
    person_id: int
    last_activity: datetime
    zone_id: str
    interaction_point_id: int | None = None
    screen_id: str | None = None


class RuntimeRegistry:
    """Single-process cooldown/session state.

    The class is lock-protected for TestClient threads and concurrent node calls.
    A future multi-worker deployment should replace this with Redis.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.recent_detections: dict[str, dict] = {}
        self.active_sessions: dict[str, ActiveSession] = {}

    @staticmethod
    def session_key(zone_id: str, interaction_point_id: int | None, screen_id: str | None) -> str:
        if screen_id:
            return f"screen:{screen_id}"
        if interaction_point_id:
            return f"point:{interaction_point_id}"
        return f"zone:{zone_id}"

    def check_duplicate(
        self,
        token_fingerprint: str,
        camera_id: str,
        confidence: float,
        now: datetime,
    ) -> str | None:
        with self._lock:
            expired = [
                key for key, info in self.recent_detections.items()
                if (now - info["timestamp"]).total_seconds() > 30
            ]
            for key in expired:
                self.recent_detections.pop(key, None)

            previous = self.recent_detections.get(token_fingerprint)
            if previous:
                elapsed = (now - previous["timestamp"]).total_seconds()
                if previous["camera_id"] == camera_id and elapsed < 10:
                    return "duplicate_same_camera"
                if previous["camera_id"] != camera_id and elapsed < 5:
                    if confidence > previous["confidence"]:
                        previous.update(camera_id=camera_id, confidence=confidence, timestamp=now)
                        return "duplicate_other_camera_replaced"
                    return "duplicate_other_camera_lower_confidence"
            return None

    def register_detection(
        self,
        token_fingerprint: str,
        camera_id: str,
        confidence: float,
        now: datetime,
    ) -> None:
        with self._lock:
            self.recent_detections[token_fingerprint] = {
                "camera_id": camera_id,
                "timestamp": now,
                "confidence": confidence,
            }

    def acquire_session(
        self,
        person_id: int,
        zone_id: str,
        interaction_point_id: int | None,
        screen_id: str | None,
        now: datetime,
    ) -> tuple[bool, str]:
        key = self.session_key(zone_id, interaction_point_id, screen_id)
        with self._lock:
            active = self.active_sessions.get(key)
            if active and (now - active.last_activity).total_seconds() < 60:
                if active.person_id != person_id:
                    return False, key
                active.last_activity = now
                return True, key
            self.active_sessions[key] = ActiveSession(
                person_id=person_id,
                last_activity=now,
                zone_id=zone_id,
                interaction_point_id=interaction_point_id,
                screen_id=screen_id,
            )
            return True, key

    def touch_person(self, person_id: int, now: datetime) -> None:
        with self._lock:
            for session in self.active_sessions.values():
                if session.person_id == person_id and (now - session.last_activity).total_seconds() < 60:
                    session.last_activity = now

    def person_has_session(
        self,
        person_id: int,
        now: datetime,
        zone_id: str | None = None,
        screen_id: str | None = None,
    ) -> bool:
        with self._lock:
            for session in self.active_sessions.values():
                if session.person_id != person_id or (now - session.last_activity).total_seconds() >= 60:
                    continue
                if screen_id and session.screen_id != screen_id:
                    continue
                if zone_id and session.zone_id != zone_id:
                    continue
                return True
        return False

    def close(
        self,
        zone_id: str | None = None,
        interaction_point_id: int | None = None,
        screen_id: str | None = None,
    ) -> list[ActiveSession]:
        closed: list[ActiveSession] = []
        with self._lock:
            for key, session in list(self.active_sessions.items()):
                matches = (
                    (screen_id and session.screen_id == screen_id)
                    or (interaction_point_id and session.interaction_point_id == interaction_point_id)
                    or (zone_id and session.zone_id == zone_id)
                )
                if matches:
                    closed.append(self.active_sessions.pop(key))
        return closed

    def clear(self) -> None:
        with self._lock:
            self.recent_detections.clear()
            self.active_sessions.clear()


runtime_registry = RuntimeRegistry()
