# School AI — QR Badge Assistant

Локален училищен асистент с QR баджове, централизиран FastAPI backend, SQLAdmin панел и интерактивни екрани по зони. Активната версия не използва лицево разпознаване или биометрия.

## Архитектура

```text
QR client node ── X-Device-Key ──► FastAPI routers ──► services ──► SQLite/PostgreSQL
                                           │
                                           └──► WebSocket screen по screen_id/zone_id
                                                      │
                                                      └── delivery ACK
```

Основни модули:

- `web/server.py` — composition root на приложението;
- `web/routers/` — device, administrative и system/WebSocket маршрути;
- `web/services/` — QR, сесии, intent parsing и delivery acknowledgment;
- `web/admin_panel.py` — SQLAdmin конфигурация;
- `web/security.py` — CSRF, device authentication и session права;
- `engine/db.py` — SQLAlchemy модели;
- `client_qr_node.py` — локално OpenCV QR разпознаване и комуникация със сървъра.

## Инсталация

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env.local
```

В `.env.local` задайте уникални стойности за:

- `ADMIN_SECRET_KEY` — подписване на административната session cookie;
- `DEVICE_API_KEY` — споделен ключ само между сървъра, QR nodes и kiosk screens;
- `CAMERA_ID`, `ZONE_ID` и `SCREEN_ID` — идентичността на конкретната точка.

Създаване или актуализиране на администратор:

```powershell
.venv\Scripts\python.exe tools\create_admin.py --name "Администратор" --password "силна-парола"
```

## Стартиране

```powershell
.venv\Scripts\python.exe main.py
```

- SQLAdmin: `http://localhost:5000/admin`
- API документация: `http://localhost:5000/docs`

При първо конфигуриране на kiosk screen отворете:

```text
http://localhost:5000/?device_key=<DEVICE_API_KEY>&screen_id=SCR-ENTRANCE-01&zone_id=MAIN_ENTRANCE
```

Ключът се премахва от адресната лента и остава само в `sessionStorage` за текущия browser tab.

Стартиране на QR node:

```powershell
.venv\Scripts\python.exe client_qr_node.py
```

## Сигурност

- Device REST маршрутите изискват `X-Device-Key`.
- WebSocket връзката трябва първо да изпрати валидна регистрация с `screen_id` и `zone_id`.
- Browser mutation заявките използват double-submit CSRF cookie + `X-CSRF-Token`.
- Административните пароли се хешират и проверяват само с Argon2.
- QR токените се съхраняват като SHA-256 отпечатъци; новите токени използват 128 бита случайност.
- Персоналните WebSocket събития се изпращат само към съответния екран/зона.
- Съобщение се маркира като доставено едва след ACK от screen или QR node.

За реално LAN внедряване използвайте HTTPS, `COOKIE_SECURE=true`, отделни ключове по устройство и PostgreSQL/Redis за multi-worker състояние.

## Тестове

```powershell
.venv\Scripts\python.exe tests\test_api.py
.venv\Scripts\python.exe -m pip check
```

Тестовете създават собствена база в системната временна директория. Не отварят, не изтриват и не променят `data/school_ai.db`.
