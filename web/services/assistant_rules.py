"""Deterministic Bulgarian intent parsing for the school assistant.

The parser deliberately keeps mutating intents strict while read-only intents
accept common synonyms, inflections, punctuation differences and small
speech-to-text/typing errors.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Iterable

from engine.db import today_bg


_EMPTY_FIELDS: dict[str, Any] = {
    "recipient_name": None,
    "message_text": None,
    "room_number": None,
    "date": None,
    "date_offset": 0,
    "period": None,
    "schedule_scope": "full",
    "class_name": None,
    "range_days": 1,
}

_STOP_WORDS = {
    "аз",
    "ако",
    "в",
    "във",
    "ви",
    "вие",
    "да",
    "дали",
    "до",
    "е",
    "за",
    "и",
    "има",
    "как",
    "какво",
    "какъв",
    "каква",
    "какви",
    "кога",
    "кой",
    "коя",
    "кои",
    "къде",
    "ли",
    "ме",
    "ми",
    "мога",
    "може",
    "моля",
    "на",
    "не",
    "някакъв",
    "някаква",
    "от",
    "по",
    "са",
    "се",
    "съм",
    "със",
    "това",
    "тук",
    "ще",
}

_WEEKDAYS = {
    "понедел": 0,
    "вторник": 1,
    "сряда": 2,
    "четвърт": 3,
    "петък": 4,
    "събот": 5,
    "недел": 6,
}

_PERIOD_WORDS = {
    "първи": 1,
    "първия": 1,
    "първият": 1,
    "втори": 2,
    "втория": 2,
    "вторият": 2,
    "трети": 3,
    "третия": 3,
    "третият": 3,
    "четвърти": 4,
    "четвъртия": 4,
    "четвъртият": 4,
    "пети": 5,
    "петия": 5,
    "петият": 5,
    "шести": 6,
    "шестия": 6,
    "шестият": 6,
    "седми": 7,
    "седмия": 7,
    "седмият": 7,
    "осми": 8,
    "осмия": 8,
    "осмият": 8,
    "девети": 9,
    "деветия": 9,
    "деветият": 9,
    "десети": 10,
    "десетия": 10,
    "десетият": 10,
}

_ROOM_ALIASES = (
    (("физкултур", "спортна зала", "спортен салон"), "физкултурен салон"),
    (("библиот",), "библиотека"),
    (("учителск",), "учителска стая"),
    (("директор", "дирекция"), "директор"),
)

_MESSAGE_ACTION_MARKERS = (
    "остави съобщение за",
    "остави съобщение на",
    "оставиш съобщение за",
    "оставиш съобщение на",
    "остави бележка за",
    "остави бележка на",
    "кажи на",
    "кажеш на",
    "предай съобщение на",
    "предай на",
    "предадеш на",
    "напиши съобщение на",
    "напиши на",
    "напишеш на",
    "изпрати съобщение на",
    "изпратиш съобщение на",
    "изпрати на",
    "изпратиш на",
    "съобщи на",
    "съобщиш на",
    "уведоми",
)

_WORD_ALIASES = {
    "кво": "какво",
    "къв": "какъв",
    "ква": "каква",
    "каде": "къде",
    "немам": "нямам",
    "расписание": "разписание",
    "собщение": "съобщение",
}


def normalize_text(value: str) -> str:
    """Normalize Bulgarian text without transliterating meaningful letters."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("ѝ", "и").replace("–", "-").replace("—", "-")
    text = re.sub(r"[^\w\s-]+", " ", text, flags=re.UNICODE)
    tokens = re.sub(r"[\s_]+", " ", text).strip().split()
    return " ".join(_WORD_ALIASES.get(token, token) for token in tokens)


def meaningful_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in normalize_text(value).split()
        if len(token) >= 3 and token not in _STOP_WORDS
    )


