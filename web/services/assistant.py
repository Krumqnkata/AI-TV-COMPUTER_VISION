"""Privacy-scoped, deterministic school assistant responses."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy.orm import Session

from engine.admin_models import (
    Announcement,
    ClassGroup,
    Club,
    DirectoryEntry,
    Duty,
    GroupMembership,
    Reminder,
    Room,
    SchoolTask,
    Substitution,
)
from engine.db import Event, Message, Person, SystemEvent, Timetable, now_bg, today_bg
from web.services.admin_control import get_setting
from web.services.ai_runtime import answer_from_public_school_context, parse_read_only_intent
from web.services.assistant_rules import (
    meaningful_tokens,
    normalize_text,
    parse_intent_rule_based,
    text_match_score,
)


_PUBLIC_AUDIENCES = {"", "*", "all", "всички", "public", "публично"}
_WEEKDAY_LABELS = (
    "понеделник",
    "вторник",
    "сряда",
    "четвъртък",
    "петък",
    "събота",
    "неделя",
)
_ROLE_LABELS = {
    "student": "ученик",
    "teacher": "учител",
    "admin": "администратор",
    "guest": "гост",
}
_ROLE_AUDIENCES = {
    "student": {"student", "students", "ученик", "ученици"},
    "teacher": {"teacher", "teachers", "учител", "учители"},
    "admin": {"admin", "admins", "администратор", "администратори"},
    "guest": {"guest", "guests", "гост", "гости"},
}
PRIVATE_ASSISTANT_INTENTS = frozenset({
    "greeting",
    "identify_person",
    "leave_message",
    "check_messages",
    "check_timetable",
    "check_free_periods",
    "show_announcements",
    "check_substitutions",
    "check_duties",
    "check_tasks",
    "check_reminders",
})
_SUBJECT_NOISE_STEMS = (
    "днес",
    "утре",
    "другиден",
    "следващ",
    "текущ",
    "първи",
    "послед",
    "час",
    "разпис",
    "програм",
    "предмет",
    "кабинет",
    "стая",
)


def _person_label(person: Person) -> str:
    detail = person.class_name or person.role
    return f"{person.full_name} ({detail})"


def find_person_by_name(name: str, db: Session) -> tuple[Person | None, str]:
    clean_name = normalize_text(name)
    for title in ("г-н", "г-жа", "господин", "госпожа", "учител", "учителка"):
        clean_name = clean_name.replace(normalize_text(title), "").strip()
    query_words = [word.rstrip(".") for word in clean_name.split() if word]
    if not query_words:
        return None, "Моля посочете валидно име на получател."

    matches = []
    for candidate in db.query(Person).filter(Person.active.is_(True)).all():
        candidate_words = [normalize_text(word).rstrip(".") for word in candidate.full_name.split()]
        if all(
            any(
                query_word == candidate_word
                or (
                    len(query_word) >= 2
                    and candidate_word.startswith(query_word)
                )
                for candidate_word in candidate_words
            )
            for query_word in query_words
        ):
            matches.append(candidate)

    if not matches:
        return None, f"Не успях да намеря потребител с име '{name}' в базата данни."
    if len(matches) > 1:
        return (
            None,
            "Намерих няколко съвпадения: "
            + " или ".join(_person_label(person) for person in matches)
            + ". За кой се отнася?",
        )
    return matches[0], ""


def _short(value: str | None, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _target_date(parsed: dict[str, Any]) -> date:
    raw_offset = parsed.get("date_offset")
    if raw_offset is None:
        raw_offset = 1 if parsed.get("date") == "tomorrow" else 0
    try:
        offset = max(0, min(int(raw_offset), 14))
    except (TypeError, ValueError):
        offset = 0
    return today_bg() + timedelta(days=offset)


def _date_label(target: date) -> str:
    if target == today_bg():
        return "днес"
    if target == today_bg() + timedelta(days=1):
        return "утре"
    return f"{_WEEKDAY_LABELS[target.weekday()]}, {target:%d.%m.%Y г.}"


def _person_audiences(person: Person | None) -> set[str]:
    values = set(_PUBLIC_AUDIENCES)
    if person is None:
        return values
    values.update(_ROLE_AUDIENCES.get(person.role, {person.role}))
    if person.class_name:
        class_name = normalize_text(person.class_name)
        values.update({class_name, f"клас {class_name}"})
    return {normalize_text(value) for value in values}


def _audience_matches(value: str | None, person: Person | None) -> bool:
    targets = {
        normalize_text(part)
        for part in re.split(r"[,;/|]+", str(value or "all"))
        if normalize_text(part)
    } or {"all"}
    audiences = _person_audiences(person)
    return bool(targets & audiences)


def _person_group_ids(person: Person | None, db: Session) -> set[int]:
    if person is None:
        return set()
    group_ids = {
        membership.group_id
        for membership in db.query(GroupMembership).filter(
            GroupMembership.person_id == person.id,
        )
    }
    if person.class_name:
        class_name = normalize_text(person.class_name)
        group_ids.update(
            group.id
            for group in db.query(ClassGroup).filter(ClassGroup.active.is_(True)).all()
            if normalize_text(group.name) == class_name
        )
    return group_ids


def _rank_items(
    query: str,
    items: Iterable[Any],
    candidate_text,
) -> list[tuple[float, Any]]:
    ranked = [
        (text_match_score(query, candidate_text(item)), item)
        for item in items
    ]
    return sorted(ranked, key=lambda pair: pair[0], reverse=True)


def _infer_managed_intent(
    db: Session,
    person: Person | None,
    query: str,
) -> tuple[str, str | None] | None:
    """Use managed entity names as deterministic aliases before AI fallback."""
    candidates: list[tuple[float, str, str | None]] = []

    query_tokens = meaningful_tokens(query)
    room_lookup_allowed = not any(
        token.startswith(("кога", "отвор", "работно", "телефон", "контакт"))
        for token in query_tokens
    )
    if room_lookup_allowed:
        rooms = db.query(Room).filter(Room.active.is_(True)).limit(100).all()
        for room in rooms:
            score = text_match_score(
                query,
                " ".join(filter(None, (room.code, room.name, room.floor, room.wing))),
            )
            candidates.append((score, "check_room", room.code))

    clubs = db.query(Club).filter(Club.active.is_(True)).limit(100).all()
    for club in clubs:
        score = text_match_score(
            query,
            " ".join(filter(None, (club.name, club.description))),
        )
        candidates.append((score, "show_clubs", None))

    if person is not None:
        subject_query = _schedule_subject_query(query)
        if subject_query:
            timetable = db.query(Timetable).filter(
                Timetable.person_id == person.id,
                Timetable.date >= today_bg(),
                Timetable.date <= today_bg() + timedelta(days=14),
            ).limit(200).all()
            for record in timetable:
                score = text_match_score(subject_query, record.subject)
                candidates.append((score, "check_timetable", None))

    if not candidates:
        return None
    score, intent, entity = max(candidates, key=lambda item: item[0])
    return (intent, entity) if score >= 0.70 else None


def _directory_response(
    db: Session,
    query: str,
    *,
    explicit: bool,
) -> str | None:
    entries = db.query(DirectoryEntry).filter(
        DirectoryEntry.active.is_(True),
    ).order_by(DirectoryEntry.sort_order, DirectoryEntry.name).limit(500).all()
    if not entries:
        return (
            "Училищният указател още няма добавени записи."
            if explicit
            else None
        )

    ranked = _rank_items(
        query,
        entries,
        lambda item: " ".join(
            filter(None, (item.kind, item.name, item.value, item.details))
        ),
    )
    threshold = 0.36 if explicit else 0.58
    selected = [item for score, item in ranked if score >= threshold][:4]
    if not selected and explicit:
        selected = entries[:5]
    if not selected:
        return None

    rendered = []
    for item in selected:
        details = ". ".join(
            part.rstrip(".")
            for part in (_short(item.value), _short(item.details))
            if part
        )
        rendered.append(
            f"{item.name}: {details}."
            if details
            else f"{item.name}: няма добавени подробности."
        )
    return " ".join(rendered)


def _room_response(db: Session, query: str, target: str | None) -> str:
    rooms = db.query(Room).filter(Room.active.is_(True)).limit(100).all()
    normalized_target = normalize_text(target or "")
    exact = next(
        (
            room
            for room in rooms
            if normalized_target
            and normalized_target
            in {normalize_text(room.code), normalize_text(room.name)}
        ),
        None,
    )
    if exact is None:
        ranked = _rank_items(
            " ".join(filter(None, (target, query))),
            rooms,
            lambda item: " ".join(
                filter(
                    None,
                    (
                        item.code,
                        item.name,
                        item.floor,
                        item.wing,
                        item.directions,
                    ),
                )
            ),
        )
        exact = ranked[0][1] if ranked and ranked[0][0] >= 0.38 else None

    if exact:
        if exact.directions:
            return exact.directions
        location = ", ".join(part for part in (exact.floor, exact.wing) if part)
        return (
            f"{exact.code} — {exact.name} се намира: {location}."
            if location
            else f"{exact.code} — {exact.name}. Няма добавени указания за посоката."
        )

    built_in_rooms = {
        "304": "Кабинет 304 се намира на третия етаж, дясно крило.",
        "302": "Кабинет 302 се намира на третия етаж, ляво крило.",
        "201": "Кабинет 201 се намира на втория етаж, ляво крило.",
        "104": "Кабинет 104 се намира на първия етаж, дясно крило.",
        "физкултурен салон": "Физкултурният салон се намира в двора на училището.",
        "библиотека": "Библиотеката се намира на първия етаж, срещу главния вход.",
        "учителска стая": "Учителската стая е на втория етаж.",
        "директор": "Кабинетът на директора се намира на втория етаж.",
    }
    search_text = normalize_text(" ".join(filter(None, (target, query))))
    built_in = next(
        (
            response
            for key, response in built_in_rooms.items()
            if normalize_text(key) in search_text
        ),
        None,
    )
    if built_in:
        return built_in
    if target:
        return f"Не намерих място или кабинет „{target}“ в училищния указател."
    return "Кое място или кабинет търсите?"


def _schedule_subject_query(query: str) -> str:
    tokens = [
        token
        for token in meaningful_tokens(query)
        if not any(token.startswith(stem) for stem in _SUBJECT_NOISE_STEMS)
    ]
    return " ".join(tokens)


def _timetable_response(
    db: Session,
    person: Person | None,
    query: str,
    parsed: dict[str, Any],
    *,
    free_periods: bool,
) -> str:
    if person is None:
        return "Моля сканирайте баджа си, за да проверя личното Ви разписание."

    target = _target_date(parsed)
    records = db.query(Timetable).filter(
        Timetable.person_id == person.id,
        Timetable.date == target,
    ).order_by(Timetable.period).all()
    label = _date_label(target)
    if not records:
        return f"Нямате часове за {label}."

    if free_periods:
        periods = [record.period for record in records]
        gaps = [
            period
            for period in range(min(periods) + 1, max(periods))
            if period not in periods
        ]
        return (
            f"Свободните Ви часове за {label} са: "
            + ", ".join(f"{period}. час" for period in gaps)
            + "."
            if gaps
            else f"Нямате свободни часове за {label}."
        )

    selected = list(records)
    period = parsed.get("period")
    if isinstance(period, int):
        selected = [record for record in selected if record.period == period]

    subject_query = _schedule_subject_query(query)
    if subject_query:
        ranked = _rank_items(
            subject_query,
            selected,
            lambda item: item.subject,
        )
        subject_matches = [item for score, item in ranked if score >= 0.42]
        if subject_matches:
            selected = sorted(subject_matches, key=lambda item: item.period)

    scope = str(parsed.get("schedule_scope") or "full")
    if scope == "current" and target == today_bg():
        current_time = now_bg().time()
        selected = [
            record
            for record in selected
            if record.start_time <= current_time <= record.end_time
        ]
        if not selected:
            return "В момента нямате учебен час."
    elif scope == "next" and target == today_bg():
        current_time = now_bg().time()
        next_record = next(
            (record for record in selected if record.start_time > current_time),
            None,
        )
        selected = [next_record] if next_record else []
        if not selected:
            return "Нямате повече часове за днес."
    elif scope == "first":
        selected = selected[:1]
    elif scope == "last":
        selected = selected[-1:]

    if not selected:
        if period:
            return f"Нямате {period}. час за {label}."
        if subject_query:
            return f"Не намерих такъв предмет в разписанието Ви за {label}."
        return f"Нямате часове за {label}."

    if (
        len(selected) == 1
        and (
            scope != "full"
            or period is not None
            or bool(subject_query)
        )
    ):
        record = selected[0]
        return (
            f"За {label}: {record.period}. час е {record.subject} "
            f"в {record.room}, от {record.start_time:%H:%M} "
            f"до {record.end_time:%H:%M} ч."
        )

    return f"Програмата Ви за {label} е: " + ", ".join(
        f"{record.period}. час — {record.subject} в {record.room} "
        f"от {record.start_time:%H:%M}"
        for record in selected
    ) + "."


def _events_response(
    db: Session,
    query: str,
    parsed: dict[str, Any],
) -> str:
    target = _target_date(parsed)
    range_days = max(1, min(int(parsed.get("range_days") or 1), 14))
    start = datetime.combine(target, datetime.min.time())
    end = datetime.combine(
        target + timedelta(days=range_days - 1),
        datetime.max.time(),
    )
    events = db.query(Event).filter(
        Event.start_time >= start,
        Event.start_time <= end,
    ).order_by(Event.start_time).limit(100).all()
    if not events:
        return (
            f"Няма планирани събития за {_date_label(target)}."
            if range_days == 1
            else "Няма планирани събития през следващите дни."
        )

    event_query = " ".join(
        token
        for token in meaningful_tokens(query)
        if not any(
            token.startswith(stem)
            for stem in ("събит", "днес", "утре", "предстоящ", "седмиц")
        )
    )
    if event_query:
        ranked = _rank_items(
            event_query,
            events,
            lambda item: " ".join(
                filter(None, (item.title, item.description, item.room))
            ),
        )
        specific = [item for score, item in ranked if score >= 0.46]
        if specific:
            events = specific

    rendered = []
    for event in events[:6]:
        when = (
            f"{event.start_time:%H:%M}"
            if range_days == 1
            else f"{event.start_time:%d.%m, %H:%M}"
        )
        place = f" в {event.room}" if event.room else ""
        rendered.append(f"„{event.title}“ — {when} ч.{place}")
    prefix = (
        f"Събитията за {_date_label(target)} са: "
        if range_days == 1
        else "Предстоящите събития са: "
    )
    return prefix + "; ".join(rendered) + "."


def _announcements_response(
    db: Session,
    person: Person | None,
) -> str:
    now = now_bg()
    announcements = db.query(Announcement).filter(
        Announcement.published.is_(True),
        Announcement.archived_at.is_(None),
        Announcement.publish_from <= now,
    ).order_by(Announcement.publish_from.desc()).limit(500).all()
    announcements = [
        item
        for item in announcements
        if (item.publish_until is None or item.publish_until > now)
        and _audience_matches(item.audience, person)
    ]
    if not announcements:
        return "Няма активни обяви за Вас."
    return "Активните обяви са: " + " ".join(
        f"„{item.title}“ — {_short(item.body)}"
        for item in announcements[:5]
    )


def _clubs_response(db: Session, query: str) -> str:
    clubs = db.query(Club).filter(
        Club.active.is_(True),
    ).order_by(Club.name).limit(100).all()
    if not clubs:
        return "Няма добавени активни клубове."
    ranked = _rank_items(
        query,
        clubs,
        lambda item: " ".join(
            filter(None, ("клуб", item.name, item.description, item.schedule_text))
        ),
    )
    specific = [item for score, item in ranked if score >= 0.48]
    selected = specific[:3] if specific else clubs[:6]
    rendered = []
    for club in selected:
        details = []
        if club.schedule_text:
            details.append(club.schedule_text)
        if club.room:
            details.append(f"място: {club.room.code} — {club.room.name}")
        if club.advisor:
            details.append(f"ръководител: {club.advisor.full_name}")
        if not details and club.description:
            details.append(_short(club.description))
        rendered.append(
            f"{club.name} ({'; '.join(details)})"
            if details
            else club.name
        )
    return "Активните клубове са: " + "; ".join(rendered) + "."


def _substitutions_response(
    db: Session,
    person: Person | None,
    parsed: dict[str, Any],
) -> str:
    target = _target_date(parsed)
    substitutions = db.query(Substitution).filter(
        Substitution.date == target,
    ).order_by(Substitution.period, Substitution.class_name).limit(100).all()
    requested_class = normalize_text(parsed.get("class_name") or "")
    if not requested_class and person and person.class_name:
        requested_class = normalize_text(person.class_name)
    if requested_class:
        substitutions = [
            item
            for item in substitutions
            if normalize_text(item.class_name) == requested_class
        ]
    elif person and person.role == "teacher":
        substitutions = [
            item
            for item in substitutions
            if person.id
            in {
                item.original_teacher_id,
                item.replacement_teacher_id,
            }
        ]

    if not substitutions:
        scope = (
            f" за {parsed['class_name']}"
            if parsed.get("class_name")
            else ""
        )
        return f"Няма замествания{scope} за {_date_label(target)}."

    rendered = []
    for item in substitutions[:8]:
        teacher = (
            item.replacement_teacher.full_name
            if item.replacement_teacher
            else "неуточнен учител"
        )
        subject = f", {item.subject}" if item.subject else ""
        room = f", {item.room.code}" if item.room else ""
        rendered.append(
            f"{item.class_name}, {item.period}. час{subject} — {teacher}{room}"
        )
    return f"Заместванията за {_date_label(target)} са: " + "; ".join(rendered) + "."


def _duties_response(
    db: Session,
    person: Person | None,
    parsed: dict[str, Any],
) -> str:
    if person is None:
        return "Моля сканирайте баджа си, за да проверя личните Ви дежурства."
    target = _target_date(parsed)
    duties = db.query(Duty).filter(
        Duty.person_id == person.id,
        Duty.date == target,
    ).order_by(Duty.start_time).limit(50).all()
    if not duties:
        return f"Нямате дежурство за {_date_label(target)}."
    return f"Дежурствата Ви за {_date_label(target)} са: " + "; ".join(
        f"{duty.start_time:%H:%M}–{duty.end_time:%H:%M} ч. в {duty.location}"
        + (f" ({_short(duty.notes)})" if duty.notes else "")
        for duty in duties
    ) + "."


def _tasks_response(
    db: Session,
    person: Person | None,
) -> str:
    group_ids = _person_group_ids(person, db)
    tasks = db.query(SchoolTask).filter(
        SchoolTask.status == "active",
    ).limit(500).all()
    visible = []
    for task in tasks:
        if task.assigned_person_id is not None:
            allowed = person is not None and task.assigned_person_id == person.id
        elif task.group_id is not None:
            allowed = task.group_id in group_ids
        else:
            allowed = _audience_matches(task.audience, person)
        if allowed:
            visible.append(task)
    visible.sort(key=lambda item: (item.due_at is None, item.due_at or datetime.max, item.id))
    if not visible:
        return "Нямате активни задачи."
    rendered = []
    for task in visible[:6]:
        due = f", срок {task.due_at:%d.%m.%Y %H:%M}" if task.due_at else ""
        description = f" — {_short(task.description)}" if task.description else ""
        rendered.append(f"„{task.title}“{due}{description}")
    return "Активните Ви задачи са: " + "; ".join(rendered) + "."


def _reminders_response(
    db: Session,
    person: Person | None,
) -> str:
    if person is None:
        return "Моля сканирайте баджа си, за да проверя личните Ви напомняния."
    group_ids = _person_group_ids(person, db)
    reminders = db.query(Reminder).filter(
        Reminder.status == "pending",
    ).order_by(Reminder.remind_at).limit(500).all()
    visible = [
        item
        for item in reminders
        if (
            item.person_id == person.id
            or (
                item.person_id is None
                and item.group_id is not None
                and item.group_id in group_ids
            )
            or (item.person_id is None and item.group_id is None)
        )
    ]
    if not visible:
        return "Нямате чакащи напомняния."
    return "Чакащите Ви напомняния са: " + "; ".join(
        f"{item.remind_at:%d.%m.%Y %H:%M} — {_short(item.text)}"
        for item in visible[:6]
    ) + "."


def _identity_response(person: Person | None) -> str:
    if person is None:
        return "Няма активен профил. Моля сканирайте баджа си."
    role = _ROLE_LABELS.get(person.role, person.role)
    class_detail = f" от {person.class_name} клас" if person.class_name else ""
    return f"Влезли сте като {person.full_name}, {role}{class_detail}."


def _time_response() -> str:
    current = now_bg()
    return (
        f"Сега е {current:%H:%M} ч., "
        f"{_WEEKDAY_LABELS[current.weekday()]}, {current:%d.%m.%Y г.}"
    )


def _help_response() -> str:
    return (
        "Мога да помогна с разписание, текущ или следващ час, свободни часове, "
        "кабинети и упътвания, събития, обяви, клубове, замествания, училищни "
        "контакти, лични съобщения, задачи, напомняния и дежурства. Например: "
        "„Къде ми е математиката?“, „Имам ли заместване утре?“, "
        "„Какви задачи имам?“ или „Как да стигна до библиотеката?“"
    )


def handle_voice_command(
    person_id: int | None,
    text_query: str,
    db: Session,
) -> dict[str, str]:
    query = normalize_text(text_query)
    blocked_words = (
        "тъп",
        "глупав",
        "скапан",
        "урод",
        "педераст",
        "курва",
        "шибан",
    )
    if any(word in query for word in blocked_words):
        return {
            "intent": "blocked",
            "query": text_query,
            "response": (
                "Моля, поддържайте учтив тон и задавайте въпроси, "
                "свързани с училището."
            ),
        }

    person = db.get(Person, person_id) if person_id else None
    if person_id and (not person or not person.active):
        return {
            "intent": "invalid_person",
            "query": text_query,
            "response": "Профилът не е активен.",
        }

    parsed = parse_intent_rule_based(text_query)
    prepared_response = None
    if parsed["intent"] == "unknown":
        inferred = _infer_managed_intent(db, person, text_query)
        if inferred:
            parsed["intent"], entity = inferred
            if entity:
                parsed["room_number"] = entity
        else:
            prepared_response = _directory_response(
                db,
                text_query,
                explicit=False,
            )
            if prepared_response:
                parsed["intent"] = "directory_lookup"
            else:
                parsed = parse_read_only_intent(db, query) or parsed

    intent = parsed["intent"]
    response = (
        "Не намерих достатъчно информация за този въпрос. "
        "Попитайте „Какво можеш?“, за да видите примерите."
    )

    if intent == "greeting":
        first_name = person.full_name.split()[0] if person else None
        response = (
            f"Здравейте, {first_name}! С какво да помогна?"
            if first_name
            else "Здравейте! С какво да помогна?"
        )

    elif intent == "help":
        response = _help_response()

    elif intent == "identify_person":
        response = _identity_response(person)

    elif intent == "time_and_date":
        response = _time_response()

    elif intent == "school_info":
        school_name = str(get_setting(db, "school.name"))
        subtitle = str(get_setting(db, "school.subtitle"))
        response = f"{school_name} — {subtitle}."

    elif intent == "leave_message":
        if not person:
            response = "Моля първо се идентифицирайте чрез бадж."
        elif not parsed.get("recipient_name"):
            response = "За кого е съобщението?"
        elif not parsed.get("message_text"):
            response = (
                "Какво съобщение искате да оставите за "
                f"{parsed['recipient_name']}?"
            )
        else:
            recipient, error = find_person_by_name(
                str(parsed["recipient_name"]),
                db,
            )
            if recipient:
                message = Message(
                    sender_id=person.id,
                    recipient_id=recipient.id,
                    text=str(parsed["message_text"]),
                    valid_until=now_bg()
                    + timedelta(
                        hours=int(
                            get_setting(
                                db,
                                "messages.default_valid_hours",
                            )
                        )
                    ),
                    status="active",
                )
                db.add(message)
                db.flush()
                response = f"Записах съобщението за {recipient.full_name}."
            else:
                response = error

    elif intent == "check_messages":
        if not person:
            response = "Моля първо се идентифицирайте чрез бадж."
        else:
            messages = db.query(Message).filter(
                Message.recipient_id == person.id,
                Message.status == "active",
                Message.valid_until > now_bg(),
            ).all()
            response = (
                f"Имате {len(messages)} нови съобщения. "
                + ". ".join(
                    f"от {message.sender.full_name}: „{message.text}“"
                    for message in messages
                )
                if messages
                else "Нямате нови съобщения."
            )

    elif intent == "check_timetable":
        response = _timetable_response(
            db,
            person,
            text_query,
            parsed,
            free_periods=False,
        )

    elif intent == "check_free_periods":
        response = _timetable_response(
            db,
            person,
            text_query,
            parsed,
            free_periods=True,
        )

    elif intent == "check_room":
        response = _room_response(
            db,
            text_query,
            parsed.get("room_number"),
        )

    elif intent == "show_events":
        response = _events_response(db, text_query, parsed)

    elif intent == "show_announcements":
        response = _announcements_response(db, person)

    elif intent == "show_clubs":
        response = _clubs_response(db, text_query)

    elif intent == "check_substitutions":
        response = _substitutions_response(db, person, parsed)

    elif intent == "check_duties":
        response = _duties_response(db, person, parsed)

    elif intent == "check_tasks":
        response = _tasks_response(db, person)

    elif intent == "check_reminders":
        response = _reminders_response(db, person)

    elif intent == "directory_lookup":
        response = prepared_response or _directory_response(
            db,
            text_query,
            explicit=True,
        ) or response

    elif intent == "unknown":
        response = answer_from_public_school_context(db, text_query) or response

    safe_metadata: dict[str, Any] = {
        "intent": intent,
        "matched": intent != "unknown",
    }
    if intent not in PRIVATE_ASSISTANT_INTENTS:
        safe_metadata["query"] = text_query
        safe_metadata["response"] = response
    db.add(
        SystemEvent(
            event_type="question_asked",
            person_id=person_id,
            timestamp=now_bg(),
            metadata_json=json.dumps(safe_metadata, ensure_ascii=False),
        )
    )
    db.commit()
    return {
        "intent": intent,
        "query": text_query,
        "response": response,
    }
