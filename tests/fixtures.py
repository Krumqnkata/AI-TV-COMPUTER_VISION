"""Deterministic data builders used only by the isolated test suite."""

from datetime import datetime, time, timedelta

from engine.db import (
    Badge,
    Camera,
    Event,
    InteractionPoint,
    Message,
    Person,
    Timetable,
    hash_token,
    now_bg,
    today_bg,
)


def seed_test_data(session) -> None:
    if session.query(Person).first() is not None:
        return

    anton = Person(full_name="Антон Иванов", role="student", class_name="9Б", active=True)
    georgi = Person(full_name="Георги Петров", role="student", class_name="9Б", active=True)
    maria = Person(full_name="Мария Димитрова", role="teacher", active=True)
    admin = Person(full_name="Администратор", role="admin", active=True)
    session.add_all([anton, georgi, maria, admin])
    session.flush()

    session.add_all([
        Badge(person_id=anton.id, token_hash=hash_token("SCH-8F3A92C1"), status="active"),
        Badge(person_id=georgi.id, token_hash=hash_token("SCH-9A2C3B4D"), status="active"),
        Badge(person_id=maria.id, token_hash=hash_token("SCH-7E1B2C3A"), status="active"),
    ])

    entrance = InteractionPoint(
        name="Главен вход - Екран",
        zone_id="MAIN_ENTRANCE",
        type="entrance",
        screen_id="SCR-ENTRANCE-01",
    )
    lobby = InteractionPoint(
        name="Фоайе - Киоск",
        zone_id="LOBBY",
        type="kiosk",
        screen_id="SCR-LOBBY-01",
    )
    teachers = InteractionPoint(
        name="Учителска стая - Екран",
        zone_id="TEACHERS_ROOM",
        type="teacher_room",
        screen_id="SCR-TEACHERS-01",
    )
    library = InteractionPoint(
        name="Библиотека - Екран",
        zone_id="LIBRARY",
        type="library",
        screen_id="SCR-LIBRARY-01",
    )
    session.add_all([entrance, lobby, teachers, library])
    session.flush()

    session.add_all([
        Camera(name="CAM-ENTRANCE-01", zone_id="MAIN_ENTRANCE", interaction_point_id=entrance.id, stream_url="0", active=True),
        Camera(name="CAM-LOBBY-01", zone_id="LOBBY", interaction_point_id=lobby.id, stream_url="1", active=True),
        Camera(name="CAM-TEACHERS-01", zone_id="TEACHERS_ROOM", interaction_point_id=teachers.id, stream_url="2", active=True),
    ])

    session.add_all([
        Message(
            sender_id=anton.id,
            recipient_id=georgi.id,
            text="Отивам да си купя баничка в междучасието. Чакам те на лавката!",
            valid_until=now_bg() + timedelta(days=1),
            status="active",
        ),
        Message(
            sender_id=maria.id,
            recipient_id=anton.id,
            text="Антоне, ела в учителската стая след третия час.",
            valid_until=now_bg() + timedelta(days=1),
            status="active",
        ),
    ])

    demo_date = today_bg()
    session.add_all([
        Timetable(person_id=anton.id, date=demo_date, period=1, start_time=time(8, 0), end_time=time(8, 45), subject="Математика", class_name="9Б", room="Кабинет 201"),
        Timetable(person_id=anton.id, date=demo_date, period=2, start_time=time(8, 55), end_time=time(9, 40), subject="Български език", class_name="9Б", room="Кабинет 104"),
        Timetable(person_id=anton.id, date=demo_date, period=3, start_time=time(9, 50), end_time=time(10, 35), subject="Информатика", class_name="9Б", room="Кабинет 304"),
        Timetable(person_id=anton.id, date=demo_date, period=5, start_time=time(11, 35), end_time=time(12, 20), subject="Физика", class_name="9Б", room="Физкултурен салон"),
        Timetable(person_id=maria.id, date=demo_date, period=1, start_time=time(8, 0), end_time=time(8, 45), subject="Информатика", class_name="10А", room="Кабинет 304"),
        Timetable(person_id=maria.id, date=demo_date, period=3, start_time=time(9, 50), end_time=time(10, 35), subject="Информатика", class_name="9Б", room="Кабинет 304"),
        Timetable(person_id=maria.id, date=demo_date, period=5, start_time=time(11, 35), end_time=time(12, 20), subject="Информационни технологии", class_name="8А", room="Кабинет 302"),
    ])

    session.add_all([
        Event(
            title="Сбирка на клуба по Роботика",
            description="Подготовка за националното състезание по роботика.",
            start_time=datetime.combine(demo_date, time(14, 30)),
            end_time=datetime.combine(demo_date, time(16, 0)),
            target_group="Роботика",
            room="Кабинет 304",
        ),
        Event(
            title="Училищен концерт",
            description="Годишен патронен празник на училището.",
            start_time=datetime.combine(demo_date, time(12, 30)),
            end_time=datetime.combine(demo_date, time(14, 0)),
            target_group="All",
            room="Фоайе",
        ),
    ])
    session.commit()
