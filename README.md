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
- `tools/` съдържа admin bootstrap и device simulator.

## Изисквания

- Python 3.11 или 3.12;
- Windows за предоставените `.bat` launchers; server кодът е platform-independent;
- SQLite за единична инсталация.

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

2. Генерирайте различни силни стойности за `ADMIN_SECRET_KEY` и `SETTINGS_MASTER_KEY`. Не ги commit-вайте и не използвайте примерните placeholders. Останалите настройки в `.env.example` са коментирани — активирайте само тези, които наистина променят default поведението.

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
- CSV/XLSX imports, retention cleanup и проверими SQLite backups.

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
```

Тестовете създават временна SQLite база чрез целия Alembic chain. Те не четат, изтриват или променят runtime базата.

Допълнителни документи:

- `ADMIN_GUIDE.md` — ежедневна работа в панела;
- `TASK.md` — активен roadmap;
- `docs/archive/` — неактуални исторически спецификации.
