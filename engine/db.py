import os
from datetime import datetime, date, time
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Date, Time, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    password_hash = Column(String(255), nullable=True)  # Argon2 hash for password

    # Relationships
    badges = relationship("Badge", back_populates="person", cascade="all, delete-orphan")
    sent_messages = relationship("Message", foreign_keys="[Message.sender_id]", back_populates="sender")
    received_messages = relationship("Message", foreign_keys="[Message.recipient_id]", back_populates="recipient")
    timetable_records = relationship("Timetable", back_populates="person", cascade="all, delete-orphan")
    system_events = relationship("SystemEvent", back_populates="person")

    def __repr__(self):
        return f"<Person(id={self.id}, name='{self.full_name}', role='{self.role}')>"


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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
        return f"<InteractionPoint(id={self.id}, name='{self.name}', zone='{self.zone_id}', type='{self.type}')>"


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
        return f"<Camera(id={self.id}, name='{self.name}', zone='{self.zone_id}')>"


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
        return f"<Event(id={self.id}, title='{self.title}', room='{self.room}')>"


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
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)  # Време
    metadata_json = Column(Text, nullable=True)  # Технически данни (JSON като стринг)

    # Relationships
    camera = relationship("Camera", back_populates="system_events")
    interaction_point = relationship("InteractionPoint", back_populates="system_events")
    person = relationship("Person", back_populates="system_events")

    def __repr__(self):
        return f"<SystemEvent(id={self.id}, type='{self.event_type}', timestamp={self.timestamp})>"


# Инициализация и Хеширане
def hash_token(token: str) -> str:
    """ Генерира SHA-256 хеш за QR токен с цел защита на сигурността """
    import hashlib
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def init_db(database_url="sqlite:///data/school_ai.db"):
    """ Създава таблиците в базата данни, ако не съществуват """
    db_dir = os.path.dirname(database_url.replace("sqlite:///", ""))
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        
    engine = create_engine(database_url, echo=False)
    Base.metadata.create_all(engine)
    return engine


