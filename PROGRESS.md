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
- Добавен е единен светъл български SQLAdmin контролен център с responsive Jinja интерфейс и локално vendor-нат HTMX.
- Добавени са отделни служебни профили, 4 системни роли, permission-aware menus/actions, login lockout и административен audit trail.
- Добавени са типизирани оперативни настройки и AES-GCM криптирани AI тайни; deployment границата остава извън панела.
- Добавени са класове/групи, помещения, обяви, клубове, замествания, дежурства, задачи, напомняния, указател и кампании.
- Добавен е CSV/XLSX импорт на разписание с preview, редови грешки, upsert/replace-range и история.
- Добавени са индивидуално device enrollment/credentials, heartbeat, config version, safe commands и command ACK.
- Добавени са retention preview/cleanup, архивиране при ръчно изтриване и проверими SQLite backups.
- Добавена е additive Alembic миграция, която пази съществуващите prototype таблици.

## Проверено

- 44+ Python файла са синтактично валидни.
- 18 интеграционни теста покриват старите API договори и новия control plane.
- Покрити са RBAC foundation, encrypted settings, login lockout, device enrollment/heartbeat/config/command ACK, import, retention, backup, CSRF, Argon2, targeted WebSocket и delivery ACK.

## Следващи етапи от TASK.md

1. Реален Whisper/STT аудио endpoint.
2. LLM intent parsing с ограничен DB контекст и rule-based fallback.
3. PostgreSQL и Redis преди multi-worker/LAN внедряване.
4. Реални тестове с две или повече физически камери и kiosk screens.
5. Deployment процедура за контролирано възстановяване от backup при спрян сървър.
