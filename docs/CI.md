# Continuous integration

GitHub Actions изпълнява следните проверки при pull request и push към
`main`:

- целия test suite на Python 3.11 и 3.12;
- live Alembic migration върху disposable PostgreSQL 18 service database;
- `compileall` и `pip check`;
- `pip-audit` за server, development, optional AI и QR-node профилите;
- CodeQL Python анализ с `security-extended` queries;
- седмични Dependabot проверки за Python и GitHub Actions.

Workflow файловете са:

- `.github/workflows/ci.yml`;
- `.github/workflows/codeql.yml`;
- `.github/dependabot.yml`.

След първото успешно изпълнение защитете `main` в GitHub и изисквайте:

- `Tests / Python 3.11`;
- `Tests / Python 3.12`;
- `Dependency audit`;
- `CodeQL / Python`, когато repository visibility позволява CodeQL upload.

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

За локалния live PostgreSQL test задайте `POSTGRES_TEST_DATABASE_URL` само
към отделна база, чието име завършва на `_test`.
