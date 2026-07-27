# Operational runbook

Този runbook е за техническия оператор на School AI при deployment с един
Uvicorn process, PostgreSQL, Nginx и `systemd`. Действията са подредени така,
че да не изискват достъп до device credentials или лични съобщения.

## Ежедневна проверка

1. Отворете **Устройства → Диагностика**.
2. Проверете `READY`, active warnings, свободното дисково място, HTTP
   errors/latency и последните `backup`/`retention` задачи.
3. Проверете поне един последен backup със статус `verified`.
4. При server достъп потвърдете:

```bash
curl --fail http://127.0.0.1:5000/health/live
curl --fail http://127.0.0.1:5000/health/ready
curl --fail http://127.0.0.1:5000/health/metrics
sudo systemctl --failed
sudo systemctl list-timers school-ai-maintenance.timer
```

`/health/metrics` съдържа само агрегирани технически стойности. Production
Nginx шаблонът не го публикува към LAN; локален monitoring agent го чете от
`127.0.0.1:5000`.

## Логове и correlation ID

`/var/log/school-ai/system.log` е JSON Lines. Всеки HTTP отговор има
`X-Request-ID`; подаден валиден `X-Request-ID` се запазва. За PWA заявките
логът включва device identifier, но никога device key, query string, request
body, QR token, име или лично съобщение.

```bash
sudo journalctl -u school-ai.service --since "30 minutes ago"
sudo tail -n 200 /var/log/school-ai/system.log
```

Търсете по `request_id`, `device_id`, `event` и `error_type`. Записът съдържа
само класа на грешката, затова чувствителни exception текстове не се използват
за диагностика.

## Рестарт на приложението

1. Проверете и запишете текущия `/health/ready` резултат.
2. Ако има schema/deployment промяна, първо създайте проверен backup.
3. Рестартирайте само application service:

```bash
sudo systemctl restart school-ai.service
sudo systemctl status school-ai.service
curl --fail http://127.0.0.1:5000/health/ready
```

PWA клиентите се свързват отново с exponential backoff. Не стартирайте втори
Uvicorn worker за временно „решение“, защото WebSocket/session state е
in-process.

## Backup и автоматична поддръжка

Автоматичната задача изпълнява проверен database backup и retention cleanup.
Всяка подзадача има отделен `operational_job_runs` запис и system audit event.

Инсталация:

```bash
sudo install -m 0644 \
  /opt/school-ai/deploy/linux/school-ai-maintenance.service \
  /etc/systemd/system/school-ai-maintenance.service
sudo install -m 0644 \
  /opt/school-ai/deploy/linux/school-ai-maintenance.timer \
  /etc/systemd/system/school-ai-maintenance.timer
sudo systemctl daemon-reload
sudo systemctl enable --now school-ai-maintenance.timer
sudo systemctl start school-ai-maintenance.service
sudo systemctl status school-ai-maintenance.service
```

След първото успешно изпълнение включете **Настройки → Наблюдение → Следене
на автоматичната поддръжка**. Ръчен backup може да се създаде и от
**Система → Резервни копия**.

За еднократна техническа проверка:

```bash
sudo -u school-ai /opt/school-ai/.venv/bin/python \
  /opt/school-ai/tools/run_maintenance.py --job backup
```

Копирайте поне един проверен backup към криптирано хранилище извън application
сървъра. Самият timer не реализира off-site копиране.

## Restore

Restore не се стартира от админ панела и не заменя автоматично работещата
база.

1. Изберете backup със статус `verified` и същия application release.
2. Проверете го първо в отделна disposable база, чието име завършва на
   `_test`, чрез `tools/verify_postgresql_restore.py`.
3. За production incident спрете `school-ai.service` и
   `school-ai-maintenance.timer`.
4. DBA възстановява архива в **нова** replacement база, проверява Alembic
   revision, row counts и `/health/ready` с отделна deployment конфигурация.
5. Едва след документирaна проверка сменете `DATABASE_URL` към replacement
   базата и стартирайте приложението.
6. Не изтривайте повредената база, докато възстановяването и audit-ът не са
   приключили.

Командата за безопасния `_test` rehearsal е описана в
[`LINUX_DEPLOYMENT.md`](LINUX_DEPLOYMENT.md).

## Изгубен или невалиден device key

### Browser PWA

1. Деактивирайте изгубеното устройство в **Устройства**.
2. Създайте нов краткотраен pairing QR за правилната физическа точка.
3. На устройството отворете `/pair?profile=kiosk` или
   `/pair?profile=screen` и сдвоете отново.
4. Не копирайте device key ръчно; той остава в `HttpOnly` cookie.

### QR camera node

Използвайте **Смени ключа**, запишете новата стойност при еднократното ѝ
показване и обновете защитения node env. Старият ключ се деактивира веднага.

## Offline таблет или екран

1. В **Диагностика** проверете последния heartbeat, WebSocket disconnect,
   camera permission и pending ACK.
2. Ако WebSocket е активен, изпратете **Провери връзката** или
   **Изискай актуална диагностика**.
3. Ако устройството е offline, командата остава `pending` и ще се вземе след
   връщане на връзката. Проверете локално захранване, trusted HTTPS адрес,
   Wi-Fi/DNS/NTP и browser kiosk режима.
4. При стар PWA shell изпратете **Провери и приложи PWA обновяване**; при
   повреден cache използвайте **Изчисти PWA кеша и презареди**.
5. Ако credential е отнет или cookie е изчистено, сдвоете отново.

Remote control-ът е умишлено application-level: pause/resume, reload,
diagnostics, connectivity, PWA update/cache и camera/audio/screen тестове.
Няма shell, произволен URL, OS restart, Wi-Fi промяна, screen capture или
достъп до файлове. OS reboot, kiosk lock и мрежови политики се правят чрез
MDM/Android device-owner/Windows Assigned Access.

## Load/reconnect baseline

Преди release изпълнете детерминирания transport baseline:

```bash
/opt/school-ai/.venv/bin/python \
  /opt/school-ai/tools/load_reconnect.py \
  --devices 100 \
  --reconnect-rounds 5
```

Той не използва production база или credentials. Проверява unique device
registration, exact targeting, reconnect churn и премахване на stale
WebSocket връзки. Това е software baseline, а не заместител на петдневния
хардуерен pilot.
