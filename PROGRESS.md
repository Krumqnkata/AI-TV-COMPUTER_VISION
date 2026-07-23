# Прогрес по School AI

**Последна проверка:** 23 юли 2026 г.

## Текуща стабилна основа

- QR badge backend с възстановени persons, badges, timetable, events, messages, cameras и interaction-points API договори.
- Малък FastAPI composition root с отделни routers, services и permission-aware SQLAdmin views.
- Argon2, double-submit CSRF, служебни профили, четири роли, login lockout и административен audit.
- Таргетирани WebSocket връзки по device/screen/zone и delivery acknowledgment.
- Индивидуален device enrollment, heartbeat, config, safe commands и command ACK.
- Управление на учебно съдържание, CSV/XLSX import, retention cleanup и database-aware PostgreSQL/SQLite backups.
- Alembic-only database bootstrap с legacy baseline; startup не изпълнява `create_all()` и import-ът не променя базата.
- Runtime DB, imports и backups са извън Git; production demo seed-ът е преместен в test fixtures.
- Разделени server, QR-node, optional-AI и development dependency профили.
- Премахнати са legacy face/mood/Piper/jokes/font assets, неизползваните config полета и старият audio/cache/logger поток.
- Оригиналната спецификация е архивирана; root `TASK.md` е единственият активен roadmap.
- GitHub Actions покрива Python 3.11/3.12, PostgreSQL 18, dependency audit и
  CodeQL; Dependabot следи Python и Actions pins.
- Подготвен е Linux deployment без Docker със hardened systemd service,
  Nginx WebSocket/TLS proxy, logrotate и production env шаблон.

## Проверено

- 51 теста минават, включително live PostgreSQL migration върху отделната
  `school_ai_test` база.
- 54 Python файла преминават `compileall` syntax проверка.
- Покрити са fresh migration, legacy upgrade, вече stamped database и отказ при outdated schema.
- Покрити са REST, CSRF, Argon2, RBAC, encrypted secrets, device lifecycle, WebSocket targeting, imports, privacy и backups.
- Simulator contract тестовете изискват индивидуални credentials по подразбиране.
- Dependency contract тестовете гарантират pinned и разделени профили без Piper и `httpx2`.
- Реален PostgreSQL backup е създаден с `pg_dump`, проверен с
  `pg_restore --list` и успешно възстановен в disposable `_test` база: 39 таблици на
  Alembic revision `20260722_01`.
- Server, development и optional-AI profiles нямат известни advisories.
  QR-node профилът има едно ограничено и документирано gTTS/Click изключение.
- GitHub workflow файловете минават `actionlint 1.7.12`.

Оставащата продуктова работа е описана само в `TASK.md`.
