# School AI — окончателен активен roadmap

**Последна актуализация:** 27 юли 2026 г.

Този файл е единственият актуален списък с оставащата работа по продукта.
Завършеното е обобщено в `PROGRESS.md`, а историческата спецификация е в
`docs/archive/TASK-original.md`.

## Взети архитектурни решения

- Централната система работи на един Linux сървър с FastAPI, PostgreSQL,
  Nginx/TLS и един Uvicorn worker.
- Първият pilot е с два таблета и не изисква Redis.
- Един таблет е комбиниран `camera + qr + kiosk + screen + audio` node.
- `/screen` има и отделен public/paired профил за Windows PC, TV browser или
  втори таблет; двата PWA профила могат да се инсталират независимо.
- QR видеото се обработва локално на таблета. Към сървъра се изпраща само
  прочетеният badge token.
- Основният tablet клиент е локално хостван web/PWA интерфейс. Малък Android
  camera wrapper се допуска само ако браузърът не управлява надеждно
  наличната selfie камера.
- Python/OpenCV QR node остава fallback за Mini PC, Raspberry Pi или външна
  USB/IP камера, но не е основният tablet клиент.
- PostgreSQL пази трайните данни. Redis се добавя само при реална нужда от
  няколко backend процеса или сървъра.
- Rule-based асистентът остава default. STT, Whisper, Gemini и Ollama са
  optional функции след успешен pilot.

## Завършена основа

- FastAPI composition root с отделни routers, services и admin views.
- PostgreSQL runtime база, Alembic migrations и изолирани SQLite/PostgreSQL
  тестове.
- Argon2, CSRF, RBAC, login lockout, encrypted secrets и административен audit.
- Управление на хора, QR баджове, разписание, съдържание, роли, настройки,
  устройства, imports, retention и backups.
- Индивидуален device enrollment, heartbeat, config, безопасни команди и ACK.
- Таргетирани WebSocket връзки по `device_id`, `screen_id` и `zone_id`.
- PostgreSQL backup чрез `pg_dump`, SHA-256 проверка и контролиран restore
  rehearsal.
- GitHub Actions за Python 3.11/3.12, PostgreSQL migration test, dependency
  audit и CodeQL.
- Linux deployment шаблони за systemd, Nginx/TLS и logrotate без production
  Docker.
- Премахнати legacy face-recognition, mood detection и несъвместими стари
  модули.
- Реализирани `/pair`, `/kiosk` и `/screen` PWA профили с локален QR decoder,
  HttpOnly credentials, public feed, targeted personal delivery и browser E2E.

## Оставаща работа по приоритет

### 0. Приключване на текущата repository работа

- [x] Всички одобрени промени да бъдат commit-нати, публикувани и merge-нати в
  `main`.
- [x] `main` да остане със зелени Python 3.11, Python 3.12 и dependency audit
  проверки.
- [x] Да се включи branch protection с required CI checks.
- [x] При промяна на database/deployment кода да се изпълнява и live
  PostgreSQL test върху отделната `*_test` база.

### 1. Tablet QR proof of concept

- [x] Да се създаде малка HTTPS страница, която отваря предната камера на
  наличния таблет и декодира само QR кодове.
- [ ] Да се използва реалният формат на училищния бадж с QR размер приблизително
  40–45 mm, чист бял фон и без декоративно лого върху кода.
- [ ] Да се тества 2 MP / 720p selfie камерата при 25, 40 и 60 cm, нормално
  коридорно осветление, по-тъмна среда, отблясъци и наклон.
- [ ] Да се направят поне 100 сканирания с реален ламиниран бадж.
- [ ] Да се измерят success rate и време до разпознаване.
- [ ] Ако browser вариантът не е надежден, да се тества малък Android
  CameraX/ML Kit wrapper, без промяна на backend API договора.

**Критерий за приемане:** поне 98% успешни сканирания на основното работно
разстояние, обичайно за около една секунда, без изпращане на видео към сървъра.

### 2. Tablet Kiosk PWA v1

- [x] Старият общ `index.html` прототип да се раздели на ясни client режими:
  `/pair`, `/kiosk` и `/screen`.
