from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


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


class DeviceHeartbeatRequest(BaseModel):
    status: str = Field(default="online", max_length=30)
    software_version: Optional[str] = Field(default=None, max_length=50)
    capabilities: Optional[list[str]] = Field(default=None, max_length=30)


class DeviceCommandAckRequest(BaseModel):
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)
