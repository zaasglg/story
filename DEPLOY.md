# Развёртывание Backend

Для отдельного сервера с Docker используйте [deploy/SERVER.md](deploy/SERVER.md) и compose.server.yaml. Они запускают Backend отдельно от Flutter и генерируют независимые production-ключи. Ранее подготовленный вариант Koyeb/Neon/Upstash сохранён ниже.

# Free backend deployment

## Состояние подготовки мобильного релиза (22 октября 2026)

Публикация в магазинах не выполнялась; релиз не считается готовым.

- Android Application ID и iOS Bundle ID выбраны `kz.alash.m1917`, namespace Android
  и Kotlin package согласованы с ним. RunnerTests использует `kz.alash.m1917.RunnerTests`.
  Идентификаторы ещё не зарегистрированы в магазинах; необходимо подтвердить их
  доступность в аккаунтах организации. Это изменение не переименовывает macOS-приложение.
  Переход со старого `com.m1917.m1917` означает отдельную установку на телефоне;
  перенос локальной авторизации не выполняется.
  После публикации Android ID нельзя менять для обновления того же приложения:
  [Android application ID](https://developer.android.com/build/configure-app-module).
- Android release больше не использует debug signing. Подготовлена release signing
  configuration из локального `mobile/android/key.properties`; шаблон —
  `key.properties.example`. `flutter build apk`/`appbundle` в release без ключа
  останавливаются с ошибкой, а не подписываются debug-ключом.
  Нужен upload keystore организации, защищённое хранение ключа и паролей,
  заполненный key.properties и проверка итогового AAB.
  Ключи/пароли не должны попадать в Git или чат. Порядок настройки:
  [Flutter Android release](https://docs.flutter.dev/deployment/android).
- Для Firebase/APNs по-прежнему нужны реальные проекты, конфигурации приложений
  с окончательными ID, Web VAPID/APNs и серверные credentials (инструкция ниже).
  Сейчас отправка push выключена. Изменение ID требует согласовать Firebase config.
- Брендовые launcher icons и native splash ещё не подготовлены и не проверены.
  Не считать текущие шаблонные платформенные ресурсы финальным оформлением ALASH.
- Локальная проверка Flutter обнаружила непринятые Android SDK licenses.
  Их должен просмотреть и принять уполномоченный пользователь через
  `flutter doctor --android-licenses`; агент их автоматически не принимает.
- Доступны Web/macOS, но физическое Android/iOS-устройство не подключено.
  Нужно отдельно проверить авторизацию/refresh, завершение сеансов, WebSocket
  после смены сети и выхода из фона, QR-камеру, разрешения, push в разных состояниях,
  клавиатуру/узкие экраны и установку подписанной release-сборки.
- Для Google Play/App Store нужны аккаунты организации, privacy policy,
  описание обработки данных/разрешений, скриншоты, версии, тестовые аккаунты
  для проверки и разрешение на публикацию. Начать с закрытого тестирования
  и TestFlight, а не сразу с публичного релиза.
  Для iOS также нужны Apple Developer Team и provisioning:
  [Flutter iOS release](https://docs.flutter.dev/deployment/ios).

Эти внешние зависимости не мешают проверять административную панель в Web.
Финансовые действия руководителя и публикация — отдельные этапы;
обновление списка не означает готовность мобильного релиза.

## Восстановление доступа через администратора

Автоматического сброса через SMS пока нет. Экран «Забыли пароль?» объясняет
сотруднику, как обратиться по известному рабочему контакту организации.
Он не принимает публичные заявки на сброс и не раскрывает наличие аккаунтов.
После независимой проверки личности уполномоченный оператор запускает из `backend/`:

```sh
uv run python -m backend.set_password +77000000004
```

Введите временный пароль интерактивно, не через командную строку или чат.
Команда завершает все прежние сеансы. Передайте пароль сотруднику через
согласованный защищённый канал и попросите сменить его после входа.
Принудительная смена при первом входе пока не реализована.
Для новой истории решений профиля примените `uv run alembic upgrade head`
(миграция `20261022_0015`). Не запускайте сброс на чужом аккаунте для проверки UI.

## Чат в реальном времени

WebSocket включён без Firebase: `/api/v1/chats/{thread_id}/events` на том же API-хосте.
Flutter автоматически преобразует `API_BASE_URL` из HTTP(S) в WS(S).
Для нескольких API-процессов нужен общий Redis (`M1917_REDIS_URL`): сообщения
сохраняются в PostgreSQL, Redis передаёт только сигналы обновления.
In-memory broker применяется только с внедрённым тестовым limiter, не в обычном запуске.

В production используйте HTTPS/WSS и укажите точный origin приложения
в `M1917_CORS_ORIGINS`. Reverse proxy должен пропускать WebSocket Upgrade,
не буферизовать соединение и иметь idle timeout больше 30 секунд.
Для nginx в location API, помимо обычного proxy_pass, нужны:

```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_read_timeout 60s;
```

Токен отправляется первым WebSocket-кадром, не в URL. Не включайте журналирование
содержимого кадров. Неавторизованные/чужие чаты закрываются кодом 1008,
ошибки Redis — 1013. Доступ повторно проверяется на каждом событии и heartbeat
(примерно каждые 25 секунд); истёкший токен приводит к переподключению через
обычный HTTP refresh. Клиент закрывает соединение в фоне и при закрытии экрана.
Потерянные сигналы восстанавливаются обновлением истории после переподключения
и контрольным запросом раз в минуту; при обрыве остаётся опрос каждые 10 секунд.
Уведомления ОС при закрытом приложении — отдельная функция, ей нужен Firebase ниже.

## Push-уведомления Firebase

Код клиента и отдельный `push-worker` подготовлены, но по умолчанию отключены.
Без Firebase-проекта и ключей реальные уведомления не отправляются.

1. Создайте проект в [Firebase Console](https://console.firebase.google.com/).
   Зарегистрируйте нужные приложения Android/iOS/Web с их настоящими package/bundle ID.
2. Для сервера создайте ключ сервисного аккаунта с правом отправки FCM.
   Сохраните его локально как `.secrets/firebase/service-account.json`.
   Не помещайте приватный ключ в Flutter, Git или чат. В облаке предпочтительны
   Application Default Credentials вместо постоянного JSON-ключа.
3. В корневом `.env` задайте `M1917_FIREBASE_PROJECT_ID`,
   `M1917_FIREBASE_SECRETS_DIR=./.secrets/firebase`, `M1917_PUSH_ENABLED=true`.
   Для перехода из web-уведомлений задайте `M1917_PUSH_WEB_URL=https://ваш-домен/`.
   Compose монтирует каталог ключа только для чтения; локальный запуск из `backend/`:
   `M1917_FIREBASE_CREDENTIALS_FILE=/absolute/path/service-account.json uv run backend-push-worker`.
   При локальном запуске также нужны обычные настройки БД, PII и `M1917_PUSH_ENABLED=true`.
4. Скопируйте `mobile/config/firebase.example.json` в `mobile/config/firebase.json`.
   Укажите `PUSH_ENABLED: true` и публичные параметры своего Firebase-приложения.
   `FIREBASE_APP_ID` различается для каждой платформы: используйте отдельные JSON-файлы
   при сборке Android/iOS/Web. Это клиентская конфигурация, не серверный приватный ключ.
5. Web: создайте публичный Web Push VAPID key в настройках Cloud Messaging и заполните
   `FIREBASE_WEB_VAPID_KEY`. Из `mobile/` выполните `dart run tool/build_web.dart`.
   Скрипт генерирует конфигурацию service worker и собирает приложение. Docker использует
   тот же скрипт. Web push требует HTTPS (для локальной разработки допустим localhost).
   Не используйте обычный `flutter build web` для включённого push без генерации worker config.
6. Android: добавьте `google-services.json` в `mobile/android/app/`.
   Собирайте с `flutter build apk --dart-define-from-file=config/firebase.json`.
   Разрешение Android 13+ запрашивается кнопкой в приложении; нужен девайс с Google Play services.
7. iOS: требуется iOS 15+, Apple Developer Team и физическое устройство для приёмки.
   Добавьте выданный Firebase `GoogleService-Info.plist` в Runner через Xcode
   с Target Membership Runner. Включите Push Notifications и Background Modes →
   Remote notifications, проверьте provisioning profile. Загрузите APNs authentication key
   в Firebase → Project settings → Cloud Messaging. Приватный APNs-ключ не входит в приложение.
   Собирайте с `flutter build ios --dart-define-from-file=config/firebase.json`.
8. Запустите `docker compose up -d --build backend mobile push-worker`.
   Проверьте `docker compose logs --tail=30 push-worker`: при отсутствии настроек
   ожидается `push_disabled`, при включении — `push_worker_started`.
   В приложении войдите и откройте настройки уведомлений кнопкой в шапке,
   затем нажмите включение и разрешите уведомления ОС.

Приёмка: создайте объявление/личное сообщение для тестового пользователя и проверьте
foreground, background, запуск после закрытия, нажатие, отказ в разрешении, выход
и смену аккаунта. Нужны проверки на настоящих Android/iOS и HTTPS Web.
На экране блокировки выводится только общий текст без финансовых и личных данных.
`COMPLETED` в очереди не подтверждает показ на устройстве. Повторы после аварии
возможны; FCM collapse/tag и клиентская дедупликация не дают exactly-once.
Пока Firebase не создан, оставьте оба флага `PUSH_ENABLED` выключенными:
приложение работает без SDK-инициализации, очередь не потребляется воркером.

This guide deploys the API to Koyeb, PostgreSQL to Neon, and Redis to Upstash.
The Docker image applies Alembic migrations and runs the idempotent bootstrap command before starting the API.

## 1. Push the repository to GitHub

Create an empty private repository on GitHub. From this repository's root run:

```bash
git add .
git commit -m "backend: prepare cloud deployment"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/project_oraz.git
git push -u origin main
```

Do not commit `.env`; it is already ignored.

## 2. Create PostgreSQL in Neon

1. Sign in at <https://console.neon.tech> and create a project.
2. Open **Connect**, select the database and copy the connection string.
3. Change only the URL scheme from `postgresql://` to `postgresql+asyncpg://`.
4. Keep all query parameters, including `sslmode=require` if Neon supplied it.

The resulting value will be used as `M1917_DATABASE_URL`.

## 3. Create Redis in Upstash

1. Sign in at <https://console.upstash.com> and create a Redis database.
2. Choose the region closest to the Koyeb service (Frankfurt is preferred).
3. Open the database details and copy the TLS Redis URL beginning with `rediss://`.

The value will be used as `M1917_REDIS_URL`. Do not use the REST URL or REST token.

## 4. Generate secrets locally

Run these commands and save each output separately:

```bash
openssl rand -hex 32
openssl rand -hex 32
cd backend && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

They are, in order, `M1917_JWT_SECRET`, `M1917_OTP_PEPPER`, and
`M1917_PII_ENCRYPTION_KEY`. Never put them in GitHub files.

## 5. Create the Koyeb service

1. Sign in at <https://app.koyeb.com> and choose **Create Web Service**.
2. Select **GitHub**, authorize the private repository, and select branch `main`.
3. Choose **Dockerfile** as the builder.
4. Set the work directory/build context to `backend` and the Dockerfile to `Dockerfile`.
5. Choose the free instance in Frankfurt.
6. Set the exposed port to `8000` and protocol to HTTP.
7. Set the health check path to `/health/live`.
8. Add the environment variables below, marking secrets as secret values.

| Variable | Value |
| --- | --- |
| `M1917_ENVIRONMENT` | `development` for a private demo; see production note below |
| `M1917_DATABASE_URL` | Neon URL with `postgresql+asyncpg://` |
| `M1917_REDIS_URL` | Upstash TLS URL beginning with `rediss://` |
| `M1917_JWT_SECRET` | first generated secret |
| `M1917_OTP_PEPPER` | second generated secret |
| `M1917_PII_ENCRYPTION_KEY` | generated Fernet key |
| `M1917_LOGIN_PHONE_HOUR_LIMIT` | `10` |
| `M1917_CORS_ORIGINS` | Flutter Web origin, for example `https://app.example.com` |
| `M1917_BOOTSTRAP_USERS_JSON` | initial users JSON described below |

For an initial test account, set `M1917_BOOTSTRAP_USERS_JSON` to a single-line JSON value:

```json
[{"phone":"+77000000001","password":"replace-with-a-unique-password","role":"BOSS","full_name":"Owner","employee_id":"BOSS-001","city":"Almaty"}]
```

Deploy the service. The first startup can take longer because migrations are applied.

## 6. Verify the deployment

Replace `YOUR_APP` with the Koyeb domain:

```bash
curl https://YOUR_APP.koyeb.app/health/live
curl https://YOUR_APP.koyeb.app/health/ready
```

Both requests should return an `ok`/`ready` status. API documentation is at:

```text
https://YOUR_APP.koyeb.app/api/v1/docs
```

Log in using the bootstrap phone number and its password. Passwords are stored as salted scrypt hashes. Existing accounts require a password to be provisioned; see the root README for the interactive reset command.

## Production note

Use `M1917_ENVIRONMENT=production` with independent JWT and client-hashing secrets and a unique PII encryption key. SMS gateway configuration is not required.

The free Koyeb instance sleeps after inactivity, so the first request may be slow.
Neon and Upstash can also suspend or limit inactive/free resources. Use paid plans for
a service that requires production availability.
