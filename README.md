# School AI — QR Badge Assistant

Училищна информационна система с QR баджове, FastAPI backend, индивидуално управлявани camera/kiosk nodes и единен български SQLAdmin контролен център. Активната версия не използва лицево разпознаване, mood detection или друга биометрия.

## Архитектура

```text
QR camera node ── device ID/key ──► FastAPI routers ──► services ──► database
                                             │
screen/kiosk ◄──── WebSocket + delivery ACK ─┘
                                             │
staff browser ◄──── RBAC + CSRF ───── SQLAdmin control centre
```

- `web/routers/` съдържа HTTP и WebSocket договорите;
- `web/services/` съдържа business logic за QR, delivery, RBAC, devices, imports, privacy и backups;
- `web/admin/` съдържа permission-aware административните views;
- `engine/` съдържа SQLAlchemy моделите и security primitives;
- `migrations/` е единственият източник за database schema;
- `client_qr_node.py` е клиентът за камера, QR и локален говор;
- `tools/` съдържа admin bootstrap, device simulator и защитена PostgreSQL
  restore проверка.

## Изисквания

- Python 3.11 или 3.12;
- Windows за предоставените `.bat` launchers или Linux със `systemd`;
- PostgreSQL 18 с Command Line Tools за runtime базата и backups;
- SQLite остава само за бързите изолирани unit/integration fixtures.

Зависимостите са разделени по роля:

```powershell
# Централен сървър
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Development и тестове
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# QR camera node на отделната машина
.\.venv\Scripts\python.exe -m pip install -r requirements-node.txt

# Optional Gemini/Ollama providers върху server инсталацията
.\.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
```

## Първоначална настройка

1. Създайте локалната конфигурация:

```powershell
Copy-Item .env.example .env.local
```

2. Създайте локалните `school_ai_dev` и `school_ai_test` PostgreSQL бази според `docs/WINDOWS_POSTGRESQL.md`. Генерирайте различни силни стойности за `ADMIN_SECRET_KEY`, `SETTINGS_MASTER_KEY` и database паролата. Не commit-вайте `.env.local` и не използвайте примерните placeholders.

3. Създайте или обновете schema-та само чрез Alembic:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

Сървърът не създава таблици автоматично. При стара или празна база отказва старт с ясна инструкция за миграция.

4. Създайте първия администратор. Паролата се въвежда скрито и не остава в shell history:

```powershell
.\.venv\Scripts\python.exe tools\create_admin.py --name "Администратор" --username admin
```

5. Стартирайте:

```powershell
.\run.bat
# или
.\.venv\Scripts\python.exe main.py
```

- приложение: `http://localhost:5000/`
- администрация: `http://localhost:5000/admin`
- OpenAPI: `http://localhost:5000/docs`

Runtime базата, imports и backups не се проследяват от Git. Преди migration на съществуваща инсталация винаги създавайте проверено резервно копие.

Linux production deployment без Docker е описан в
`docs/LINUX_DEPLOYMENT.md`. Предоставени са `run.sh`, hardened `systemd`
service, Nginx WebSocket/TLS шаблон и logrotate правило.

## Устройства

1. В админ панела отворете **Устройства → Управление на устройства**.
2. Създайте кратък еднократен enrollment код за конкретната зона/екран.
3. На устройството задайте `DEVICE_ID` и `DEVICE_ENROLLMENT_TOKEN`.
4. При първото стартиране запазете върнатия `DEVICE_KEY` в локалната конфигурация.
5. След сдвояване изчистете enrollment token-а.

Стартиране на реалния QR node:

```powershell
.\run-node.bat
```

Smoke simulator без камера:

```powershell
.\.venv\Scripts\python.exe tools\simulate_nodes.py `
  --device-id test-screen-01 `
  --enrollment-token ONE_TIME_TOKEN
```

Simulator-ът използва индивидуални credentials, изпраща heartbeat, чете конфигурацията и може да ACK-ва pending команди. Legacy shared key се допуска само чрез изричен `--legacy-key`.

## Административен контрол

Панелът управлява:

- хора, QR баджове, класове, групи и помещения;
- разписание, събития, обяви, клубове, замествания и задачи;
- служебни профили, четири роли, permissions и audit trail;
- типизирани runtime настройки и AES-GCM encrypted AI secrets;
- device enrollment, heartbeat, config, safe commands и ACK;
- CSV/XLSX imports, retention cleanup и проверими PostgreSQL backups чрез `pg_dump`/`pg_restore`.

Deployment настройки като database URL, TLS, session/master keys и database restore не се променят от панела.

## Сигурност

- Argon2 за пароли и login lockout след поредица неуспешни опити;
- double-submit CSRF за browser mutations;
- SHA-256 отпечатъци за QR tokens и device credentials;
- scope по device, camera, screen и zone;
- личните съобщения се доставят таргетирано и се маркират едва след ACK;
- encrypted secrets не се показват обратно след запис;
- няма shell команди или OS restart от admin/device control plane.

`DEVICE_API_KEY` е временен compatibility fallback. След сдвояване на всички устройства го изключете от панела и после го премахнете от deployment конфигурацията.

## Тестове

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
```

Основният suite създава временна SQLite база и не докосва runtime PostgreSQL базата. Допълнителният live PostgreSQL migration test използва само `POSTGRES_TEST_DATABASE_URL` и отказва база, чието име не завършва на `_test`. Подробностите са в `docs/WINDOWS_POSTGRESQL.md`.

GitHub Actions изпълнява тестовете на Python 3.11/3.12 с PostgreSQL 18,
dependency audit и CodeQL. Настройката и required checks са описани в
`docs/CI.md`.

Допълнителни документи:

- `ADMIN_GUIDE.md` — ежедневна работа в панела;
- `docs/WINDOWS_POSTGRESQL.md` — локална PostgreSQL инсталация, тестове и restore;
- `docs/LINUX_DEPLOYMENT.md` — production инсталация без Docker;
- `docs/CI.md` — GitHub Actions и branch protection checks;
- `docs/SECURITY.md` — dependency audit правила и временни изключения;
- `TASK.md` — активен roadmap;
- `docs/archive/` — неактуални исторически спецификации.

## Лиценз

Copyright © 2026 Krumqnkata. Всички права са запазени.

Това е публично видим, но не и open-source проект. Използване, стартиране,
внедряване, копиране, промяна или разпространение на оригиналния код се допуска
само след предварително писмено разрешение от притежателя и при задължително
посочване на автора, освен в границите на права, които не могат да бъдат
ограничени от приложимото право:

> School AI — original project by Krumqnkata
>
> https://github.com/Krumqnkata/AI-TV-COMPUTER_VISION

Преглеждането и fork-ването през GitHub остават предмет на GitHub Terms of
Service. Third-party компонентите запазват собствените си лицензи. Пълните
условия на български и английски език, включително приложимото българско право
и правилата за бъдещи версии, са в [`LICENSE`](LICENSE) на български и английски език.
