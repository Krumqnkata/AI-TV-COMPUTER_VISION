# Linux deployment без Docker

Тази процедура е за един училищен сървър с `systemd`, PostgreSQL и Nginx.
Windows development средата и `run.bat` остават непроменени.

## 1. Системни пакети

На Debian/Ubuntu инсталирайте Python, PostgreSQL client/server, Nginx и Git:

```bash
sudo apt update
sudo apt install python3 python3-venv postgresql postgresql-client nginx git curl
```

Създайте непривилегирован service user и deployment директориите:

```bash
sudo useradd --system --home-dir /opt/school-ai --shell /usr/sbin/nologin school-ai
sudo install -d -o school-ai -g school-ai -m 0750 /opt/school-ai
sudo install -d -o school-ai -g school-ai -m 0750 \
  /var/lib/school-ai/backups \
  /var/lib/school-ai/imports \
  /var/log/school-ai
```

## 2. Приложение

Клонирайте release branch или разархивирайте проверен release в
`/opt/school-ai`, след което:

```bash
sudo -u school-ai python3 -m venv /opt/school-ai/.venv
sudo -u school-ai /opt/school-ai/.venv/bin/python -m pip install -r /opt/school-ai/requirements.txt
```

Не стартирайте приложението като `root`.

## 3. PostgreSQL

Използвайте `postgres` superuser-а само за първоначалната настройка:

```sql
CREATE ROLE school_ai_app
    WITH LOGIN
    PASSWORD 'replace-with-a-long-random-database-password';

CREATE DATABASE school_ai_prod
    OWNER school_ai_app
    ENCODING 'UTF8';
```

Копирайте production env шаблона:

```bash
sudo install -o school-ai -g school-ai -m 0600 \
  /opt/school-ai/deploy/linux/env.production.example \
  /opt/school-ai/.env.local
sudoedit /opt/school-ai/.env.local
```

Заменете всички placeholders. Database паролата в URL трябва да е URL-encoded.
Генерирайте различни стойности за `ADMIN_SECRET_KEY` и
`SETTINGS_MASTER_KEY`. Не използвайте PostgreSQL `postgres` ролята в
`DATABASE_URL`.

Създайте schema-та и първия администратор:

```bash
sudo -u school-ai /opt/school-ai/.venv/bin/python -m alembic upgrade head
sudo -u school-ai /opt/school-ai/.venv/bin/python -m alembic current
sudo -u school-ai /opt/school-ai/.venv/bin/python \
  /opt/school-ai/tools/create_admin.py \
  --name "Администратор" \
  --username admin
```

## 4. systemd

Инсталирайте и стартирайте service файла:

```bash
sudo install -m 0644 \
  /opt/school-ai/deploy/linux/school-ai.service \
  /etc/systemd/system/school-ai.service
sudo systemctl daemon-reload
sudo systemctl enable --now school-ai.service
sudo systemctl status school-ai.service
```

Логовете се виждат с:

```bash
sudo journalctl -u school-ai.service -f
```

Файловият `system.log` е в `/var/log/school-ai`. Инсталирайте logrotate
правилото:

```bash
sudo install -m 0644 \
  /opt/school-ai/deploy/linux/school-ai.logrotate \
  /etc/logrotate.d/school-ai
sudo logrotate --debug /etc/logrotate.d/school-ai
```

## 5. Nginx и TLS

Преди инсталация заменете:

- `school-ai.example.edu` с реалното DNS име;
- certificate paths с училищния или публичния TLS сертификат.

```bash
sudo install -m 0644 \
  /opt/school-ai/deploy/linux/nginx-school-ai.conf \
  /etc/nginx/sites-available/school-ai
sudo ln -s /etc/nginx/sites-available/school-ai /etc/nginx/sites-enabled/school-ai
sudo nginx -t
sudo systemctl reload nginx
```

Nginx шаблонът предава WebSocket upgrade headers. Приложението слуша само на
`127.0.0.1:5000`; LAN клиентите използват HTTPS адреса на Nginx.

Не отваряйте киоска през raw LAN HTTP адрес. Browser camera access, Service
Worker и PWA installability изискват HTTPS origin с сертификат, доверен от
таблетите и Windows устройствата. След TLS настройката проверете `/pair`,
`/kiosk`, `/screen`, двата manifest файла и `/ws/kiosk`/`/ws/screen`.

Отворете отвън само необходимите портове:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

Не публикувайте PostgreSQL port 5432 към училищната LAN, освен ако няма
отделна мрежова архитектура и firewall правило.

## 6. Проверка

```bash
curl --fail http://127.0.0.1:5000/health/live
curl --fail http://127.0.0.1:5000/health/ready
curl --fail https://school-ai.example.edu/health/ready
sudo -u school-ai /opt/school-ai/.venv/bin/python -m alembic current
```

Създайте backup от админ панела и проверете, че архивът е със статус
`verified`. Направете restore rehearsal само в отделна disposable `_test`
база, никога върху работещата production база.

```bash
export POSTGRES_TEST_DATABASE_URL='postgresql+psycopg://school_ai_app:URL_ENCODED_PASSWORD@localhost:5432/school_ai_restore_test'
/opt/school-ai/.venv/bin/python \
  /opt/school-ai/tools/verify_postgresql_restore.py \
  /var/lib/school-ai/backups/school-ai-backup.dump \
  --confirm-destroy-test-database
unset POSTGRES_TEST_DATABASE_URL
```

Tool-ът изтрива, използва и почиства само `public` схемата на посочената
`*_test` база.

## 7. Обновяване

Преди release:

1. създайте и изтеглете проверен backup;
2. спрете услугата;
3. обновете кода до конкретен release/commit;
4. инсталирайте pinned dependencies;
5. изпълнете `alembic upgrade head`;
6. стартирайте услугата и проверете `/api/stats`.

```bash
sudo systemctl stop school-ai.service
sudo -u school-ai /opt/school-ai/.venv/bin/python -m pip install \
  -r /opt/school-ai/requirements.txt
sudo -u school-ai /opt/school-ai/.venv/bin/python -m alembic upgrade head
sudo systemctl start school-ai.service
sudo systemctl status school-ai.service
```

Текущият deployment е умишлено с един application process. Не добавяйте
няколко Uvicorn workers преди WebSocket connection/delivery state да бъде
изнесено в Redis.
