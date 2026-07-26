from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QRDetectionRequest(BaseModel):
    camera_id: str = Field(min_length=1, max_length=100)
    zone_id: str = Field(min_length=1, max_length=50)
    badge_token: str = Field(min_length=8, max_length=200)
    timestamp: Optional[datetime] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class CloseSessionRequest(BaseModel):
    zone_id: Optional[str] = Field(default=None, max_length=50)
    interaction_point_id: Optional[int] = None
    screen_id: Optional[str] = Field(default=None, max_length=50)


class MessageCreateRequest(BaseModel):
    sender_id: int
    recipient_id: int
    text: str = Field(min_length=1, max_length=500)
    valid_hours: int = Field(default=24, ge=1, le=168)
    zone_id: Optional[str] = Field(default=None, max_length=50)
    screen_id: Optional[str] = Field(default=None, max_length=50)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Съобщението не може да е празно")
        return value


class VoiceCommandRequest(BaseModel):
    person_id: Optional[int] = None
    text_query: str = Field(min_length=1, max_length=500)
    zone_id: Optional[str] = Field(default=None, max_length=50)
    screen_id: Optional[str] = Field(default=None, max_length=50)


class DeliveryAckRequest(BaseModel):
    delivery_id: str = Field(min_length=8, max_length=100)
    message_ids: list[int] = Field(default_factory=list, max_length=100)


class PersonCreateRequest(BaseModel):
    full_name: str = Field(min_length=3, max_length=100)
    role: str
    class_name: Optional[str] = Field(default=None, max_length=10)
    password: Optional[str] = Field(default=None, min_length=8, max_length=256)


class EventCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    description: Optional[str] = Field(default=None, max_length=1000)
    start_time: datetime
    end_time: datetime
    target_group: Optional[str] = Field(default="All", max_length=100)
    room: Optional[str] = Field(default=None, max_length=50)


class TimetableCreateRequest(BaseModel):
    person_id: int
    date: date
    period: int = Field(ge=1, le=12)
    start_time: str
    end_time: str
    subject: str = Field(min_length=2, max_length=100)
    class_name: Optional[str] = Field(default=None, max_length=10)
    room: str = Field(min_length=1, max_length=50)


class BadgeStatusRequest(BaseModel):
    status: str


class PersonStatusRequest(BaseModel):
    active: bool


class DeviceEnrollRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=2, max_length=150)
    device_type: str = Field(min_length=2, max_length=30)
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    software_version: Optional[str] = Field(default=None, max_length=50)


class DeviceDiagnostics(BaseModel):
    """Small, non-personal browser/device capability snapshot."""

    model_config = ConfigDict(extra="forbid")

    browser: Optional[str] = Field(default=None, max_length=80)
    platform: Optional[str] = Field(default=None, max_length=80)
    language: Optional[str] = Field(default=None, max_length=30)
    secure_context: Optional[bool] = None
    standalone: Optional[bool] = None
    camera_api: Optional[bool] = None
    camera_permission: Optional[str] = Field(default=None, max_length=30)
    camera_status: Optional[str] = Field(default=None, max_length=30)
    scanner_engine: Optional[str] = Field(default=None, max_length=30)
    barcode_detector: Optional[bool] = None
    service_worker: Optional[bool] = None
    indexed_db: Optional[bool] = None
    web_socket: Optional[bool] = None
    speech_synthesis: Optional[bool] = None
    viewport_width: Optional[int] = Field(default=None, ge=0, le=20_000)
    viewport_height: Optional[int] = Field(default=None, ge=0, le=20_000)


class DeviceHeartbeatRequest(BaseModel):
    status: str = Field(default="online", max_length=30)
    software_version: Optional[str] = Field(default=None, max_length=50)
    capabilities: Optional[list[str]] = Field(default=None, max_length=30)
    diagnostics: Optional[DeviceDiagnostics] = None


class DeviceCommandAckRequest(BaseModel):
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)


class PwaPairRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=2, max_length=150)
    software_version: Optional[str] = Field(default=None, max_length=50)

    @field_validator("identifier")
    @classmethod
    def strip_pair_identifier(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Идентификаторът трябва да е поне 3 знака")
        return value

    @field_validator("name")
    @classmethod
    def strip_pair_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Името трябва да е поне 2 знака")
        return value


class KioskDetectRequest(BaseModel):
    badge_token: str = Field(min_length=8, max_length=200)
    timestamp: Optional[datetime] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class KioskQueryRequest(BaseModel):
    text_query: str = Field(min_length=1, max_length=500)

    @field_validator("text_query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Въпросът не може да е празен")
        return value


class KioskMessageRequest(BaseModel):
    recipient_id: int
    text: str = Field(min_length=1, max_length=500)
    valid_hours: int = Field(default=24, ge=1, le=168)

    @field_validator("text")
    @classmethod
    def strip_kiosk_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Съобщението не може да е празно")
        return value