def _token_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    shorter = min(len(left), len(right))
    common_prefix = 0
    for left_character, right_character in zip(left, right):
        if left_character != right_character:
            break
        common_prefix += 1
    if shorter >= 5 and common_prefix >= min(5, shorter):
        return 0.92
    if shorter < 4:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def text_match_score(query: str, candidate: str) -> float:
    """Return query-token coverage suitable for small managed school datasets."""
    normalized_query = normalize_text(query)
    normalized_candidate = normalize_text(candidate)
    if not normalized_query or not normalized_candidate:
        return 0.0
    if normalized_candidate in normalized_query:
        return 1.0
    query_tokens = meaningful_tokens(normalized_query)
    candidate_tokens = meaningful_tokens(normalized_candidate)
    if not query_tokens or not candidate_tokens:
        return 0.0
    best_matches = [
        max(_token_similarity(query_token, candidate_token) for candidate_token in candidate_tokens)
        for query_token in query_tokens
    ]
    strong_matches = [score for score in best_matches if score >= 0.78]
    if not strong_matches:
        return 0.0
    return sum(strong_matches) / len(query_tokens)


def _has_phrase(query: str, phrases: Iterable[str]) -> bool:
    return any(normalize_text(phrase) in query for phrase in phrases)


def _has_term(tokens: tuple[str, ...], terms: Iterable[str]) -> bool:
    for token in tokens:
        for term in terms:
            normalized_term = normalize_text(term)
            if " " in normalized_term:
                continue
            if token.startswith(normalized_term) or _token_similarity(token, normalized_term) >= 0.82:
                return True
    return False


def _parse_message_action(raw_query: str) -> dict[str, Any] | None:
    """Parse write intent only after an explicit, non-fuzzy action phrase."""
    lowered = unicodedata.normalize("NFKC", raw_query).casefold().strip()
    for marker in _MESSAGE_ACTION_MARKERS:
        marker_match = re.search(
            rf"(?<!\w){re.escape(marker)}(?!\w)",
            lowered,
        )
        if marker_match is None:
            continue
        after = lowered[marker_match.end():].strip(" ,:-")
        parts = re.split(
            r"\s*(?:(?:,|:)\s*(?:че|да)\b|\b(?:че|да)\b|,|:)\s*",
            after,
            maxsplit=1,
        )
        if len(parts) == 2:
            recipient_name, message_text = parts
        else:
            words = after.split()
            recipient_name = " ".join(words[:2]) if len(words) >= 2 else after
            message_text = " ".join(words[2:]) if len(words) >= 3 else ""
        return {
            "intent": "leave_message",
            **_EMPTY_FIELDS,
            "recipient_name": recipient_name.strip() or None,
            "message_text": message_text.strip() or None,
        }
    return None


def _date_fields(query: str, reference_date: date) -> dict[str, Any]:
    tokens = tuple(query.split())
    offset = 0
    if _has_term(tokens, ("другиден", "вдругиден")):
        offset = 2
    elif _has_term(tokens, ("утре", "утреш")):
        offset = 1
    elif not _has_term(tokens, ("днес", "днеш")):
        for stem, weekday in _WEEKDAYS.items():
            if _has_term(tokens, (stem,)):
                offset = (weekday - reference_date.weekday()) % 7
                if offset == 0 and _has_term(tokens, ("следващ", "идващ")):
                    offset = 7
                break
    return {
        "date": "today" if offset == 0 else "tomorrow" if offset == 1 else "offset",
        "date_offset": offset,
        "range_days": 7
        if _has_term(tokens, ("седмиц", "предстоящ", "скоро"))
        or _has_phrase(query, ("следващите дни", "идните дни"))
        else 1,
    }


def _extract_period(query: str) -> int | None:
    numeric = re.search(
        r"\b(1[0-2]|[1-9])(?:\s*[-.]?\s*(?:ви|ри|ти|ми))?\s*(?:час|часа)\b",
        query,
    )
    if numeric:
        return int(numeric.group(1))
    for word, period in _PERIOD_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\s+(?:час|часа)\b", query):
            return period
    return None


