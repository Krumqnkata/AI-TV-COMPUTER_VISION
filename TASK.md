# School AI — активен roadmap

Този файл е единственият актуален списък с оставаща продуктова работа. Историческата спецификация е запазена в `docs/archive/TASK-original.md` и не определя текущия обхват.

## Текущ обхват

- QR баджове за идентификация без биометрия и лицево разпознаване.
- FastAPI backend, SQLAdmin контролен център и PostgreSQL runtime база с
  Windows development и Linux production deployment.
- Индивидуално сдвоени camera/kiosk/screen устройства с ограничени команди и acknowledgment.
- Rule-based асистент по подразбиране; Gemini и Ollama са optional providers с минимален read-only контекст.

## Завършена основа

- REST/WebSocket договори, CSRF, Argon2, RBAC и административен audit.
- Управление на хора, баджове, графици, съдържание, настройки, устройства, imports, retention и backups.
- Alembic-only schema bootstrap с legacy baseline и изолирани test fixtures.
- Разделени server, QR-node, AI и development dependency профили.
- GitHub Actions за Python 3.11/3.12, live PostgreSQL migration, dependency
  audit и CodeQL.
- Linux deployment без Docker със systemd, Nginx/TLS шаблон и log rotation.

## Следващи приоритети

1. **Физически pilot** — поне две камери и два екрана в различни зони;
   измерване на latency, duplicate filtering и ACK retry.
2. **Училищен production rollout** — инсталация на подготвения Linux
   deployment, реален TLS сертификат, least-privilege PostgreSQL role и
   restore rehearsal при спрян сървър.
3. **Надеждност при мащабиране** — Redis-backed connection/delivery state
   преди повече от един Uvicorn worker.
4. **Наблюдаемост** — structured logs, health metrics и предупреждения за
   offline устройства и забавени ACK команди.
5. **Optional STT** — отделен Whisper/audio endpoint с consent, rate limits,
   retention и изрично включване от администратора.

## Непроменими граници

- Без face recognition, mood detection или скрито биометрично профилиране.
- Без shell команди, OS restart и произволни файлови пътища от админ панела.
- Без автоматична подмяна или restore на работещата база.
- Legacy shared device key остава само временен compatibility fallback до сдвояване на всички физически устройства.
