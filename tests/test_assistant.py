"""Contracts for broad but deterministic Bulgarian rule-based intent parsing."""

from __future__ import annotations

import unittest
from datetime import date

from web.services.assistant_rules import (
    parse_intent_rule_based,
    text_match_score,
)


class TestAssistantRuleParsing(unittest.TestCase):
    def test_broad_school_query_variants_map_to_read_only_intents(self):
        examples = {
            "Какво имам сега?": "check_timetable",
            "кво имам утре": "check_timetable",
            "Имам ли часове утре?": "check_timetable",
            "Къде ми е математиката?": "check_timetable",
            "Имам ли някакви лични съобщения?": "check_messages",
            "Имам ли нещо?": "check_messages",
            "Какви училищни новини има?": "show_announcements",
            "Има ли замествания утре?": "check_substitutions",
            "Къде съм дежурен?": "check_duties",
            "Покажи напомнянията ми": "check_reminders",
            "Какво трябва да предам?": "check_tasks",
            "Какви клубове има?": "show_clubs",
            "Какво предстои тази седмица?": "show_events",
            "Как да стигна до библиотеката?": "check_room",
            "Какъв е телефонът на секретариата?": "directory_lookup",
            "Кой съм аз?": "identify_person",
            "Колко е часът?": "time_and_date",
            "помош": "help",
        }

        for query, expected in examples.items():
            with self.subTest(query=query):
                self.assertEqual(
                    parse_intent_rule_based(query)["intent"],
                    expected,
                )

    def test_write_intent_requires_a_separate_explicit_action_phrase(self):
        reminder = parse_intent_rule_based("Покажи напомнянията ми")
        message = parse_intent_rule_based(
            "Може ли да кажеш на госпожа Мария Димитрова, че ще закъснея?"
        )
        explicit_message = parse_intent_rule_based(
            "Изпрати съобщение на Георги Петров: чакам те пред входа"
        )

        self.assertEqual(reminder["intent"], "check_reminders")
        self.assertEqual(message["intent"], "leave_message")
        self.assertEqual(
            message["recipient_name"],
            "госпожа мария димитрова",
        )
        self.assertEqual(message["message_text"], "ще закъснея?")
        self.assertEqual(explicit_message["intent"], "leave_message")
        self.assertEqual(
            explicit_message["message_text"],
            "чакам те пред входа",
        )

    def test_date_class_period_and_scope_entities_are_extracted(self):
        parsed = parse_intent_rule_based(
            "Какъв е третият час на 9Б утре?",
            reference_date=date(2026, 7, 27),
        )

        self.assertEqual(parsed["intent"], "check_timetable")
        self.assertEqual(parsed["date_offset"], 1)
        self.assertEqual(parsed["class_name"], "9Б")
        self.assertEqual(parsed["period"], 3)
        self.assertEqual(parsed["schedule_scope"], "period")

    def test_weekday_and_upcoming_range_are_deterministic(self):
        parsed = parse_intent_rule_based(
            "Какви събития има следващия понеделник?",
            reference_date=date(2026, 7, 27),
        )
        upcoming = parse_intent_rule_based(
            "Какво предстои тази седмица?",
            reference_date=date(2026, 7, 27),
        )

        self.assertEqual(parsed["date_offset"], 7)
        self.assertEqual(upcoming["range_days"], 7)

    def test_managed_knowledge_matching_accepts_inflection_and_small_typo(self):
        self.assertGreaterEqual(
            text_match_score(
                "Кога отваря библиотеката?",
                "Работно време на библиотека",
            ),
            0.45,
        )
        self.assertGreaterEqual(
            text_match_score(
                "телфон на секретариата",
                "Телефон за контакт със секретариат",
            ),
            0.45,
        )


if __name__ == "__main__":
    unittest.main()
