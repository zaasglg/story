# Backend на отдельном сервере с Docker

Это инструкция для нового сервера Linux с Docker Engine, Compose v2, Python3 и доменом. На существующем сервере с данными сначала нужны отдельная защищённая копия БД/PII-ключа и согласование миграций. Установку на «Invest» без его адреса и способа доступа не выполняли.

## Получить проверенную ветку

```sh
git clone --branch codex/backend-server https://github.com/zaasglg/story.git alash
cd alash
```

## Настроить домен и ключи

Направьте DNS A-запись домена API на IP сервера. Откройте входящие TCP80/443 и UDP443; PostgreSQL, Redis и8000 не нужно публиковать наружу. Домен в следующей команде замените своим:

```sh
python3 deploy/generate_server_env.py --domain api.example.com
```

Скрипт создаёт `.env.server` с правами0600 и независимыми ключами. Не печатает секреты и отказывается перезаписывать существующий файл. Сохраните отдельную защищённую копию: без прежнего PII-ключа нельзя расшифровать сохранённые паспорта и банки. При обновлении никогда не генерируйте ключи заново поверх действующих.

Если используется Flutter Web на другом домене, дополните `M1917_CORS_ORIGINS` его HTTPS origin. Нативное приложение не требует browser CORS.

## Запустить новый Backend

```sh
docker compose --env-file .env.server -f compose.server.yaml config --quiet
docker compose --env-file .env.server -f compose.server.yaml up -d --build
```

Отдельный migrate-service применит схему до0037, затем запустится API. В этом варианте нет Flutter Web и демонстрационного seed. Caddy выпустит HTTPS-сертификат, если домен уже указывает на сервер и80/443 доступны. WebSocket работает через тот же proxy.

```sh
docker compose --env-file .env.server -f compose.server.yaml ps
docker compose --env-file .env.server -f compose.server.yaml logs -f --tail=100 backend proxy
curl --fail https://api.example.com/health/ready
```

Ожидается `{"status":"ready"}`. Не считать публикацию проверенной до этого запроса и входа тестового пользователя.

## Создать первого BOSS на новом сервере

Пароль вводится интерактивно, не записывается в shell history или Git. Выполняйте только на новом сервере для первичного аккаунта; штатных ADMIN затем создаёт BOSS через приложение.

```sh
docker compose --env-file .env.server -f compose.server.yaml exec backend python -c '
import asyncio, getpass, json, os
phone = input("Телефон BOSS в международном формате: ").strip()
name = input("ФИО BOSS: ").strip()
password = getpass.getpass("Пароль (не менее 8 символов): ")
if len(password) < 8 or password != getpass.getpass("Повторите пароль: "):
    raise SystemExit("Проверьте длину и совпадение паролей")
os.environ["M1917_BOOTSTRAP_USERS_JSON"] = json.dumps([{
    "phone": phone, "full_name": name, "employee_id": "BOSS-001",
    "role": "BOSS", "password": password
}])
from backend.seed import seed
asyncio.run(seed())
'
```

Не переносите демонстрационный общий пароль на сервер. Если аккаунт уже существует, используйте штатную процедуру изменения пароля после проверки личности.

## Подключить Flutter на Mac

После проверки HTTPS API выполните в локальном `mobile/`, указав тот же домен:

```sh
flutter run --dart-define=API_BASE_URL=https://api.example.com/api/v1
```

`localhost:8000` обращается к Mac, а не к удалённому серверу. При смене backend на новую БД потребуется заново войти: сессии старой БД туда не переносились. Реквизиты/работники также не переносятся автоматически.

## Включить автоматические прогулы

Сначала подтвердите расписания и нерабочие дни объектов в приложении. Существующие legacy-расписания миграция намеренно оставляет неутверждёнными. Укажите в `.env.server` согласованную дату начала, например текущую дату в форматеYYYY-MM-DD, в `M1917_ATTENDANCE_AUTO_FROM`:

```sh
docker compose --env-file .env.server -f compose.server.yaml --profile attendance up -d attendance-worker
docker compose --env-file .env.server -f compose.server.yaml logs -f attendance-worker
```

Без явной даты worker не запускается. Время ожидания централизовано в `M1917_ATTENDANCE_GRACE_MINUTES` (30 минут после конца смены по умолчанию). EXCUSED и нерабочие дни не создают прогул. Не ставьте старую дату без отдельной исторической проверки.

## Что эта установка не делает

Не переносит данные прежней БД, не исправляет исторические финансы и не настраивает Firebase/APNs. Автоматические off-host backups ещё нужно организовать; Docker volume резервной копией не является. Аудиопроверка использует ffprobe, установленный в Backend Docker image.

Обновление существующей БД выполняется отдельно после проверенной резервной копии и разрешения. Не используйте `down -v`: команда удаляет данные volumes.