- [x] Всички CSS, шрифтове, QR decoder и JavaScript assets да се сервират
  локално, без Tailwind CDN, Google Fonts или друга runtime internet зависимост.
- [x] `/pair` да приема еднократен enrollment код и да записва индивидуалните
  `device_id` и `device_key` без secret в URL.
- [x] Устройството да получава `zone_id`, `screen_id`, `camera_id`, capabilities
  и runtime настройки от сървъра.
- [x] Предната камера да сканира QR локално и чрез scoped
  `/api/kiosk/detect` facade да използва съществуващата QR detection service.
- [x] WebSocket клиентът да се регистрира с индивидуалните credentials и да
  получава само събитията за своя екран/зона.
- [x] Да има heartbeat, config refresh, command polling/ACK и видим
  online/offline статус.
- [x] Безопасните application команди да известяват точния PWA client по
  WebSocket и да покриват диагностика, connectivity, update/cache, pause/resume
  и локалните camera/audio/screen тестове.
- [x] Reconnect логиката да използва backoff, а delivery ACK да се повтаря
  безопасно след временна мрежова грешка.
- [x] Да има защита от повторно показване на една и съща доставка.
- [x] Личната сесия да се изчиства автоматично след зададения idle timeout.
- [x] Интерфейсът да има големи touch бутони, текстово поле, ясни съобщения за
  грешка и browser TTS.
- [x] Текстовият интерфейс да използва съществуващия assistant API. STT не е
  част от задължителния v1.
- [x] Публичният kiosk да не съдържа person registration, system administration
  или други staff действия.
- [x] Да се добавят browser E2E тестове за pairing, QR session, reconnect,
  targeted delivery, ACK и idle cleanup.

**Статус:** софтуерната реализация и Chromium acceptance са завършени.
Хардуерният критерий остава отворен до изпълнение на физическите тестове в
раздели 1 и 3.

**Критерий за приемане:** таблетът може да бъде сдвоен от чисто състояние, да
се възстанови след рестарт, да сканира бадж и да завърши таргетирана сесия без
ръчна намеса или изтичане на чужди данни.

### 3. Заключване и физически pilot

- [ ] Таблетите да бъдат монтирани стабилно, постоянно захранени и свързани към
  отделена училищна Wi-Fi/VLAN мрежа.
- [ ] За development да се използва PWA fullscreen/screen pinning, а за
  production — Android managed kiosk/device-owner режим, MDM или защитен kiosk
  wrapper.
- [ ] Да се предотвратят sleep, notifications, излизане към настройките и
  стартиране на други приложения.
- [ ] Да се инсталират два таблета в две различни зони с отделни device
  credentials.
- [ ] Да се измерят QR latency, duplicate filtering, WebSocket reconnect,
  delivery ACK retry, heartbeat и offline detection.
- [ ] Да се тестват рестарт на таблет, рестарт на сървър, прекъсване на Wi-Fi и
  временно спиране на PostgreSQL.
- [ ] Да се направи usability проверка с реални ученици, учители и
  администратори без използване на production лични данни в началния тест.
- [ ] Резултатите, проблемите и избраният tablet модел да се документират.

**Критерий за приемане:** поне пет учебни дни стабилен pilot с два таблета,
без загубена потвърдена доставка, без cross-zone показване и с приемливо време
за QR реакция.

### 4. Минимална наблюдаемост и автоматизация

- [x] Да се добавят `/health/live` и `/health/ready` endpoints.
- [x] Логовете да станат structured JSON с request/correlation ID, device ID и
  безопасно редуцирани грешки без credentials или лични съобщения.
- [x] Да има метрики за HTTP грешки, latency, активни WebSocket връзки,
  offline устройства, QR failures и забавени ACK.
- [x] Offline marking да работи като периодична задача, а не само при отваряне
  на admin dashboard.
- [x] Да има пълен набор оперативни предупреждения:
  - [x] устройство без heartbeat;
  - [x] доставка/команда без ACK;
  - [x] липсващ или стар проверен backup;
  - [x] недостатъчно disk space;
  - [x] неуспешна периодична задача.
