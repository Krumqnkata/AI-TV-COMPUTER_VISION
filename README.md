# School AI — QR Badge Assistant

Училищна информационна система с QR баджове, FastAPI backend, cross-platform
киоск/екран PWA и единен български SQLAdmin контролен център. Активната версия
не използва лицево разпознаване, mood detection или друга биометрия.

## Архитектура

```text
tablet/Windows kiosk ── HttpOnly profile credential ──► FastAPI ──► PostgreSQL
        │                    │                              │
 local QR camera       WebSocket + retry ACK               │
        │                    ▼                              │
        └────────► paired/public `/screen` PWA ◄────────────┘
                                   │
staff browser ◄──────── RBAC + CSRF SQLAdmin control centre
```

- `web/routers/` съдържа HTTP и WebSocket договорите;
- `web/services/` съдържа business logic за QR, delivery, RBAC, devices, imports, privacy и backups;
- `web/admin/` съдържа permission-aware административните views;
- `web/static/pwa/` и `web/templates/pwa/` съдържат общия offline-capable
  клиент за `/pair`, `/kiosk` и `/screen`;
- `engine/` съдържа SQLAlchemy моделите и security primitives;
- `migrations/` е единственият източник за database schema;
- `client_qr_node.py` е клиентът за камера, QR и локален говор;
- `tools/` съдържа admin bootstrap, device simulator, audited maintenance,
  reconnect baseline и защитена PostgreSQL restore проверка.

## Изисквания

- Python 3.11 или 3.12;
- Windows за предоставените `.bat` launchers или Linux със `systemd`;
- PostgreSQL 18 с Command Line Tools за runtime базата и backups;
- HTTPS адрес за LAN киоски/екрани (камерата и PWA инсталацията не работят от
  обикновен `http://192.168...`; `localhost` е допустим само за development);
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
- liveness: `http://localhost:5000/health/live`
- readiness (база, migrations и background monitor):
  `http://localhost:5000/health/ready`
- Prometheus-compatible process metrics:
  `http://localhost:5000/health/metrics`

Локалната конзола използва компактен четим формат и пропуска успешните
статични asset заявки. Пълният поток остава като JSON Lines в
`logs/system.log`; `LOG_FORMAT=json` включва JSON и за stdout в production.

Runtime базата, imports и backups не се проследяват от Git. Преди migration на съществуваща инсталация винаги създавайте проверено резервно копие.

Linux production deployment без Docker е описан в
`docs/LINUX_DEPLOYMENT.md`. Предоставени са `run.sh`, hardened `systemd`
service, Nginx WebSocket/TLS шаблон и logrotate правило.
Автоматичните backup/retention задачи се изпълняват от отделен persistent
`systemd` timer и оставят audit запис за всяко изпълнение.

## Киоск и информационен екран PWA

Един и същ локално хостван web клиент работи на Android таблет, Windows PC и
съвременен браузър. Има два отделно инсталируеми профила:

- `/kiosk` — локална QR камера, лична сесия, следващ час, съобщения, текстов
  асистент и включван от потребителя browser TTS;
- `/screen` — публични обяви, събития и замествания. В режим **Сдвоен** показва
  и таргетирания личен резултат от киоск със същите `screen_id`/`zone_id`.

Сдвояване:

1. В **Устройства → Интерактивни точки** създайте точка със `zone_id` и
   `screen_id`.
2. В **Устройства → Управление на устройства** изберете „Киоск“ или „Екран“,
   точката и нужния режим.
3. Отворете защитения училищен адрес `/pair?profile=kiosk` или
   `/pair?profile=screen` на устройството.
4. Сканирайте еднократния QR код от админ панела. Индивидуалният ключ се записва
   като `HttpOnly`, `SameSite=Strict` cookie и не попада в URL, JavaScript
   storage или log.
5. Използвайте бутона **Инсталирай** в Chrome/Edge/поддържан mobile browser или
   оставете клиента да работи като обикновена responsive web страница.

Двата manifest файла имат различни PWA `id` и `start_url`, затова `/kiosk` и
`/screen` могат да бъдат инсталирани отделно на един Windows компютър. Firefox
desktop остава напълно използваем browser fallback, дори когато не предлага
manifest-based инсталация.

Rule-based асистентът е default и покрива разписание, предмети и свободни
часове, помещения, събития, обяви, клубове, замествания, дежурства, задачи,
напомняния, контакти и лични съобщения. Училищно-специфични отговори могат да
се добавят без код от **Съдържание → Училищен указател и FAQ**.

Пълната настройка, browser матрицата, физическият pilot и troubleshooting са в
[`docs/KIOSK_PWA.md`](docs/KIOSK_PWA.md).

Техническото състояние на всеки сдвоен клиент се вижда в **Устройства →
Диагностика**. Там се показват heartbeat, текуща WebSocket връзка, последно
свързване/разкачване, camera permission/status, безопасен browser capability
snapshot и чакащи command ACK. Background monitor отбелязва остарелите
устройства offline на всеки 15 секунди, независимо дали админ панелът е
отворен. От **Управление на устройства** могат безопасно да се изпратят
pause/resume, config reload, connectivity/diagnostics, PWA update/cache и
camera/audio/screen test команди. Командата се известява веднага по WebSocket
и остава трайно pending до ACK.

## Python QR node fallback

`client_qr_node.py` остава за Mini PC, Raspberry Pi или отделна USB/IP камера,
но не е основният tablet клиент. Стартиране:

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

### AI доставчици

Rule-based режимът е default и не изисква външна услуга. След инсталиране на
`requirements-ai.txt` потребител с право `assistant.manage` може от **Система →
Настройки → AI асистент** да избере Gemini или Ollama, да зададе отделен модел
за всеки доставчик и да запише криптирания ключ. Бутонът **Тествай избрания
доставчик** изпраща минимална служебна заявка без училищен контекст.

Същата страница показва само безопасен runtime статус: последен успех/тип
грешка, latency, заявки за последната минута и circuit breaker. Външните заявки
имат общ timeout, rate limit и временно се спират след конфигуриран брой
последователни грешки. Агрегираните резултати са налични и в
`/health/metrics`, без ключове, въпроси, отговори или person/device labels.

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

За browser acceptance:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
$env:RUN_BROWSER_E2E='1'
$env:PLAYWRIGHT_BROWSER='chromium'
$env:PWA_E2E_MODE='full'
.\.venv\Scripts\python.exe -m unittest tests.test_pwa_browser -v
```

GitHub Actions изпълнява Python 3.11/3.12 и PostgreSQL 18 тестовете, dependency
audit, CodeQL, пълен Chromium PWA acceptance и smoke проверки с Firefox,
WebKit и Microsoft Edge на Windows. Настройката и required checks са описани в
`docs/CI.md`.

Допълнителни документи:

- `ADMIN_GUIDE.md` — ежедневна работа в панела;
- `docs/WINDOWS_POSTGRESQL.md` — локална PostgreSQL инсталация, тестове и restore;
- `docs/LINUX_DEPLOYMENT.md` — production инсталация без Docker;
- `docs/OPERATIONS_RUNBOOK.md` — наблюдение, restart, backup/restore и устройства;
- `docs/KIOSK_PWA.md` — pairing, инсталация, режими, сигурност и pilot;
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
