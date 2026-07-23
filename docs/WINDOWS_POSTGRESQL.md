# PostgreSQL под Windows

Проектът работи директно под Windows, без Docker. За development и runtime
използвайте PostgreSQL 18 и отделни бази за приложението и тестовете.

## 1. Инсталация

Инсталирайте PostgreSQL 18 за Windows с компонентите:

- PostgreSQL Server;
- Command Line Tools (`psql`, `pg_dump`, `pg_restore`);
- pgAdmin 4 по желание.

Запомнете паролата на локалния `postgres` администратор. Ако installer-ът не
добави `bin` папката в `PATH`, задайте например:

```dotenv
POSTGRES_BIN_DIR=C:/Program Files/PostgreSQL/18/bin
```

## 2. Development и test бази

Отворете Query Tool в pgAdmin като `postgres` и заменете примерната парола:

```sql
CREATE ROLE school_ai_app
    WITH LOGIN
    PASSWORD 'replace-with-a-long-random-database-password';

CREATE DATABASE school_ai_dev
    OWNER school_ai_app
    ENCODING 'UTF8';

CREATE DATABASE school_ai_test
    OWNER school_ai_app
    ENCODING 'UTF8';
```

`school_ai_test` е отделна disposable база. Live migration тестът изтрива и
създава наново само нейния `public` schema и отказва да работи с база, чието
име не завършва на `_test`.

## 3. Локална конфигурация

В `.env.local` задайте development връзката:

```dotenv
DATABASE_URL=postgresql+psycopg://school_ai_app:URL_ENCODED_PASSWORD@localhost:5432/school_ai_dev
```

Ако паролата съдържа `@`, `:`, `/`, `#`, `%` или други специални символи,
URL-encode-нете я преди поставяне в URL. Не commit-вайте `.env.local`.

Инсталирайте server зависимостите и създайте чистата schema:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe tools\create_admin.py --name "Администратор" --username admin
```

Сървърът продължава да се стартира по същия начин:

```powershell
.\run.bat
```

## 4. PostgreSQL migration test

Live тестът никога не използва `DATABASE_URL`. Задайте отделната test връзка
само за текущия PowerShell прозорец:

```powershell
$env:POSTGRES_TEST_DATABASE_URL='postgresql+psycopg://school_ai_app:URL_ENCODED_PASSWORD@localhost:5432/school_ai_test'
.\.venv\Scripts\python.exe -m unittest tests.test_postgresql -v
Remove-Item Env:POSTGRES_TEST_DATABASE_URL
```

Останалият test suite продължава да използва временни SQLite файлове за
бързина и никога не докосва `school_ai_dev`.

## 5. Backups

Админ панелът извиква `pg_dump` в custom format и валидира архива чрез
`pg_restore --list`. Паролата се предава на PostgreSQL инструмента чрез
процесната среда и не присъства в command-line аргументите.

Възстановяване не се изпълнява от работещия сървър. При нужда спрете
приложението и използвайте контролирано:

```powershell
& 'C:\Program Files\PostgreSQL\18\bin\pg_restore.exe' `
  --clean --if-exists --no-owner --no-acl `
  --host localhost --port 5432 --username school_ai_app `
  --dbname school_ai_dev 'D:\path\to\school-ai-backup.dump'
```

Преди restore винаги създайте отделно актуално копие и проверете целевата база.

За безопасен пълен restore rehearsal използвайте само отделната
`school_ai_test` база. Следната команда изтрива нейния `public` schema,
възстановява архива, проверява всички таблици и Alembic revision-а, след което
я почиства отново:

```powershell
$env:POSTGRES_TEST_DATABASE_URL='postgresql+psycopg://school_ai_app:URL_ENCODED_PASSWORD@localhost:5432/school_ai_test'
.\.venv\Scripts\python.exe tools\verify_postgresql_restore.py `
  data\backups\school-ai-backup.dump `
  --confirm-destroy-test-database
Remove-Item Env:POSTGRES_TEST_DATABASE_URL
```

Tool-ът отказва non-PostgreSQL база, име без `_test` и runtime базата.
