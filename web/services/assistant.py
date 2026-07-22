import json
import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from engine.db import Event, Message, Person, SystemEvent, Timetable, now_bg, today_bg


def parse_intent_rule_based(query: str) -> dict:
    query = query.lower().strip()
    empty = {"recipient_name": None, "message_text": None, "room_number": None, "date": None}

    if any(x in query for x in ["съобщение", "съобщения", "писма", "писмо", "имам ли нещо"]):
        if not any(x in query for x in ["остави", "кажи на", "предай", "напиши на"]):
            return {"intent": "check_messages", **empty, "date": "today"}

    markers = ["остави съобщение за", "остави съобщение на", "кажи на", "предай на", "напиши на"]
    if any(marker in query for marker in markers):
        recipient_name = None
        message_text = None
        for marker in markers:
            if marker not in query:
                continue
            after = query.split(marker, 1)[1].strip()
            for separator in [", че ", " че ", ", да ", " да "]:
                if separator in after:
                    recipient_name, message_text = after.split(separator, 1)
                    break
            if recipient_name is None:
                words = after.split()
                recipient_name = " ".join(words[:2]) if len(words) >= 2 else after
                message_text = " ".join(words[2:]) if len(words) >= 2 else ""
            break
        return {
            "intent": "leave_message",
            **empty,
            "recipient_name": recipient_name.strip() if recipient_name else None,
            "message_text": message_text.strip() if message_text else None,
        }

    if any(x in query for x in ["свободен час", "дупка", "прозорец"]):
        return {"intent": "check_free_periods", **empty, "date": "tomorrow" if "утре" in query else "today"}
    if any(x in query for x in ["час", "клас", "програма", "разписание"]):
        return {"intent": "check_timetable", **empty, "date": "tomorrow" if "утре" in query else "today"}
    if any(x in query for x in ["кабинет", "стая", "къде е", "намира се", "салон", "библиотека", "директор", "учителска"]):
        matches = re.findall(r"\d+", query)
        room_number = matches[0] if matches else None
        if not room_number:
            labels = {
                "салон": "физкултурен салон",
                "библиотека": "библиотека",
                "учителска": "учителска стая",
                "директор": "директор",
            }
            room_number = next((value for key, value in labels.items() if key in query), None)
        return {"intent": "check_room", **empty, "room_number": room_number}
    if any(x in query for x in ["събитие", "събития", "концерт", "клуб", "сбирка", "празник"]):
        return {"intent": "show_events", **empty, "date": "today"}
    return {"intent": "unknown", **empty}


def _person_label(person: Person) -> str:
    detail = person.class_name or person.role
    return f"{person.full_name} ({detail})"


def find_person_by_name(name: str, db: Session) -> tuple[Person | None, str]:
    clean_name = name.lower().strip()
    for title in ["г-н", "г-жа", "господин", "госпожа", "учител", "учителка"]:
        clean_name = clean_name.replace(title, "").strip()
    query_words = [word.rstrip(".") for word in re.split(r"\s+", clean_name) if word]
    if not query_words:
        return None, "Моля посочете валидно име на получател."

    matches = []
    for candidate in db.query(Person).filter(Person.active.is_(True)).all():
        candidate_words = [word.lower().rstrip(".") for word in candidate.full_name.split()]
        if all(any(q == c or (len(q) >= 2 and c.startswith(q)) for c in candidate_words) for q in query_words):
            matches.append(candidate)

    if not matches:
        return None, f"Не успях да намеря потребител с име '{name}' в базата данни."
    if len(matches) > 1:
        return None, "Намерих няколко съвпадения: " + " или ".join(_person_label(p) for p in matches) + ". За кой се отнася?"
    return matches[0], ""


