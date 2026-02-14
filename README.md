# private-telegram-llm-companion

Приватный Telegram-бот для одного владельца с хранением контекста в БД и интеграцией с Polza.ai (OpenAI-compatible API).

## Возможности

- Доступ только для `OWNER_TELEGRAM_ID`.
- Одна фиксированная персона через `SYSTEM_PROMPT` в `src/config.py`.
- Полная история сообщений хранится в SQLite.
- Для LLM отправляется окно контекста `MAX_CONTEXT_MESSAGES`.
- Команда `/reset` создаёт новую активную сессию (история старых сессий сохраняется).
- Интеграция с Polza.ai через `base_url=https://api.polza.ai/api/v1`.
- Опционально: бот может сам писать первым после долгой паузы в общении.

## Ограничения

- MVP работает с `SQLite` (`DATABASE_URL=sqlite:///...`).
- Режим получения апдейтов: `long polling`.
- Один бот = один владелец.

## Команды

- `/start` - приветствие и краткая справка.
- `/help` - список команд.
- `/reset` - сброс контекста (новая сессия).
- `/export [N]` - выгрузка последних `N` сообщений текущей сессии (по умолчанию 20).
- `/health` - статус БД и активной сессии (без секретов).

## Структура проекта

```text
/src
  bot.py
  llm_client.py
  db.py
  config.py
.env.example
requirements.txt
README.md
```

## Быстрый запуск (локально)

1. Создай и активируй виртуальное окружение:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Установи зависимости:

```bash
pip install -r requirements.txt
```

3. Подготовь `.env`:

```bash
cp .env.example .env
```

4. Заполни обязательные переменные в `.env`:

- `TELEGRAM_BOT_TOKEN`
- `OWNER_TELEGRAM_ID`
- `POLZA_API_KEY`
- `POLZA_BASE_URL` (оставь `https://api.polza.ai/api/v1`)
- `POLZA_MODEL`
- `DATABASE_URL` (например `sqlite:///data.db`)
- `MAX_CONTEXT_MESSAGES`
- `LLM_TIMEOUT_SECONDS`

`SYSTEM_PROMPT` редактируется в коде:

- `/Users/reindevu/Documents/Github/tgbot-chat-companion/src/config.py`

5. Запусти бота:

```bash
python src/bot.py
```

## Как работает диалог

1. Входящее сообщение проверяется по `message.from.id`.
2. Если пользователь не владелец, бот игнорирует запрос или отвечает `Access denied` (см. `UNAUTHORIZED_MODE`).
3. Сообщение владельца сохраняется в `messages`.
4. Из БД берётся активная сессия и последние `MAX_CONTEXT_MESSAGES`.
5. В LLM отправляется:
   - `system` = константа `SYSTEM_PROMPT` из `src/config.py`
   - история (`user`/`assistant`) текущей сессии
6. Ответ LLM сохраняется в БД и отправляется в Telegram.

## Авто-сообщения после паузы

Бот может сам инициировать диалог, если вы долго не писали.

Переменные:

- `AUTO_MESSAGE_ENABLED=true|false`
- `AUTO_MESSAGE_IDLE_HOURS_MIN` (например `1`)
- `AUTO_MESSAGE_IDLE_HOURS_MAX` (например `3`)
- `AUTO_MESSAGE_CHECK_MINUTES` (например `10`)

Логика:

- Проверка запускается раз в `AUTO_MESSAGE_CHECK_MINUTES`.
- Если с последнего `user`-сообщения прошла пауза в диапазоне `MIN..MAX` часов, бот отправляет одно инициативное сообщение.
- До следующего вашего сообщения бот не шлёт повторные инициативные сообщения (анти-спам).

Важно:

- Telegram позволяет боту писать первым только если вы уже начали чат с ботом ранее (`/start`).

## Схема БД

Таблица `sessions`:

- `id` (PK)
- `owner_telegram_id` (INT)
- `created_at`
- `is_active` (BOOL)

Таблица `messages`:

- `id` (PK)
- `session_id` (FK -> sessions.id)
- `role` (`system|user|assistant`, фактически используются `user|assistant`)
- `content` (TEXT)
- `created_at`
- `meta_json` (опционально: model, latency, token_usage, request_id)
- `is_proactive` (BOOL, 1 если инициативное сообщение)

## Режимы для неавторизованных пользователей

- `UNAUTHORIZED_MODE=deny` - ответить `UNAUTHORIZED_MESSAGE`.
- `UNAUTHORIZED_MODE=ignore` - молча игнорировать.

## Примечания

- Для production на VPS добавь systemd/supervisor и хранение БД в persistent volume.
- При необходимости Postgres можно добавить отдельным адаптером БД без изменения бизнес-логики.
