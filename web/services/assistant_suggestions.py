"""Role-aware, privacy-safe question suggestions for the kiosk assistant."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from engine.admin_models import DirectoryEntry
from engine.db import Person
from web.services.assistant_rules import normalize_text


MAX_FAQ_SUGGESTIONS = 12

_PERSONAL_QUESTIONS = (
    ("profile-active", "Кой профил е активен?"),
    ("messages-new", "Имам ли нови съобщения?"),
    ("tasks-active", "Какви активни задачи имам?"),
    ("reminders-pending", "Какво да не забравя?"),
)

_DUTY_QUESTION = ("duties-today", "Имам ли дежурство днес?")

_SCHEDULE_QUESTIONS = (
    ("schedule-next", "Кой е следващият ми час?"),
    ("schedule-today", "Каква е програмата ми днес?"),
    ("schedule-free", "Кога имам свободен час?"),
    ("substitutions-tomorrow", "Имам ли заместване утре?"),
)

_SCHOOL_QUESTIONS = (
    ("announcements-important", "Какви са важните обяви?"),
    ("events-upcoming", "Какви събития предстоят?"),
    ("clubs-active", "Какви клубове има?"),
    ("library-location", "Как да стигна до библиотеката?"),
)

_ADMIN_SUBSTITUTIONS_QUESTION = (
    "substitutions-today",
    "Какви са заместванията днес?",
)


def _question(question_id: str, query: str) -> dict[str, str]:
    return {
        "id": question_id,
        "label": query,
        "query": query,
    }


def _category(
    category_id: str,
    label: str,
    questions: tuple[tuple[str, str], ...] | list[tuple[str, str]],
) -> dict[str, object]:
    return {
        "id": category_id,
        "label": label,
        "questions": [
            _question(question_id, query)
            for question_id, query in questions
        ],
    }


def _faq_questions(
    db: Session,
    seen_queries: set[str],
) -> list[dict[str, str]]:
    entries = (
        db.query(DirectoryEntry)
        .filter(
            DirectoryEntry.active.is_(True),
            func.lower(func.trim(DirectoryEntry.kind)) == "faq",
        )
        .order_by(
            DirectoryEntry.sort_order,
            DirectoryEntry.name,
            DirectoryEntry.id,
        )
        .limit(100)
        .all()
    )
    questions: list[dict[str, str]] = []
    for entry in entries:
        query = (entry.name or "").strip()
        normalized = normalize_text(query)
        if not normalized or normalized in seen_queries:
            continue
        seen_queries.add(normalized)
        questions.append(_question(f"faq-{entry.id}", query))
        if len(questions) >= MAX_FAQ_SUGGESTIONS:
            break
    return questions


def build_kiosk_query_suggestions(
    db: Session,
    person: Person,
) -> dict[str, list[dict[str, object]]]:
    """Build the touch-friendly question catalog for one active kiosk role."""
    role = normalize_text(person.role)
    personal_questions = list(_PERSONAL_QUESTIONS)
    if role in {"teacher", "admin"}:
        personal_questions.append(_DUTY_QUESTION)

    categories = [
        _category("personal", "За мен", personal_questions),
    ]
    if role in {"student", "teacher"}:
        categories.append(
            _category("schedule", "Разписание", _SCHEDULE_QUESTIONS)
        )

    school_questions = list(_SCHOOL_QUESTIONS)
    if role == "admin":
        school_questions.append(_ADMIN_SUBSTITUTIONS_QUESTION)
    categories.append(_category("school", "Училище", school_questions))

    seen_queries = {
        normalize_text(str(question["query"]))
        for category in categories
        for question in category["questions"]
    }
    faq_questions = _faq_questions(db, seen_queries)
    if faq_questions:
        categories.append({
            "id": "faq",
            "label": "Често задавани",
            "questions": faq_questions,
        })

    return {"categories": categories}
