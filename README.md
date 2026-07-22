# School AI — QR Badge Assistant

Локален училищен асистент с QR баджове, FastAPI backend и единен български SQLAdmin контролен център. От панела се управляват хора, класове, разписание, съдържание, служебни роли, устройства, безопасни настройки, retention и SQLite backups. Активната версия не използва лицево разпознаване или биометрия.

## Архитектура

```text
QR/client node ── X-Device-ID + X-Device-Key ──► FastAPI routers ──► services ──► SQLite
                                           │
                                           └──► WebSocket screen по screen_id/zone_id
                                                      │
                                                      └── delivery ACK
```

Основни модули:

- `web/server.py` — composition root на приложението;
- `web/routers/` — device, administrative и system/WebSocket маршрути;
- `web/services/` — QR, сесии, import, RBAC, settings, devices, privacy и backups;
- `web/admin_panel.py` — малък SQLAdmin composition root;
- `web/admin/` — authentication, permission-aware models и guided workflows;
- `web/templates/admin/` и `web/static/admin/` — светъл responsive Jinja/HTMX интерфейс;
- `web/security.py` — CSRF, device authentication и session права;
- `engine/db.py` и `engine/admin_models.py` — основни и административни SQLAlchemy модели;
- `migrations/` — additive Alembic migration за контролния център;
- `client_qr_node.py` — локално OpenCV QR разпознаване и комуникация със сървъра.

## Инсталация

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env.local
```

В `.env.local` задайте уникални стойности за deployment границата:

- `ADMIN_SECRET_KEY` — подписване на административната session cookie;
- `SETTINGS_MASTER_KEY` — криптиране на AI ключовете, записвани през панела;
- `DEVICE_API_KEY` — временен общ ключ само докато старите устройства бъдат сдвоени индивидуално;
- `CAMERA_ID`, `ZONE_ID` и `SCREEN_ID` — идентичността на конкретната точка.

Създаване или актуализиране на администратор:

```powershell
.venv\Scripts\python.exe tools\create_admin.py --name "Администратор" --username "admin" --password "силна-парола"
```

При съществуваща база изпълнете additive миграцията (тя не променя старите таблици):

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
```

## Стартиране

```powershell
.venv\Scripts\python.exe main.py
```

- SQLAdmin: `http://localhost:5000/admin`
- API документация: `http://localhost:5000/docs`

Препоръчително сдвояване на устройство:

1. Влезте в `Админ → Устройства` и създайте еднократен код.
2. На устройството задайте `DEVICE_ID` и еднократно `DEVICE_ENROLLMENT_TOKEN`.
3. Стартирайте клиента и запазете показания `DEVICE_KEY` в локалната deployment конфигурация.
4. Премахнете `DEVICE_ENROLLMENT_TOKEN`.

За browser kiosk първоначалният URL може да съдържа индивидуалните данни еднократно:

```text
http://localhost:5000/?device_id=kiosk-main-01&device_key=<DEVICE_KEY>&screen_id=SCR-ENTRANCE-01&zone_id=MAIN_ENTRANCE
```

Ключът се премахва от адресната лента и остава само в `sessionStorage` за текущия browser tab.

Стартиране на QR node:

```powershell
.venv\Scripts\python.exe client_qr_node.py
```

## Сигурност

- Управляваните device маршрути изискват индивидуални `X-Device-ID` и `X-Device-Key`; общият ключ е изключваем compatibility режим.
- WebSocket връзката се удостоверява и регистрира по `device_id`, `screen_id` и `zone_id`.
- Browser mutation заявките използват double-submit CSRF cookie + `X-CSRF-Token`.
- Служебните профили са отделни от ученици/учители, с роли, права, 8-часова сесия и временно заключване след 5 грешни опита.
- Административните пароли се хешират и проверяват само с Argon2.
- AI ключовете са AES-GCM криптирани и никога не се показват обратно в панела.
- QR токените се съхраняват като SHA-256 отпечатъци; новите токени използват 128 бита случайност.
- Персоналните WebSocket събития се изпращат само към съответния екран/зона.
- Съобщение се маркира като доставено едва след ACK от screen или QR node.

За реално LAN внедряване използвайте HTTPS, `COOKIE_SECURE=true`, постоянни `ADMIN_SECRET_KEY`/`SETTINGS_MASTER_KEY` и Redis преди multi-worker режим. `DATABASE_URL`, TLS и master/session ключовете умишлено не се редактират от панела.

Кратко ръководство за ежедневна работа има в [ADMIN_GUIDE.md](ADMIN_GUIDE.md).

## Тестове

```powershell
.venv\Scripts\python.exe -m unittest tests.test_api -v
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m alembic upgrade head --sql
```

Тестовете създават собствена база в системната временна директория. Не отварят, не изтриват и не променят `data/school_ai.db`.