def _extract_class_name(query: str) -> str | None:
    match = re.search(r"\b(?:клас(?:а)?\s*)?(\d{1,2})\s*[-.]?\s*([а-я])\b", query)
    return f"{match.group(1)}{match.group(2).upper()}" if match else None


def _schedule_scope(query: str, period: int | None) -> str:
    if period is not None:
        return "period"
    tokens = tuple(query.split())
    if _has_term(tokens, ("следващ", "после")):
        return "next"
    if _has_term(tokens, ("текущ", "сега")) or _has_phrase(query, ("в момента", "точно сега")):
        return "current"
    if _has_term(tokens, ("първи", "първия")):
        return "first"
    if _has_term(tokens, ("последен", "последния")):
        return "last"
    return "full"


def _room_target(query: str) -> str | None:
    number = re.search(
        r"\b(?:кабинет|стая|зала)?\s*([а-я]?\d{2,4}[а-я]?)\b",
        query,
    )
    if number:
        return number.group(1).upper()
    tokens = tuple(query.split())
    for aliases, canonical in _ROOM_ALIASES:
        if _has_phrase(query, aliases) or _has_term(tokens, aliases):
            return canonical
    return None


def parse_intent_rule_based(
    query: str,
    *,
    reference_date: date | None = None,
) -> dict[str, Any]:
    """Classify a school query and extract deterministic entities."""
    raw_query = str(query or "").strip()
    normalized = normalize_text(raw_query)
    tokens = tuple(normalized.split())
    parsed = {**_EMPTY_FIELDS, **_date_fields(normalized, reference_date or today_bg())}
    parsed["period"] = _extract_period(normalized)
    parsed["schedule_scope"] = _schedule_scope(normalized, parsed["period"])
    parsed["class_name"] = _extract_class_name(normalized)

    if not normalized:
        return {"intent": "unknown", **parsed}

    message_action = _parse_message_action(raw_query)
    if message_action:
        message_action.update({
            "date": parsed["date"],
            "date_offset": parsed["date_offset"],
            "range_days": parsed["range_days"],
        })
        return message_action

    if _has_phrase(
        normalized,
        (
            "какво можеш",
            "с какво можеш",
            "как да те питам",
            "какви въпроси",
            "покажи командите",
            "дай примери",
            "помогни ми",
        ),
    ) or _has_term(tokens, ("помощ", "помош", "команди")):
        return {"intent": "help", **parsed}

    if _has_phrase(
        normalized,
        (
            "кой съм аз",
            "как се казвам",
            "кой е профилът ми",
            "кой профил е активен",
            "в кой клас съм",
            "какъв ми е класът",
        ),
    ):
        return {"intent": "identify_person", **parsed}

    if _has_phrase(
        normalized,
        (
            "колко е часът",
            "колко е часа",
            "точен час",
            "каква дата сме",
            "коя дата сме",
            "кой ден сме",
            "днешната дата",
        ),
    ):
        return {"intent": "time_and_date", **parsed}

    if _has_phrase(
        normalized,
        (
            "как се казва училището",
            "кое е училището",
            "информация за училището",
            "за това училище",
        ),
    ):
        return {"intent": "school_info", **parsed}

    announcement_terms = ("обяв", "извест", "уведомлен", "важно")
    if (
        _has_term(tokens, announcement_terms)
        or any(token.startswith("новин") for token in tokens)
        or _has_phrase(
        normalized,
        ("училищни съобщения", "съобщения от училището"),
        )
    ):
        return {"intent": "show_announcements", **parsed}

    message_terms = ("съобщен", "писмо", "поща")
    if (
        _has_term(tokens, message_terms)
        or _has_phrase(
            normalized,
            (
                "някой писал ли ми е",
                "някой писа ли ми",
                "търсил ли ме е някой",
                "има ли нещо за мен",
                "имам ли нещо",
            ),
        )
    ):
        return {"intent": "check_messages", **parsed}

    if _has_term(tokens, ("заместв", "заместник")) or _has_phrase(
        normalized,
        ("смяна на учител", "отсъстващ учител", "кой ще води часа"),
    ):
        return {"intent": "check_substitutions", **parsed}

    if _has_term(tokens, ("дежур",)):
        return {"intent": "check_duties", **parsed}

    if _has_term(tokens, ("напомнян",)) or _has_phrase(
        normalized,
        ("какво да не забравя", "нещо за напомняне"),
    ):
        return {"intent": "check_reminders", **parsed}

    if _has_term(tokens, ("задач", "домашн", "срок")) or _has_phrase(
        normalized,
        ("какво трябва да предам", "какво трябва да направя"),
    ):
        return {"intent": "check_tasks", **parsed}

    if _has_term(tokens, ("клуб", "кръжок", "извънклас")) or _has_phrase(
        normalized,
        ("занимания по интереси",),
    ):
        return {"intent": "show_clubs", **parsed}

    if _has_term(tokens, ("събит", "мероприят", "концерт", "състезан", "празник", "изложб", "сбирк")) or _has_phrase(
        normalized,
        ("какво предстои", "какво ще има", "какво има днес", "какво има утре"),
    ):
        return {"intent": "show_events", **parsed}

    if _has_phrase(
        normalized,
        (
            "свободен час",
            "свободни часове",
            "кога нямам час",
            "кога нямам занятия",
            "дупка в програмата",
            "прозорец в програмата",
        ),
    ) or _has_term(tokens, ("дупка", "прозорец")):
        return {"intent": "check_free_periods", **parsed}

    timetable_context = (
        _has_term(tokens, ("разписан", "програм", "предмет", "урок", "занят"))
        or _has_phrase(
            normalized,
            (
                "какви часове",
                "какъв час",
                "какво имам",
                "какво ми е",
                "къде ми е",
                "къде имам",
                "в кой кабинет ми е",
                "в кой кабинет е",
                "кой кабинет е",
                "в коя стая е",
                "къде е часът по",
                "къде е урокът по",
                "следващия час",
                "следващият час",
            ),
        )
        or _has_term(tokens, ("час",))
        or parsed["period"] is not None
    )
    if timetable_context:
        return {"intent": "check_timetable", **parsed}

    directory_context = _has_term(
        tokens,
        (
            "телефон",
            "имейл",
            "email",
            "контакт",
            "адрес",
            "сайт",
            "приемно",
            "работно",
            "секретар",
            "канцелар",
        ),
    ) or _has_phrase(
        normalized,
        ("кой е директорът", "коя е директорката", "с кого да се свържа"),
    )
    if directory_context:
        return {"intent": "directory_lookup", **parsed}

    location_phrase = _has_phrase(
        normalized,
        (
            "къде е",
            "къде се намира",
            "как да стигна",
            "накъде е",
            "пътят до",
        ),
    )
    short_place_query = (
        len(tokens) <= 3
        and not _has_term(tokens, ("кога", "отвор", "работно"))
        and _has_term(
            tokens,
            (
                "библиот",
                "учителск",
                "дирекция",
                "стол",
                "лавка",
                "тоалет",
                "медицин",
                "психолог",
                "двор",
                "вход",
                "изход",
            ),
        )
    )
    room_context = _has_term(
        tokens,
        ("кабинет", "стая", "зала", "етаж", "крило"),
    ) or location_phrase or short_place_query
    if room_context:
        return {
            "intent": "check_room",
            **parsed,
            "room_number": _room_target(normalized),
        }

    greeting_terms = {
        "здравей",
        "здравейте",
        "здрасти",
        "привет",
        "добър",
        "добро",
        "добра",
        "ден",
        "утро",
        "вечер",
        "асистент",
    }
    if len(tokens) <= 5 and any(token in greeting_terms for token in tokens):
        return {"intent": "greeting", **parsed}

    return {"intent": "unknown", **parsed}
