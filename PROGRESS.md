# Прогрес по School AI

**Последна проверка:** 22 юли 2026 г.

## Завършено

- Възстановени са REST контрактите за persons, timetable, events, badges, messages, cameras и interaction points, премахнати в commit `e21f25e`.
- SQLAdmin панелът е запазен и отделен в `web/admin_panel.py`.
- `web/server.py` е намален до application wiring; routes и business logic са разделени в `web/routers/` и `web/services/`.
- Device endpoints използват `X-Device-Key`; browser POST/PUT/PATCH/DELETE използват реална double-submit CSRF проверка.
- Паролите са унифицирани на Argon2. SQLAdmin вече не записва bcrypt хешове, които login модулът не може да провери.
- WebSocket screens се регистрират с `screen_id` и `zone_id`; личните събития не се broadcast-ват глобално.
- Добавен е `DeliveryReceipt`: pending съобщенията се маркират като delivered само след WebSocket/HTTP acknowledgment.
- Тестовете използват временна SQLite база и не докосват production/demo базата.
- Dependency стекът е pin-нат и `pip check` приключва без конфликти.
- Премахнати са legacy face-recognition, UI/people-counter/state-manager модулите и техните тежки зависимости.

## Проверено

- 30 Python файла са синтактично валидни.
- 9 интеграционни теста преминават успешно.
- Покрити са device auth, CSRF, Argon2 login, възстановени endpoints, session locking, identity validation, targeted WebSocket и delivery ACK.

## Следващи етапи от TASK.md

1. Реален Whisper/STT аудио endpoint.
2. LLM intent parsing с ограничен DB контекст и rule-based fallback.
3. CSV/Excel импорт на разписание.
4. PostgreSQL и Redis при multi-worker/LAN внедряване.
5. Отделни ротационни ключове за всяко устройство вместо един споделен prototype key.
6. Реални тестове с две или повече физически камери и kiosk screens.