def seed_db(session):
    """ Напълва базата данни с примерни данни за тестване """
    # 1. Проверяваме дали вече има данни
    if session.query(Person).first() is not None:
        return  # Вече е попълнена
    
    # 2. Потребители (persons)
    anton = Person(full_name="Антон Иванов", role="student", class_name="9Б", active=True)
    georgi = Person(full_name="Георги Петров", role="student", class_name="9Б", active=True)
    maria = Person(full_name="Мария Димитрова", role="teacher", active=True)
    admin = Person(full_name="Администратор", role="admin", active=True)
    
    session.add_all([anton, georgi, maria, admin])
    session.commit()  # Записваме, за да получат ID-та
    
    # 3. Баджове (badges)
    # Примерни токени: SCH-8F3A92C1, SCH-9A2C3B4D, SCH-7E1B2C3A
    token_anton = "SCH-8F3A92C1"
    token_georgi = "SCH-9A2C3B4D"
    token_maria = "SCH-7E1B2C3A"
    
    badge_anton = Badge(person_id=anton.id, token_hash=hash_token(token_anton), status="active")
    badge_georgi = Badge(person_id=georgi.id, token_hash=hash_token(token_georgi), status="active")
    badge_maria = Badge(person_id=maria.id, token_hash=hash_token(token_maria), status="active")
    
    session.add_all([badge_anton, badge_georgi, badge_maria])
    
    # 4. Интерактивни точки (interaction_points)
    entrance_pt = InteractionPoint(name="Главен вход - Екран", zone_id="MAIN_ENTRANCE", type="entrance", screen_id="SCR-ENTRANCE-01")
    lobby_pt = InteractionPoint(name="Фоайе - Киоск", zone_id="LOBBY", type="kiosk", screen_id="SCR-LOBBY-01")
    teachers_pt = InteractionPoint(name="Учителска стая - Екран", zone_id="TEACHERS_ROOM", type="teacher_room", screen_id="SCR-TEACHERS-01")
    library_pt = InteractionPoint(name="Библиотека - Екран", zone_id="LIBRARY", type="library", screen_id="SCR-LIBRARY-01")
    
    session.add_all([entrance_pt, lobby_pt, teachers_pt, library_pt])
    session.commit()
    
    # 5. Камери (cameras)
    cam_entrance = Camera(name="Камера главен вход", zone_id="MAIN_ENTRANCE", interaction_point_id=entrance_pt.id, stream_url="0", active=True)
    cam_lobby = Camera(name="Камера фоайе киоск", zone_id="LOBBY", interaction_point_id=lobby_pt.id, stream_url="1", active=True)
    cam_teachers = Camera(name="Камера учителска стая", zone_id="TEACHERS_ROOM", interaction_point_id=teachers_pt.id, stream_url="2", active=True)
    
    session.add_all([cam_entrance, cam_lobby, cam_teachers])
    
    # 6. Съобщения (messages)
    # Антон оставя съобщение на Георги
    msg = Message(
        sender_id=anton.id,
        recipient_id=georgi.id,
        text="Отивам да си купя баничка в междучасието. Чакам те на лавката!",
        valid_until=datetime(2026, 7, 10, 18, 0, 0),
        status="active"
    )
    # Госпожа Мария Димитрова оставя съобщение на Антон
    msg_teacher = Message(
        sender_id=maria.id,
        recipient_id=anton.id,
        text="Антоне, моля те ела в учителската стая след 3-тия час, за да уточним проекта по информатика.",
        valid_until=datetime(2026, 7, 10, 15, 0, 0),
        status="active"
    )
    session.add_all([msg, msg_teacher])
    
    # 7. Разписание (timetable)
    # Разписание за ученика Антон за днес (10 Юли 2026г.)
    timetable_anton = [
        Timetable(person_id=anton.id, date=date(2026, 7, 10), period=1, start_time=time(8, 0), end_time=time(8, 45), subject="Математика", class_name="9Б", room="Кабинет 201"),
        Timetable(person_id=anton.id, date=date(2026, 7, 10), period=2, start_time=time(8, 55), end_time=time(9, 40), subject="Български език", class_name="9Б", room="Кабинет 104"),
        Timetable(person_id=anton.id, date=date(2026, 7, 10), period=3, start_time=time(9, 50), end_time=time(10, 35), subject="Информатика", class_name="9Б", room="Кабинет 304"),
        # Има "дупка" (свободен час) между 10:35 и 11:35
        Timetable(person_id=anton.id, date=date(2026, 7, 10), period=5, start_time=time(11, 35), end_time=time(12, 20), subject="Физика", class_name="9Б", room="Физкултурен салон"),
    ]
    
    # Разписание за учителката Мария Димитрова
    timetable_maria = [
        Timetable(person_id=maria.id, date=date(2026, 7, 10), period=1, start_time=time(8, 0), end_time=time(8, 45), subject="Информатика", class_name="10А", room="Кабинет 304"),
        Timetable(person_id=maria.id, date=date(2026, 7, 10), period=3, start_time=time(9, 50), end_time=time(10, 35), subject="Информатика", class_name="9Б", room="Кабинет 304"),
        # Мария има свободен час на 4-ти час (10:45 - 11:30)
        Timetable(person_id=maria.id, date=date(2026, 7, 10), period=5, start_time=time(11, 35), end_time=time(12, 20), subject="Информационни технологии", class_name="8А", room="Кабинет 302"),
    ]
    
    session.add_all(timetable_anton + timetable_maria)
    
    # 8. Събития (events)
    event1 = Event(
        title="Сбирка на клуба по Роботика",
        description="Ще се проведе подготовка за националното състезание по роботика.",
        start_time=datetime(2026, 7, 10, 14, 30, 0),
        end_time=datetime(2026, 7, 10, 16, 0, 0),
        target_group="Роботика",
        room="Кабинет 304"
    )
    event2 = Event(
        title="Училищен концерт",
        description="Годишен патронен празник на училището. Всички ученици и учители са поканени във фоайето.",
        start_time=datetime(2026, 7, 10, 12, 30, 0),
        end_time=datetime(2026, 7, 10, 14, 0, 0),
        target_group="All",
        room="Фоайе"
    )
    session.add_all([event1, event2])
    
    session.commit()
    print("Database seeded successfully with initial test data.")

if __name__ == "__main__":
    # Тестово стартиране за инициализация и тестване локално
    engine = init_db()
    Session = sessionmaker(bind=engine)
    session = Session()
    seed_db(session)
    session.close()
