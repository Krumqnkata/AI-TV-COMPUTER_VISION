# Прогрес по School AI

**Последна проверка:** 28 юли 2026 г.

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
- Реализирани са отделно инсталируеми `/kiosk` и `/screen` PWA профили върху
  общ responsive web клиент, плюс `/pair` pairing поток.
- PWA credentials се пазят в отделни `HttpOnly`, `SameSite=Strict` cookies;
  zone/screen/camera scope-ът се извежда само от server-side device record.
- QR се обработва локално чрез native `BarcodeDetector` и pinned локален
  `@zxing/browser` fallback, без camera video или runtime CDN заявка.
- `/screen` има public и paired режим, audience-filtered feed, отделна
  WebSocket регистрация и изолация на personal payload от public screens.
- Delivery ACK има минимален IndexedDB retry queue, deduplication по event ID и
  offline/online liveness reconnect.
- Админ панелът управлява pairing QR, физическа точка, режим, аудитория,
  ротация, яркост, idle timeout, heartbeat и безопасни команди.
- Добавени са `/health/live` и dependency-aware `/health/ready`, които
  проверяват база, Alembic revision и operations monitor.
- Фонов operations monitor отбелязва offline устройства на всеки 15 секунди,
  без отваряне на админ панела.
- Админ диагностиката обединява heartbeat, текущ/последен WebSocket, camera и
  privacy-safe browser capabilities, command ACK и предупреждения за
  heartbeat, delivery/command ACK и стар backup.
- Пълният файлов и production log е JSON Lines с request/correlation ID,
  device ID и редуциран `error_type`; локалната конзола е компактна и скрива
  успешните статични asset заявки. Query/body, credentials и лични съобщения
  не се записват.
- `/health/metrics` предоставя агрегирани HTTP error/latency, WebSocket,
  offline-device, QR, delayed-ACK и bounded AI provider/outcome/latency метрики,
  а Nginx шаблонът го ограничава до локален monitoring.
- Disk space и failed/stale maintenance задачите се показват като operational
  warnings. Daily persistent systemd timer изпълнява audited backup и
  retention jobs.
- Device control-ът известява точния client веднага по WebSocket и поддържа
  diagnostics/connectivity, PWA update/cache, pause/resume, reload и безопасни
  локални тестове с траен ACK.
- Rule-based асистентът разпознава широк набор български формулировки,
  склонения и малки печатни/STT грешки за всички управлявани училищни справки.
  Активните записи в „Училищен указател и FAQ“ служат като разширяема
  knowledge база без промяна на кода.
- Gemini и Ollama имат отделни модели, encrypted keys, безопасен connection
  test, timeout, rate limit, circuit breaker и privacy-safe runtime статус.
- Киоскът предлага role-based готови въпроси и FAQ, а TTS има bg-BG избор,
  ръчно прочитане/спиране и забрана за автоматично четене на лични отговори.
- Добавени са 100-device/5-round reconnect baseline и operational runbook за
  restart, backup, restore, lost key и offline tablet.
- `main` има branch protection със strict required Python, dependency, PWA и
  CodeQL checks, linear history и забранени force push/delete.
- Operational пакетът и дистанционният application control са merge-нати в
  `main` чрез PR #10 при зелени required checks.

## Проверено

- 85 теста се откриват локално: 83 минават, а live PostgreSQL и browser
  acceptance са коректно opt-in и са пропуснати в обикновения SQLite run.
- Отделният пълен Chromium acceptance минава през pairing, public feed, QR
  session, targeted delivery, remote command wake-up/ACK, storage privacy,
  idle cleanup, reconnect и Service Worker. Windows Microsoft Edge smoke също
  минава локално.
- CI е разширен с Chromium full acceptance и Firefox, WebKit и Windows Edge
  smoke jobs.
- 73 Python файла преминават syntax проверка, а петте PWA JavaScript файла
  преминават `node --check`.
- Покрити са fresh migration, legacy upgrade, вече stamped database и отказ при outdated schema.
- Покрити са REST, CSRF, Argon2, RBAC, encrypted secrets, device lifecycle, WebSocket targeting, imports, privacy и backups.
- Simulator contract тестовете изискват индивидуални credentials по подразбиране.
- Dependency contract тестовете гарантират pinned и разделени профили без Piper и `httpx2`.
- Реален PostgreSQL backup е създаден с `pg_dump`, проверен с
  `pg_restore --list` и успешно възстановен в disposable `_test` база: 39 таблици на
  Alembic revision `20260722_01`.
- Новата migration верига достига единствен head `20260728_01`; отделният live
  PostgreSQL `_test` migration test минава с 39 application таблици.
- Server, development (включително Playwright 1.61.0) и optional-AI profiles
  нямат известни advisories.
  QR-node профилът има едно ограничено и документирано gTTS/Click изключение.
- GitHub workflow файловете минават `actionlint 1.7.12`.

Софтуерната част на Kiosk PWA v1 и минималният operational пакет са завършени.
Остават физическият 2 MP camera proof of concept, device-owner/MDM
заключването, петдневният pilot и реалният училищен TLS/PostgreSQL rollout; те
са описани само в `TASK.md`.
