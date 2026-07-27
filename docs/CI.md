# Continuous integration

GitHub Actions изпълнява следните проверки при pull request и push към
`main`:

- целия test suite на Python 3.11 и 3.12;
- live Alembic migration върху disposable PostgreSQL 18 service database;
- `compileall` и `pip check`;
- `pip-audit` за server, development, optional AI и QR-node профилите;
- пълен Chromium PWA acceptance за pairing, QR session, targeted delivery,
  remote command wake-up/ACK, idle cleanup, reconnect и Service Worker;
- smoke проверки с Firefox, WebKit и Microsoft Edge върху Windows runner;
- CodeQL Python анализ с `security-extended` queries;
- седмични Dependabot проверки за Python и GitHub Actions.

Workflow файловете са:

- `.github/workflows/ci.yml`;
- `.github/workflows/codeql.yml`;
- `.github/dependabot.yml`.

`main` е защитен в GitHub със strict status checks и изисква:

- `Tests / Python 3.11`;
- `Tests / Python 3.12`;
- `Dependency audit`;
- `PWA / Chromium full acceptance`;
- `PWA / Firefox smoke`;
- `PWA / WebKit smoke`;
- `PWA / Windows Edge smoke`;
- `CodeQL / Python`, когато repository visibility позволява CodeQL upload.

Правилото важи и за администратори, изисква linear history и забранява force
push и branch deletion. Не е включен изкуствен second-reviewer праг, защото
repository workflow-ът е с един поддържащ; всички промени все пак минават през
feature branch и зелени required checks.

Локалният еквивалент е:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q main.py migrations tests tools utils web
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
.\.venv\Scripts\python.exe -m pip_audit -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip_audit -r requirements-ai.txt
.\.venv\Scripts\python.exe -m pip_audit -r requirements-node.txt --ignore-vuln PYSEC-2026-2132
```

Локален Chromium acceptance:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
$env:RUN_BROWSER_E2E='1'
$env:PLAYWRIGHT_BROWSER='chromium'
$env:PWA_E2E_MODE='full'
.\.venv\Scripts\python.exe -m unittest tests.test_pwa_browser -v
```

Browser suite-ът стартира отделен FastAPI процес и временна SQLite база. Той
не използва development или production PostgreSQL.

За локалния live PostgreSQL test задайте `POSTGRES_TEST_DATABASE_URL` само
към отделна база, чието име завършва на `_test`.
