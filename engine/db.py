from datetime import date, datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date, Time, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

# ─── Българска часова зона ───
# Ползва се за default-ите на created_at/timestamp колоните по-долу, за да не
# зависят от часовата зона на самата машина/контейнер, на който върви сървъра.
try:
    from zoneinfo import ZoneInfo
    BG_TZ = ZoneInfo("Europe/Sofia")
except Exception:
    # Липсва IANA tzdata база (чест случай на Windows или "slim" Docker образи).
    # Fallback към фиксиран офсет — работи, но НЕ следи автоматично лято/зимно време.
    # Правилният фикс: pip install tzdata --break-system-packages
    from datetime import timezone, timedelta as _timedelta
    import time as _time_module
    _is_dst_now = _time_module.localtime().tm_isdst > 0
    _bg_offset_hours = 3 if _is_dst_now else 2
    BG_TZ = timezone(_timedelta(hours=_bg_offset_hours))
    print(
        f"[ПРЕДУПРЕЖДЕНИЕ] Липсва tzdata база — 'Europe/Sofia' не е намерена. "
        f"Използва се фиксиран офсет UTC+{_bg_offset_hours} без автоматична смяна "
        f"лято/зимно време. Инсталирай пакета 'tzdata': pip install tzdata --break-system-packages"
    )


def now_bg() -> datetime:
    """
    Текущо време по българска часова зона, като 'наивен' datetime (без tzinfo) —
    съвместимо с DateTime колоните по-долу, които също са наивни. ВАЖНО: това е
    функция, подавана като default=now_bg (без скоби!) — SQLAlchemy я вика наново
    при всеки INSERT, а не веднъж при стартиране на приложението.
    """
    return datetime.now(BG_TZ).replace(tzinfo=None)


def today_bg() -> date:
    """Днешна дата по българска часова зона."""
    return datetime.now(BG_TZ).date()


class Person(Base):
    """
    10.1. Таблица persons
    Съдържа потребителите.
    """
    __tablename__ = 'persons'

    id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False)  # student / teacher / admin / guest
    class_name = Column(String(10), nullable=True)  # Клас, ако е ученик
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=now_bg, nullable=False)
    password_hash = Column(String(255), nullable=True)  # Argon2 hash for password

    # Relationships
    badges = relationship("Badge", back_populates="person", cascade="all, delete-orphan")
    sent_messages = relationship("Message", foreign_keys="[Message.sender_id]", back_populates="sender")
    received_messages = relationship("Message", foreign_keys="[Message.recipient_id]", back_populates="recipient")
    timetable_records = relationship("Timetable", back_populates="person", cascade="all, delete-orphan")
    system_events = relationship("SystemEvent", back_populates="person")

    def __repr__(self):
        return self.full_name

    __str__ = __repr__


class Badge(Base):
    """
    10.2. Таблица badges
    Съдържа QR баджовете.
    """
    __tablename__ = 'badges'

    id = Column(Integer, primary_key=True, autoincrement=True)
    person_id = Column(Integer, ForeignKey('persons.id'), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)  # Хеш на QR токена
    status = Column(String(20), default="active", nullable=False)  # active / lost / disabled
    created_at = Column(DateTime, default=now_bg, nullable=False)

    # Relationships
    person = relationship("Person", back_populates="badges")

    def __repr__(self):
        return f"<Badge(id={self.id}, person_id={self.person_id}, status='{self.status}')>"


class InteractionPoint(Base):
    """
    10.3. Таблица interaction_points
    Интерактивни точки за комуникация (киоск, екран на входа и др.)
    """
    __tablename__ = 'interaction_points'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    zone_id = Column(String(50), nullable=False)  # Зона
    type = Column(String(20), nullable=False)  # entrance / kiosk / teacher_room / library
    screen_id = Column(String(50), nullable=True)  # Свързан екран
    active = Column(Boolean, default=True, nullable=False)

    # Relationships
    cameras = relationship("Camera", back_populates="interaction_point")
    system_events = relationship("SystemEvent", back_populates="interaction_point")

    def __repr__(self):
        return f"{self.name} — {self.zone_id}"

    __str__ = __repr__


class Camera(Base):
    """
    10.4. Таблица cameras
    Камери, свързани със зони и интерактивни точки.
    """
    __tablename__ = 'cameras'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    zone_id = Column(String(50), nullable=False)  # Зона
    interaction_point_id = Column(Integer, ForeignKey('interaction_points.id'), nullable=True)  # Свързана точка
    stream_url = Column(String(255), nullable=False)  # Локален адрес
    active = Column(Boolean, default=True, nullable=False)

    # Relationships
    interaction_point = relationship("InteractionPoint", back_populates="cameras")
    system_events = relationship("SystemEvent", back_populates="camera")

    def __repr__(self):
        return f"{self.name} — {self.zone_id}"

    __str__ = __repr__


