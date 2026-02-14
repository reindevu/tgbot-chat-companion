from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import ConfigError, Settings, load_settings
from db import Database
from llm_client import LLMError, PolzaLLMClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    db: Database
    llm: PolzaLLMClient


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not await _ensure_owner(update, services.settings):
        return

    services.db.get_or_create_active_session(services.settings.owner_telegram_id)
    await update.effective_message.reply_text(
        "Привет. Это приватный companion-бот.\n"
        "Команды: /help, /reset, /export [N], /health"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not await _ensure_owner(update, services.settings):
        return

    await update.effective_message.reply_text(
        "Доступные команды:\n"
        "/start - приветствие\n"
        "/help - справка\n"
        "/reset - новая сессия, контекст обнуляется\n"
        "/export [N] - выгрузка последних N сообщений (по умолчанию 20)\n"
        "/health - статус БД и активной сессии"
    )


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not await _ensure_owner(update, services.settings):
        return

    session_id = services.db.create_new_session(services.settings.owner_telegram_id)
    await update.effective_message.reply_text(
        f"Контекст сброшен. Создана новая сессия: {session_id}."
    )


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not await _ensure_owner(update, services.settings):
        return

    requested = 20
    if context.args:
        try:
            requested = int(context.args[0])
        except ValueError:
            await update.effective_message.reply_text("N должно быть целым числом")
            return
    limit = max(1, min(requested, 200))

    session_id = services.db.get_or_create_active_session(services.settings.owner_telegram_id)
    rows = services.db.export_recent_messages(session_id, limit)
    if not rows:
        await update.effective_message.reply_text("История пустая")
        return

    lines = [f"[{row['created_at']}] {row['role']}: {row['content']}" for row in rows]
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "\n... (truncated)"

    await update.effective_message.reply_text(text)


async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not await _ensure_owner(update, services.settings):
        return

    status = services.db.health(services.settings.owner_telegram_id)
    await update.effective_message.reply_text(json.dumps(status, ensure_ascii=False, indent=2))


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not await _ensure_owner(update, services.settings):
        return

    message = update.effective_message
    user_text = (message.text or "").strip()
    if not user_text:
        return

    session_id = services.db.get_or_create_active_session(services.settings.owner_telegram_id)
    services.db.add_message(session_id=session_id, role="user", content=user_text)

    history = services.db.get_context_messages(
        session_id=session_id,
        limit=services.settings.max_context_messages,
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": services.settings.system_prompt}]
    messages.extend({"role": m.role, "content": m.content} for m in history)

    typing_task: asyncio.Task[None] | None = None
    if message.chat_id:
        typing_task = asyncio.create_task(_typing_loop(context, message.chat_id))

    try:
        assistant_text, meta = await services.llm.generate(messages)
    except LLMError:
        await message.reply_text("Сервис временно недоступен, попробуй ещё раз.")
        return
    finally:
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

    services.db.add_message(
        session_id=session_id,
        role="assistant",
        content=assistant_text,
        meta=meta,
    )
    await message.reply_text(assistant_text)


async def proactive_check_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    services = _services(context)
    if not services.settings.auto_message_enabled:
        return

    session_id = services.db.get_or_create_active_session(services.settings.owner_telegram_id)
    last_user = services.db.get_last_user_message(session_id)
    if not last_user:
        return

    if services.db.has_proactive_after(session_id, last_user.id):
        return

    now = datetime.now(tz=timezone.utc)
    idle_seconds = (now - last_user.created_at).total_seconds()
    required_idle_seconds = _deterministic_idle_seconds(
        message_id=last_user.id,
        min_hours=services.settings.auto_message_idle_hours_min,
        max_hours=services.settings.auto_message_idle_hours_max,
    )

    if idle_seconds < required_idle_seconds:
        return

    history = services.db.get_context_messages(
        session_id=session_id,
        limit=services.settings.max_context_messages,
    )
    prompt_messages: list[dict[str, str]] = [
        {"role": "system", "content": services.settings.system_prompt},
    ]
    prompt_messages.extend({"role": m.role, "content": m.content} for m in history)
    prompt_messages.append(
        {
            "role": "user",
            "content": (
                "Был длительный перерыв в переписке. "
                "Напиши короткое тёплое сообщение первой (1-2 предложения, без навязчивости), "
                "чтобы мягко начать диалог снова."
            ),
        }
    )

    try:
        assistant_text, meta = await services.llm.generate(prompt_messages)
    except LLMError:
        logger.exception("Failed to generate proactive message")
        return

    meta = {
        **meta,
        "proactive": True,
        "trigger": "inactivity",
        "idle_seconds": int(idle_seconds),
        "required_idle_seconds": int(required_idle_seconds),
    }
    services.db.add_message(
        session_id=session_id,
        role="assistant",
        content=assistant_text,
        meta=meta,
        is_proactive=True,
    )

    try:
        await context.bot.send_message(
            chat_id=services.settings.owner_telegram_id,
            text=assistant_text,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Unable to send proactive message to owner")


async def _ensure_owner(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return False

    if user.id == settings.owner_telegram_id:
        return True

    if settings.unauthorized_mode == "deny":
        await message.reply_text(settings.unauthorized_message or "Access denied")
    return False



def _deterministic_idle_seconds(message_id: int, min_hours: float, max_hours: float) -> float:
    if min_hours == max_hours:
        return min_hours * 3600
    span = max_hours - min_hours
    hashed = (message_id * 2654435761) % 1000
    ratio = hashed / 999.0
    return (min_hours + span * ratio) * 3600


async def _typing_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    while True:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:  # noqa: BLE001
            logger.debug("Unable to send typing action", exc_info=True)
        await asyncio.sleep(4)



def _services(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["services"]



def build_application(services: Services) -> Application:
    app = ApplicationBuilder().token(services.settings.telegram_bot_token).build()
    app.bot_data["services"] = services

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("health", health_cmd))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_message))

    if services.settings.auto_message_enabled:
        app.job_queue.run_repeating(
            proactive_check_job,
            interval=services.settings.auto_message_check_minutes * 60,
            first=30,
            name="proactive-inactivity-check",
        )
        logger.info(
            "Auto message enabled: idle %.2f-%.2f h, check each %s min",
            services.settings.auto_message_idle_hours_min,
            services.settings.auto_message_idle_hours_max,
            services.settings.auto_message_check_minutes,
        )

    return app



def main() -> None:
    load_dotenv()
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"Config error: {exc}") from exc

    db = Database(settings.sqlite_path)
    db.init()

    llm = PolzaLLMClient(
        api_key=settings.polza_api_key,
        base_url=settings.polza_base_url,
        model=settings.polza_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

    services = Services(settings=settings, db=db, llm=llm)
    app = build_application(services)

    logger.info("Bot started with long polling")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