- [x] Да има админ диагностика за камера, WebSocket, browser capabilities,
  heartbeat, чакащи command ACK и последна връзка.
- [x] Retention cleanup и backup графикът да бъдат автоматизирани с
  deployment-level scheduler и audit.
- [x] Да се добавят load/reconnect тестове за очаквания брой устройства.
- [x] Да се напише кратък operational runbook за restart, backup, restore,
  lost device key и offline tablet.

### 5. Училищен production rollout

- [ ] Подготвеният Linux deployment да се инсталира на реалния училищен
  сървър.
- [ ] Да се създадат отделни least-privilege PostgreSQL роли за приложението,
  migrations и backups според реалната operational процедура.
- [ ] Да се настроят вътрешен DNS адрес, реален TLS сертификат, Nginx,
  firewall, коректно време/NTP, systemd и logrotate.
- [ ] Uvicorn да остане с един worker за първоначалния deployment.
- [ ] Да се създадат реалните staff профили и да се проверят четирите роли и
  права.
- [ ] Да се импортират реалните помещения, разписание, хора и съдържание чрез
  контролиран preview/import процес.
- [ ] Да има автоматични локални backups и поне едно криптирано копие извън
  application сървъра.
- [ ] Да се изпълни пълен restore rehearsal при спрян сървър и да се документира
  реалното време за възстановяване.
- [ ] Да се провери целият cold-start процес след рестарт или прекъсване на
  електрозахранването.
- [ ] Legacy shared `DEVICE_API_KEY` да се изключи след сдвояване на всички
  устройства.
- [ ] Да се проведе кратко обучение по `ADMIN_GUIDE.md`.

**Критерий за приемане:** системата се възстановява документируемо от проверен
backup, всички устройства използват индивидуални credentials, TLS е активен и
администраторите могат да изпълняват ежедневните операции без shell достъп.

## Redis — само при условие

Redis не е задача за първия deployment с един сървър, един Uvicorn worker и два
таблета. Добавя се преди някое от следните:

- повече от един Uvicorn worker;
- втори backend сървър или automatic failover;
- отделни background workers, които споделят kiosk sessions и delivery state;
- zero-downtime deployment, при който едновременно работят няколко application
  процеса.

При добавяне Redis трябва да поеме transient connection/session/deduplication
state и event distribution. PostgreSQL остава единственият източник на трайните
business данни.

## Optional работа след успешен production pilot

- [ ] Push-to-talk STT с локален Whisper service, видима microphone индикация,
  consent, rate limits и кратка/нулева audio retention.
- [ ] Gemini или локален Ollama provider след privacy и quality оценка.
- [ ] Native Android приложение само ако PWA и малкият camera wrapper не дават
  достатъчна надеждност или device management.
- [ ] Повече Uvicorn workers/Redis само след измерен капацитетен проблем.
- [ ] Допълнителни пасивни TV screen nodes и нови училищни зони.

## Непроменими граници

- Без face recognition, mood detection или скрито биометрично профилиране.
- Без изпращане или съхраняване на camera video за QR идентификация.
- Без shell команди, OS restart и произволни файлови пътища от админ панела.
- Без промяна на database URL, TLS private keys, master/session keys или
  database restore от админ панела.
- Без автоматична подмяна или restore на работещата база.
- Без production secrets в Git, logs, URLs или QR кодове.
- Legacy shared device key остава само временен compatibility fallback.

## Кога проектът е завършен

Проектът се счита за production-ready, когато:

1. Tablet QR proof of concept и Kiosk PWA v1 покриват критериите по-горе.
2. Двата pilot таблета работят стабилно поне пет учебни дни.
3. CI, E2E, reconnect и live PostgreSQL тестовете са зелени.
4. Monitoring и offline/ACK предупрежденията са активни.
5. Linux/PostgreSQL/TLS deployment-ът е проверен на реалния сървър.
6. Backup от production-like база е възстановен успешно по документацията.
7. Няма активни legacy shared credentials или недокументирани production
   тайни.