class Message(Base):
    """
    10.5. Таблица messages
    Асинхронни съобщения между потребители.
    """
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    sender_id = Column(Integer, ForeignKey('persons.id'), nullable=False)
    recipient_id = Column(Integer, ForeignKey('persons.id'), nullable=False)
    text = Column(Text, nullable=False)  # Съдържание
    valid_until = Column(DateTime, nullable=False)  # Валидност
    delivered_at = Column(DateTime, nullable=True)  # Дата на доставяне
    status = Column(String(20), default="active", nullable=False)  # active / delivered / expired / deleted

    # Relationships
    sender = relationship("Person", foreign_keys=[sender_id], back_populates="sent_messages")
    recipient = relationship("Person", foreign_keys=[recipient_id], back_populates="received_messages")

    def __repr__(self):
        return f"<Message(id={self.id}, sender_id={self.sender_id}, recipient_id={self.recipient_id}, status='{self.status}')>"


class Timetable(Base):
    """
    10.6. Таблица timetable
    Училищно разписание на часовете за ученици и учители.
    """
    __tablename__ = 'timetable'

    id = Column(Integer, primary_key=True, autoincrement=True)
    person_id = Column(Integer, ForeignKey('persons.id'), nullable=False)
    date = Column(Date, nullable=False)  # Дата
    period = Column(Integer, nullable=False)  # Час (напр. 1, 2, 3...)
    start_time = Column(Time, nullable=False)  # Начален час
    end_time = Column(Time, nullable=False)  # Краен час
    subject = Column(String(100), nullable=False)  # Предмет
    class_name = Column(String(10), nullable=True)  # Клас
    room = Column(String(50), nullable=False)  # Кабинет

    # Relationships
    person = relationship("Person", back_populates="timetable_records")

    def __repr__(self):
        return f"<Timetable(id={self.id}, person_id={self.person_id}, subject='{self.subject}', room='{self.room}')>"


class Event(Base):
    """
    10.7. Таблица events
    Училищни събития, съобщения, клубове.
    """
    __tablename__ = 'events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(150), nullable=False)  # Заглавие
    description = Column(Text, nullable=True)  # Описание
    start_time = Column(DateTime, nullable=False)  # Начало
    end_time = Column(DateTime, nullable=False)  # Край
    target_group = Column(String(100), nullable=True)  # За кого се отнася (клас, роля или "All")
    room = Column(String(50), nullable=True)  # Кабинет

    def __repr__(self):
        return self.title

    __str__ = __repr__


class SystemEvent(Base):
    """
    10.8. Таблица system_events
    Технически логове на системни събития.
    """
    __tablename__ = 'system_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False)  # Тип събитие (напр. badge_detected)
    camera_id = Column(Integer, ForeignKey('cameras.id'), nullable=True)  # Камера
    interaction_point_id = Column(Integer, ForeignKey('interaction_points.id'), nullable=True)  # Точка
    person_id = Column(Integer, ForeignKey('persons.id'), nullable=True)  # Потребител, ако е нужно
    timestamp = Column(DateTime, default=now_bg, nullable=False)  # Време
    metadata_json = Column(Text, nullable=True)  # Технически данни (JSON като стринг)

    # Relationships
    camera = relationship("Camera", back_populates="system_events")
    interaction_point = relationship("InteractionPoint", back_populates="system_events")
    person = relationship("Person", back_populates="system_events")

    def __repr__(self):
        return f"<SystemEvent(id={self.id}, type='{self.event_type}', timestamp={self.timestamp})>"


class DeliveryReceipt(Base):
    """Acknowledgment state for personal messages sent to a kiosk/screen."""
    __tablename__ = "delivery_receipts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_id = Column(String(100), unique=True, nullable=False, index=True)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=False)
    screen_id = Column(String(50), nullable=True)
    zone_id = Column(String(50), nullable=False)
    message_ids_json = Column(Text, nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    created_at = Column(DateTime, default=now_bg, nullable=False)
    acknowledged_at = Column(DateTime, nullable=True)


# Инициализация и Хеширане
def hash_token(token: str) -> str:
    """ Генерира SHA-256 хеш за QR токен с цел защита на сигурността """
    import hashlib
    return hashlib.sha256(token.encode('utf-8')).hexdigest()