def handle_voice_command(person_id: int | None, text_query: str, db: Session) -> dict:
    query = text_query.lower().strip()
    blocked_words = ["тъп", "глупав", "скапан", "урод", "педераст", "курва", "шибан"]
    if any(word in query for word in blocked_words):
        return {
            "intent": "blocked",
            "query": text_query,
            "response": "Моля, поддържайте учтив тон и задавайте въпроси, свързани с училището.",
        }

    person = db.get(Person, person_id) if person_id else None
    if person_id and (not person or not person.active):
        return {"intent": "invalid_person", "query": text_query, "response": "Профилът не е активен."}

    parsed = parse_intent_rule_based(query)
    intent = parsed["intent"]
    response = "Не успях да разбера въпроса Ви. Опитайте с други думи."

    if intent == "leave_message":
        if not person:
            response = "Моля първо се идентифицирайте чрез бадж."
        elif not parsed["recipient_name"]:
            response = "За кого е съобщението?"
        elif not parsed["message_text"]:
            response = f"Какво съобщение искате да оставите за {parsed['recipient_name']}?"
        else:
            recipient, error = find_person_by_name(parsed["recipient_name"], db)
            if recipient:
                message = Message(
                    sender_id=person.id,
                    recipient_id=recipient.id,
                    text=parsed["message_text"],
                    valid_until=now_bg() + timedelta(hours=24),
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
            if messages:
                response = f"Имате {len(messages)} нови съобщения. " + ". ".join(
                    f"от {m.sender.full_name}: '{m.text}'" for m in messages
                )
            else:
                response = "Нямате нови съобщения."

    elif intent in ("check_timetable", "check_free_periods"):
        if not person:
            response = "Моля сканирайте баджа си."
        else:
            target_date = today_bg() + (timedelta(days=1) if parsed["date"] == "tomorrow" else timedelta())
            records = db.query(Timetable).filter(
                Timetable.person_id == person.id,
                Timetable.date == target_date,
            ).order_by(Timetable.period).all()
            date_word = "утре" if parsed["date"] == "tomorrow" else "днес"
            if intent == "check_timetable":
                if "следващ" in query and target_date == today_bg():
                    record = next((r for r in records if r.start_time > now_bg().time()), None)
                    response = (
                        f"Следващият Ви час е {record.subject} в {record.room} от {record.start_time:%H:%M} ч."
                        if record else "Нямате повече часове за днес."
                    )
                elif records:
                    response = f"Програмата Ви за {date_word} е: " + ", ".join(
                        f"{r.period}-ти час: {r.subject} в {r.room}" for r in records
                    )
                else:
                    response = f"Нямате часове за {date_word}."
            elif not records:
                response = f"Нямате часове за {date_word}."
            else:
                periods = [r.period for r in records]
                gaps = [p for p in range(min(periods) + 1, max(periods)) if p not in periods]
                response = (
                    f"Имате свободен час ({date_word}) на: " + ", ".join(f"{p}-ти час" for p in gaps) + "."
                    if gaps else f"Нямате свободни часове за {date_word}."
                )

    elif intent == "check_room":
        room = str(parsed["room_number"] or "").lower().strip()
        rooms = {
            "304": "Кабинет 304 се намира на третия етаж, дясно крило.",
            "302": "Кабинет 302 се намира на третия етаж, ляво крило.",
            "201": "Кабинет 201 се намира на втория етаж, ляво крило.",
            "104": "Кабинет 104 се намира на първия етаж, дясно крило.",
            "физкултурен салон": "Физкултурният салон се намира в двора на училището.",
            "библиотека": "Библиотеката се намира на първия етаж, срещу главния вход.",
            "учителска стая": "Учителската стая е на втория етаж.",
            "директор": "Кабинетът на директора се намира на втория етаж.",
        }
        response = next((value for key, value in rooms.items() if key in room or room in key), None) if room else None
        response = response or (f"Не намерих кабинет '{parsed['room_number']}'." if room else "Кой кабинет търсите?")

    elif intent == "show_events":
        start = datetime.combine(today_bg(), datetime.min.time())
        end = datetime.combine(today_bg(), datetime.max.time())
        events = db.query(Event).filter(Event.start_time >= start, Event.start_time <= end).order_by(Event.start_time).all()
        response = (
            "Днес има следните събития: " + ", ".join(f"'{e.title}' в {e.room}" for e in events) + "."
            if events else "Няма планирани събития за днес."
        )

    safe_metadata = {"intent": intent, "response": response}
    if intent not in ("leave_message", "check_messages"):
        safe_metadata["query"] = text_query
    db.add(SystemEvent(
        event_type="question_asked",
        person_id=person_id,
        timestamp=now_bg(),
        metadata_json=json.dumps(safe_metadata, ensure_ascii=False),
    ))
    db.commit()
    return {"intent": intent, "query": text_query, "response": response}
