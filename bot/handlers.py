from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from html import escape
import logging
from typing import Iterable, Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
)

from bot.services.redis_cache import (
    check_rate_limit,
    get_linked_message_id,
    pop_last_reopen_msg,
    pop_start_payload,
    save_message_link,
    set_last_reopen_msg,
    set_start_payload,
)
from bot.services.topics import (
    TICKET_IN_PROGRESS,
    TICKET_NEW,
    TICKET_WAITING_USER,
    close_all_support_topics,
    close_support_topic,
    close_support_topic_by_user,
    get_by_topic_id,
    get_or_create_support_topic,
    is_close_ticket_callback,
    is_take_ticket_callback,
    open_database_session,
    parse_ticket_callback,
    recreate_support_topic,
    refresh_topic_header_for_user,
    reopen_support_topic_by_user,
    sync_topic_status_from_ticket,
    topic_close_keyboard,
    topic_id_from_close_callback,
)
from config.messages import get_messages, msg
from config.settings import Settings
from database import repositories

logger = logging.getLogger(__name__)

router = Router(name="support")
TICKET_CLOSED_MESSAGE = msg("ticket_closed_topic")
USER_TICKET_CLOSED_MESSAGE = msg("ticket_closed_user")
REOPEN_TICKET_CALLBACK_DATA = "reopen_ticket"
CSAT_CALLBACK_PREFIX = "csat:"
INTERNAL_NOTE_PREFIX = "!"


# --- Private User Handlers ---


@router.message(F.chat.type == "private", CommandStart())
async def start(message: Message, command: CommandObject) -> None:
    if message.from_user and not message.from_user.is_bot and command.args:
        await set_start_payload(message.from_user.id, command.args)
    await send_welcome_message(message)


@router.message(F.chat.type == "private", Command("end"))
async def close_private_support_topic_command(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return

    await delete_message_safely(bot, message)
    async with open_database_session() as session:
        topic, changed = await close_support_topic_by_user(
            session,
            bot,
            settings.support_chat_id,
            message.from_user.id,
        )
        latest = await repositories.get_latest_ticket_by_user(session, message.from_user.id)
        ticket_id = int(latest.id) if latest is not None else None

    if topic is None:
        await send_user_ticket_closed_message(
            bot,
            message.from_user.id,
            msg("ticket_closed_not_found"),
        )
        return

    if changed:
        await send_topic_status_message(
            bot,
            settings,
            int(topic.topic_id),
            msg("user_closed_topic"),
        )
    await send_user_ticket_closed_message(
        bot,
        message.from_user.id,
        USER_TICKET_CLOSED_MESSAGE,
        ticket_id=ticket_id if changed else None,
    )


@router.message(F.chat.type == "private")
async def forward_private_message(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if should_ignore_private_message(message):
        return

    user_id = message.from_user.id  # type: ignore[union-attr]
    if not await check_rate_limit(user_id):
        await message.answer(msg("rate_limit_exceeded"))
        return

    async with open_database_session() as session:
        existing = await repositories.get_by_user_id(session, user_id)
        if existing is not None and getattr(existing, "is_banned", False):
            return

    payload = await pop_start_payload(user_id)
    topic = await get_private_support_topic(message, bot, settings, source_payload=payload)
    await _clear_stale_reopen_keyboard(bot, user_id)

    reply_to_id = await _resolve_reply_id(message.chat.id, message)
    try:
        copied = await copy_private_message_to_topic(
            message, bot, settings, topic, reply_to_message_id=reply_to_id
        )
    except TelegramBadRequest as exc:
        if not is_message_thread_not_found(exc):
            logger.warning("Failed to copy private message from %s: %s", user_id, exc)
            await message.answer(msg("delivery_failed_user"))
            return
        topic = await recreate_private_support_topic(message, bot, settings, topic)
        copied = await copy_private_message_to_topic(
            message, bot, settings, topic, reply_to_message_id=None
        )

    await save_message_link(
        message.chat.id,
        message.message_id,
        settings.support_chat_id,
        copied.message_id,
    )
    await mark_user_message_delivered(bot, message)
    await _on_user_message_forwarded(bot, settings, user_id, topic)


@router.edited_message(F.chat.type == "private")
async def sync_edited_private_message(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return
    linked_id = await get_linked_message_id(message.chat.id, message.message_id)
    if linked_id is None:
        return
    await _edit_linked_message(bot, settings.support_chat_id, linked_id, message)


# --- Private Callbacks ---


@router.callback_query(
    lambda cb: cb.data == REOPEN_TICKET_CALLBACK_DATA
    or bool(cb.data and cb.data.startswith(f"{REOPEN_TICKET_CALLBACK_DATA}:"))
)
async def reopen_private_support_topic_callback(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
) -> None:
    if callback.from_user.is_bot:
        await callback.answer()
        return
    if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
        await callback.answer("Кнопка доступна только в диалоге с ботом.", show_alert=True)
        return

    async with open_database_session() as session:
        existing = await repositories.get_by_user_id(session, callback.from_user.id)
        if existing is not None and getattr(existing, "is_banned", False):
            await callback.answer("Доступ в поддержку ограничен.", show_alert=True)
            return
        topic, reopened = await reopen_support_topic_by_user(
            session,
            bot,
            settings.support_chat_id,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name,
        )

    if not reopened:
        await remove_inline_keyboard(callback.message)
        await callback.answer("Обращение уже открыто.")
        return

    await callback.answer("Обращение открыто.")
    await remove_inline_keyboard(callback.message)
    await send_topic_status_message(
        bot,
        settings,
        int(topic.topic_id),
        msg("user_reopened_topic"),
    )
    await send_welcome_message(callback.message)


@router.callback_query(lambda cb: bool(cb.data and cb.data.startswith(CSAT_CALLBACK_PREFIX)))
async def csat_rating_callback(callback: CallbackQuery) -> None:
    if callback.from_user.is_bot or not callback.data:
        await callback.answer()
        return
    parts = callback.data[len(CSAT_CALLBACK_PREFIX) :].split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await callback.answer()
        return
    ticket_id, score = int(parts[0]), int(parts[1])
    if score < 1 or score > 5:
        await callback.answer()
        return

    async with open_database_session() as session:
        ticket = await repositories.get_ticket_by_id(session, ticket_id)
        if ticket is None or int(ticket.user_id) != callback.from_user.id:
            await callback.answer("Обращение не найдено.", show_alert=True)
            return
        if ticket.csat_score is not None:
            await callback.answer("Оценка уже сохранена.")
            return
        await repositories.update_ticket(session, ticket, csat_score=score)
        await repositories.record_ticket_event(
            session,
            ticket_id=ticket_id,
            actor_type="user",
            actor_id=callback.from_user.id,
            event_type="csat_rated",
            new_value=str(score),
        )

    await callback.answer(msg("csat_thanks", score=score))
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(
                reply_markup=reopen_ticket_keyboard(ticket_id=ticket_id)
            )
        except TelegramBadRequest:
            pass


# --- Support Chat Forum UI Sync ---


@router.message(F.chat.id, F.forum_topic_closed)
async def sync_forum_topic_closed(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if message.chat.id != settings.support_chat_id or message.message_thread_id is None:
        return
    if message.from_user and message.from_user.is_bot:
        return

    actor_id = message.from_user.id if message.from_user else None
    found, changed, user_id, ticket_id = await close_topic(
        message.message_thread_id,
        bot,
        settings,
        closed_by=f"ui:{actor_id}" if actor_id else "ui",
        actor_id=actor_id,
        close_forum=False,
    )
    if found and changed and user_id is not None:
        await send_user_ticket_closed_message(bot, user_id, ticket_id=ticket_id)


@router.message(F.chat.id, F.forum_topic_reopened)
async def sync_forum_topic_reopened(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if message.chat.id != settings.support_chat_id or message.message_thread_id is None:
        return
    if message.from_user and message.from_user.is_bot:
        return

    actor_id = message.from_user.id if message.from_user else None
    async with open_database_session() as session:
        topic = await repositories.get_by_topic_id(session, message.message_thread_id)
        if topic is None or is_open_topic(topic):
            return
        await reopen_support_topic_by_user(
            session,
            bot,
            settings.support_chat_id,
            int(topic.user_id),
            topic.username,
            topic.full_name,
            actor_type="operator",
            actor_id=actor_id,
        )


# --- Support Chat Callbacks ---


@router.callback_query(lambda callback: is_close_ticket_callback(callback.data))
async def close_support_topic_callback(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
) -> None:
    if not _can_operate_in_support_chat(callback.from_user.id, callback.message, settings):
        await callback.answer("Недостаточно прав или неверный чат.", show_alert=True)
        return

    topic_id, expected_ticket_id = parse_ticket_callback(
        callback.data or "", "close_ticket:"
    )
    if expected_ticket_id is not None:
        async with open_database_session() as session:
            topic = await repositories.get_by_topic_id(session, topic_id)
            active = (
                await repositories.get_active_ticket_by_user(session, int(topic.user_id))
                if topic is not None
                else None
            )
            if active is None or int(active.id) != expected_ticket_id:
                await remove_inline_keyboard(callback.message)  # type: ignore[arg-type]
                await callback.answer("Кнопка устарела: обращение уже закрыто.")
                return

    found, changed, user_id, ticket_id = await close_topic(
        topic_id,
        bot,
        settings,
        closed_by=f"operator:{callback.from_user.id}",
        actor_id=callback.from_user.id,
    )
    if not found:
        await callback.answer("Topic не найден в базе.", show_alert=True)
        return
    if not changed:
        await callback.answer("Обращение уже закрыто.")
        return

    await callback.answer("Обращение закрыто.")
    await send_topic_status_message(bot, settings, topic_id, TICKET_CLOSED_MESSAGE)
    if user_id is not None:
        await send_user_ticket_closed_message(bot, user_id, ticket_id=ticket_id)


@router.callback_query(lambda callback: is_take_ticket_callback(callback.data))
async def take_support_topic_callback(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
) -> None:
    if not _can_operate_in_support_chat(callback.from_user.id, callback.message, settings):
        await callback.answer("Недостаточно прав или неверный чат.", show_alert=True)
        return

    topic_id, expected_ticket_id = parse_ticket_callback(
        callback.data or "", "take_ticket:"
    )
    async with open_database_session() as session:
        topic = await repositories.get_by_topic_id(session, topic_id)
        if topic is None:
            await callback.answer("Topic не найден.", show_alert=True)
            return
        ticket = await repositories.get_active_ticket_by_user(session, int(topic.user_id))
        if ticket is None or (
            expected_ticket_id is not None and int(ticket.id) != expected_ticket_id
        ):
            await remove_inline_keyboard(callback.message)  # type: ignore[arg-type]
            await callback.answer("Обращение уже закрыто.")
            return
        await _assign_ticket(
            session, bot, settings.support_chat_id, topic, ticket, callback.from_user.id
        )

    await callback.answer("Обращение взято в работу.")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(
                reply_markup=topic_close_keyboard(
                    topic_id, ticket_id=int(ticket.id), assigned=True
                )
            )
        except TelegramBadRequest:
            pass
    await send_topic_status_message(
        bot,
        settings,
        topic_id,
        f"Оператор {callback.from_user.id} взял обращение в работу.",
    )


# --- Support Chat Operator Commands ---


@router.message(F.chat.id, Command("end"))
async def close_support_topic_command(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        if (
            message.chat.id == settings.support_chat_id
            and message.message_thread_id is None
        ):
            await message.answer("Команда /end должна быть отправлена внутри topic обращения.")
        elif message.chat.id == settings.support_chat_id and message.from_user:
            await message.answer("Нет прав на закрытие обращения.")
        return

    found, changed, user_id, ticket_id = await close_topic(
        message.message_thread_id,  # type: ignore[arg-type]
        bot,
        settings,
        closed_by=f"operator:{message.from_user.id}",  # type: ignore[union-attr]
        actor_id=message.from_user.id,  # type: ignore[union-attr]
    )
    if not found:
        await message.answer("Topic не найден в базе.")
        return
    if not changed:
        await message.answer("Обращение уже закрыто.")
        return

    await send_topic_status_message(
        bot, settings, message.message_thread_id, TICKET_CLOSED_MESSAGE  # type: ignore[arg-type]
    )
    if user_id is not None:
        await send_user_ticket_closed_message(bot, user_id, ticket_id=ticket_id)


@router.message(F.chat.id, Command("all_end"))
async def close_all_support_topics_command(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if message.chat.id != settings.support_chat_id:
        return
    if message.message_thread_id is None:
        await message.answer("Команда /all_end должна быть отправлена внутри topic поддержки.")
        return
    if message.from_user is None or not is_allowed_operator(message.from_user.id, settings):
        await message.answer("Нет прав на закрытие обращений.")
        return

    async with open_database_session() as session:
        closed_count, renamed_count, user_ids = await close_all_support_topics(
            session,
            bot,
            settings.support_chat_id,
            operator_id=message.from_user.id,
        )
    await message.answer(
        f"Закрыто обращений: {closed_count}. Переименовано topic: {renamed_count}."
    )
    if user_ids:
        asyncio.create_task(_broadcast_ticket_closed(bot, user_ids))


@router.message(F.chat.id, Command("take"))
async def take_ticket_command(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        return
    operator_id = message.from_user.id  # type: ignore[union-attr]
    async with open_database_session() as session:
        topic, ticket = await _get_topic_and_active_ticket(
            session, message.message_thread_id  # type: ignore[arg-type]
        )
        if topic is None or ticket is None:
            await message.answer("Активное обращение не найдено.")
            return
        await _assign_ticket(
            session, bot, settings.support_chat_id, topic, ticket, operator_id
        )
    await message.answer(f"Обращение #{ticket.id} закреплено за оператором {operator_id}.")


@router.message(F.chat.id, Command("assign"))
async def assign_ticket_command(
    message: Message,
    command: CommandObject,
    bot: Bot,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        return

    target_id: Optional[int] = None
    if command.args and command.args.strip().isdigit():
        target_id = int(command.args.strip())
    elif message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id

    if target_id is None:
        await message.answer("Укажите ID оператора: /assign <user_id> или ответьте на его сообщение.")
        return

    async with open_database_session() as session:
        topic, ticket = await _get_topic_and_active_ticket(
            session, message.message_thread_id  # type: ignore[arg-type]
        )
        if topic is None or ticket is None:
            await message.answer("Активное обращение не найдено.")
            return
        await _assign_ticket(
            session,
            bot,
            settings.support_chat_id,
            topic,
            ticket,
            target_id,
            actor_id=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"Обращение #{ticket.id} передано оператору {target_id}.")


@router.message(F.chat.id, Command("waiting"))
async def waiting_user_command(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        return
    async with open_database_session() as session:
        topic, ticket = await _get_topic_and_active_ticket(
            session, message.message_thread_id  # type: ignore[arg-type]
        )
        if topic is None or ticket is None:
            await message.answer("Активное обращение не найдено.")
            return
        old_status = ticket.status
        await repositories.update_ticket(session, ticket, status=TICKET_WAITING_USER)
        await repositories.record_ticket_event(
            session,
            ticket_id=int(ticket.id),
            actor_type="operator",
            actor_id=message.from_user.id,  # type: ignore[union-attr]
            event_type="status_changed",
            old_value=old_status,
            new_value=TICKET_WAITING_USER,
        )
        await sync_topic_status_from_ticket(
            bot, settings.support_chat_id, topic, TICKET_WAITING_USER
        )
    await message.answer(f"Статус обращения #{ticket.id}: ожидание ответа клиента.")


@router.message(F.chat.id, Command("tag"))
async def tag_ticket_command(
    message: Message,
    command: CommandObject,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        return
    raw_args = (command.args or "").strip()
    if not raw_args:
        await message.answer("Использование: /tag billing,vip или /tag clear")
        return

    new_tags: Optional[str]
    if raw_args.lower() == "clear":
        new_tags = None
    else:
        cleaned = [t.strip().lstrip("#").lower() for t in raw_args.replace(" ", ",").split(",")]
        unique_tags = sorted({t for t in cleaned if t})
        new_tags = ",".join(unique_tags) if unique_tags else None

    async with open_database_session() as session:
        _, ticket = await _get_topic_and_active_ticket(
            session, message.message_thread_id  # type: ignore[arg-type]
        )
        if ticket is None:
            await message.answer("Активное обращение не найдено.")
            return
        old_tags = ticket.tags
        await repositories.update_ticket(session, ticket, tags=new_tags)
        await repositories.record_ticket_event(
            session,
            ticket_id=int(ticket.id),
            actor_type="operator",
            actor_id=message.from_user.id,  # type: ignore[union-attr]
            event_type="tags_updated",
            old_value=old_tags,
            new_value=new_tags,
        )
    await message.answer(f"Теги обращения #{ticket.id}: {new_tags or 'очищены'}.")


@router.message(F.chat.id, Command("note"))
async def customer_note_command(
    message: Message,
    command: CommandObject,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        return
    raw_args = (command.args or "").strip()
    async with open_database_session() as session:
        topic = await repositories.get_by_topic_id(
            session, message.message_thread_id  # type: ignore[arg-type]
        )
        if topic is None:
            await message.answer("Topic не найден в базе.")
            return
        if not raw_args:
            await message.answer(f"Заметка о клиенте: {topic.notes or 'отсутствует'}")
            return
        new_note = None if raw_args.lower() == "clear" else raw_args
        await repositories.update_support_topic(session, topic, notes=new_note)
    await message.answer("Заметка о клиенте обновлена." if new_note else "Заметка очищена.")


@router.message(F.chat.id, Command("info"))
async def customer_info_command(
    message: Message,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        return
    async with open_database_session() as session:
        topic = await repositories.get_by_topic_id(
            session, message.message_thread_id  # type: ignore[arg-type]
        )
        if topic is None:
            await message.answer("Topic не найден в базе.")
            return
        user_id = int(topic.user_id)
        total_tickets = await repositories.count_user_tickets(session, user_id)
        ticket = await repositories.get_active_ticket_by_user(session, user_id)
        if ticket is None:
            ticket = await repositories.get_latest_ticket_by_user(session, user_id)

    lines = [
        f"Клиент ID: {user_id}",
        f"Username: @{topic.username}" if topic.username else "Username: -",
        f"Имя: {topic.full_name or '-'}",
        f"Бан: {'Да (' + (topic.ban_reason or 'без причины') + ')' if topic.is_banned else 'Нет'}",
        f"Всего обращений: {total_tickets}",
        f"Заметка: {topic.notes or '-'}",
    ]
    if ticket is not None:
        lines.extend(
            [
                f"Последний тикет: #{ticket.id} ({ticket.status})",
                f"Оператор: {ticket.assigned_operator_id or '-'}",
                f"Теги: {ticket.tags or '-'}",
                f"Источник: {ticket.source_payload or '-'}",
                f"CSAT: {ticket.csat_score or '-'}",
            ]
        )
    await message.answer("\n".join(lines))


@router.message(F.chat.id, Command("ban"))
async def ban_user_command(
    message: Message,
    command: CommandObject,
    bot: Bot,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        return
    reason = (command.args or "").strip() or "Заблокирован оператором"
    operator_id = message.from_user.id  # type: ignore[union-attr]
    async with open_database_session() as session:
        topic = await repositories.get_by_topic_id(
            session, message.message_thread_id  # type: ignore[arg-type]
        )
        if topic is None:
            await message.answer("Topic не найден в базе.")
            return
        await repositories.update_support_topic(
            session, topic, is_banned=True, ban_reason=reason[:512]
        )
        await close_support_topic(
            session,
            bot,
            settings.support_chat_id,
            int(topic.topic_id),
            closed_by=f"ban:{operator_id}",
            actor_type="operator",
            actor_id=operator_id,
        )
    await message.answer(f"Пользователь {topic.user_id} заблокирован. Причина: {reason}")


@router.message(F.chat.id, Command("unban"))
async def unban_user_command(
    message: Message,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        return
    async with open_database_session() as session:
        topic = await repositories.get_by_topic_id(
            session, message.message_thread_id  # type: ignore[arg-type]
        )
        if topic is None:
            await message.answer("Topic не найден в базе.")
            return
        await repositories.update_support_topic(
            session, topic, is_banned=False, ban_reason=None
        )
    await message.answer(f"Пользователь {topic.user_id} разблокирован.")


@router.message(F.chat.id, Command("stats"))
async def crm_stats_command(message: Message, settings: Settings) -> None:
    if not _validate_operator_command(message, settings, require_thread=False):
        return
    async with open_database_session() as session:
        stats = await repositories.get_crm_stats(session)

    by_status = stats["by_status"]
    frt = f"{stats['avg_frt_sec']} сек" if stats["avg_frt_sec"] is not None else "-"
    csat = f"{stats['avg_csat']}/5" if stats["avg_csat"] is not None else "-"
    lines = [
        "Статистика CRM:",
        f"Всего тикетов: {stats['total_tickets']}",
        f"Новых (new): {by_status.get(TICKET_NEW, 0)}",
        f"В работе (in_progress): {by_status.get(TICKET_IN_PROGRESS, 0)}",
        f"Ожидают клиента (waiting_user): {by_status.get(TICKET_WAITING_USER, 0)}",
        f"Закрытых (closed): {by_status.get('closed', 0)}",
        f"Средний первый ответ (FRT): {frt}",
        f"Средний CSAT: {csat}",
        f"Заблокированных клиентов: {stats['banned_users']}",
    ]
    await message.answer("\n".join(lines))


@router.message(F.chat.id, Command("macros"))
async def list_macros_command(message: Message, settings: Settings) -> None:
    if not _validate_operator_command(message, settings, require_thread=False):
        return
    async with open_database_session() as session:
        macros = await repositories.list_canned_responses(session)
    if not macros:
        await message.answer("Список шаблонов пуст. Добавить: /macro_add <slug> <текст>")
        return
    lines = ["Доступные шаблоны (/r <slug>):"]
    lines.extend(f"- {m.slug}: {m.content[:60]}" for m in macros)
    await message.answer("\n".join(lines))


@router.message(F.chat.id, Command("macro_add"))
async def add_macro_command(
    message: Message,
    command: CommandObject,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=False):
        return
    args = (command.args or "").strip().split(maxsplit=1)
    if len(args) != 2:
        await message.answer("Использование: /macro_add <slug> <текст>")
        return
    slug, content = args[0], args[1]
    async with open_database_session() as session:
        macro = await repositories.upsert_canned_response(
            session,
            slug=slug,
            content=content,
            created_by=message.from_user.id,  # type: ignore[union-attr]
        )
    await message.answer(f"Шаблон '{macro.slug}' сохранён.")


@router.message(F.chat.id, Command("macro_del"))
async def del_macro_command(
    message: Message,
    command: CommandObject,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=False):
        return
    slug = (command.args or "").strip()
    if not slug:
        await message.answer("Использование: /macro_del <slug>")
        return
    async with open_database_session() as session:
        deleted = await repositories.delete_canned_response(session, slug)
    await message.answer(
        f"Шаблон '{slug}' удалён." if deleted else f"Шаблон '{slug}' не найден."
    )


@router.message(F.chat.id, Command("r"))
async def send_macro_command(
    message: Message,
    command: CommandObject,
    bot: Bot,
    settings: Settings,
) -> None:
    if not _validate_operator_command(message, settings, require_thread=True):
        return
    slug = (command.args or "").strip()
    if not slug:
        await message.answer("Использование: /r <slug>")
        return

    async with open_database_session() as session:
        macro = await repositories.get_canned_response(session, slug)
        if macro is None:
            await message.answer(f"Шаблон '{slug}' не найден.")
            return
        topic = await get_by_topic_id(
            session, message.message_thread_id  # type: ignore[arg-type]
        )
        if topic is None:
            await message.answer("Topic не найден в базе.")
            return
        if not is_open_topic(topic):
            await message.answer(msg("operator_closed_reply"))
            return

        sent = await _deliver_to_user(
            bot,
            settings,
            topic,
            message,
            text_override=macro.content,
        )
        if sent is not None:
            await _on_operator_message_sent(
                session,
                bot,
                settings.support_chat_id,
                topic,
                message.from_user.id,  # type: ignore[union-attr]
            )
            await message.answer(f"[Отправлено клиенту по шаблону {macro.slug}]:\n{macro.content}")


# --- Support Chat Relay & Edits ---


@router.message(F.chat.id)
async def forward_support_topic_message(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if message.chat.id != settings.support_chat_id:
        return
    if message.message_thread_id is None:
        return
    if should_ignore_support_message(message, settings):
        return

    raw_text = message.text or message.caption or ""
    if raw_text.startswith(INTERNAL_NOTE_PREFIX):
        return

    async with open_database_session() as session:
        topic = await get_by_topic_id(session, message.message_thread_id)
        if topic is None:
            logger.info(
                "Support topic message ignored: topic_id %s not found.",
                message.message_thread_id,
            )
            return
        if not is_open_topic(topic):
            await message.reply(msg("operator_closed_reply"))
            return

        sent = await _deliver_to_user(bot, settings, topic, message)
        if sent is None:
            return

        await save_message_link(
            int(topic.user_id),
            sent.message_id,
            settings.support_chat_id,
            message.message_id,
        )
        await _on_operator_message_sent(
            session,
            bot,
            settings.support_chat_id,
            topic,
            message.from_user.id,  # type: ignore[union-attr]
        )


@router.edited_message(F.chat.id)
async def sync_edited_support_message(
    message: Message,
    bot: Bot,
    settings: Settings,
) -> None:
    if message.chat.id != settings.support_chat_id or message.message_thread_id is None:
        return
    if should_ignore_support_message(message, settings):
        return
    raw_text = message.text or message.caption or ""
    if raw_text.startswith(INTERNAL_NOTE_PREFIX):
        return

    linked_id = await get_linked_message_id(settings.support_chat_id, message.message_id)
    if linked_id is None:
        return
    async with open_database_session() as session:
        topic = await get_by_topic_id(session, message.message_thread_id)
    if topic is None or not is_open_topic(topic):
        return
    await _edit_linked_message(bot, int(topic.user_id), linked_id, message)


# --- Helpers ---


def should_ignore_private_message(message: Message) -> bool:
    if message.chat.type != "private":
        return True
    if message.from_user is None or message.from_user.is_bot:
        return True
    return is_service_message(message)


def should_ignore_support_message(message: Message, settings: Settings) -> bool:
    if message.from_user is None or message.from_user.is_bot:
        return True
    if not is_allowed_operator(message.from_user.id, settings):
        logger.info(
            "Support topic message ignored: operator %s is not in ADMIN_IDS.",
            message.from_user.id,
        )
        return True
    return is_service_message(message)


def is_allowed_operator(user_id: int, settings: Settings) -> bool:
    return not settings.admin_ids or user_id in settings.admin_ids


def _validate_operator_command(
    message: Message,
    settings: Settings,
    *,
    require_thread: bool,
) -> bool:
    if message.chat.id != settings.support_chat_id:
        return False
    if require_thread and message.message_thread_id is None:
        return False
    if message.from_user is None or message.from_user.is_bot:
        return False
    return is_allowed_operator(message.from_user.id, settings)


def _can_operate_in_support_chat(
    user_id: int,
    msg: object,
    settings: Settings,
) -> bool:
    if not is_allowed_operator(user_id, settings):
        return False
    if not isinstance(msg, Message):
        return False
    return msg.chat.id == settings.support_chat_id


async def send_welcome_message(message: Message) -> None:
    await message.answer(
        welcome_text(message.from_user.username if message.from_user else None),
        parse_mode="HTML",
    )


def welcome_text(username: Optional[str]) -> str:
    mention = f", @{escape(username)}" if username else "!"
    return msg("welcome_text", mention=mention)


def is_slash_command(message: Message) -> bool:
    text = message.text or message.caption
    return bool(text and text.startswith("/"))


def is_service_message(message: Message) -> bool:
    return bool(
        message.new_chat_members
        or message.left_chat_member
        or message.new_chat_title
        or message.new_chat_photo
        or message.delete_chat_photo
        or message.group_chat_created
        or message.supergroup_chat_created
        or message.channel_chat_created
        or message.message_auto_delete_timer_changed
        or message.forum_topic_created
        or message.forum_topic_edited
        or message.forum_topic_closed
        or message.forum_topic_reopened
        or message.general_forum_topic_hidden
        or message.general_forum_topic_unhidden
        or message.pinned_message
    )


async def get_private_support_topic(
    message: Message,
    bot: Bot,
    settings: Settings,
    *,
    source_payload: Optional[str] = None,
) -> object:
    async with open_database_session() as session:
        return await get_or_create_support_topic(
            session,
            bot,
            message,
            settings.support_chat_id,
            source_payload=source_payload,
        )


async def copy_private_message_to_topic(
    message: Message,
    bot: Bot,
    settings: Settings,
    topic: object,
    *,
    reply_to_message_id: Optional[int] = None,
) -> object:
    return await bot.copy_message(
        chat_id=settings.support_chat_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        message_thread_id=get_topic_id(topic),
        reply_to_message_id=reply_to_message_id,
    )


async def recreate_private_support_topic(
    message: Message,
    bot: Bot,
    settings: Settings,
    topic: object,
) -> object:
    async with open_database_session() as session:
        return await recreate_support_topic(
            session,
            bot,
            message,
            settings.support_chat_id,
            topic,
        )


async def close_topic(
    topic_id: int,
    bot: Bot,
    settings: Settings,
    *,
    closed_by: str = "operator",
    actor_id: Optional[int] = None,
    close_forum: bool = True,
) -> tuple:
    async with open_database_session() as session:
        topic = await get_by_topic_id(session, topic_id)
        if topic is None:
            return False, False, None, None
        was_open = is_open_topic(topic)
        active = await repositories.get_active_ticket_by_user(session, int(topic.user_id))
        ticket_id = int(active.id) if active is not None else None
        topic = await close_support_topic(
            session,
            bot,
            settings.support_chat_id,
            topic_id,
            closed_by=closed_by,
            actor_type="operator",
            actor_id=actor_id,
            close_forum=close_forum,
        )
    return True, was_open, get_user_id(topic) if topic is not None else None, ticket_id


async def send_topic_status_message(
    bot: Bot,
    settings: Settings,
    topic_id: int,
    text: str,
) -> None:
    try:
        await bot.send_message(
            chat_id=settings.support_chat_id,
            message_thread_id=topic_id,
            text=text,
        )
    except TelegramBadRequest as exc:
        logger.warning("Cannot send topic status message to %s: %s", topic_id, exc)


async def send_user_ticket_closed_message(
    bot: Bot,
    user_id: int,
    text: str = USER_TICKET_CLOSED_MESSAGE,
    *,
    ticket_id: Optional[int] = None,
) -> None:
    old_msg_id = await pop_last_reopen_msg(user_id)
    if old_msg_id is not None:
        try:
            await bot.edit_message_reply_markup(
                chat_id=user_id, message_id=old_msg_id, reply_markup=None
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            pass

    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reopen_ticket_keyboard(ticket_id=ticket_id),
        )
        await set_last_reopen_msg(user_id, sent.message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.info("Cannot send ticket closed message to user %s: %s", user_id, exc)


def reopen_ticket_keyboard(*, ticket_id: Optional[int] = None) -> InlineKeyboardMarkup:
    reopen_cb = (
        f"{REOPEN_TICKET_CALLBACK_DATA}:{ticket_id}"
        if ticket_id is not None
        else REOPEN_TICKET_CALLBACK_DATA
    )
    rows = [
        [
            InlineKeyboardButton(
                text=get_messages()["buttons"]["reopen_ticket"],
                callback_data=reopen_cb,
            )
        ]
    ]
    if ticket_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{i}⭐",
                    callback_data=f"{CSAT_CALLBACK_PREFIX}{ticket_id}:{i}",
                )
                for i in range(1, 6)
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _broadcast_ticket_closed(bot: Bot, user_ids: Iterable[int]) -> None:
    for user_id in user_ids:
        try:
            await send_user_ticket_closed_message(bot, user_id)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            await send_user_ticket_closed_message(bot, user_id)
        await asyncio.sleep(0.05)


async def _clear_stale_reopen_keyboard(bot: Bot, user_id: int) -> None:
    msg_id = await pop_last_reopen_msg(user_id)
    if msg_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=user_id, message_id=msg_id, reply_markup=None
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


async def _resolve_reply_id(chat_id: int, message: Message) -> Optional[int]:
    if message.reply_to_message is None:
        return None
    return await get_linked_message_id(chat_id, message.reply_to_message.message_id)


async def _deliver_to_user(
    bot: Bot,
    settings: Settings,
    topic: object,
    message: Message,
    *,
    text_override: Optional[str] = None,
) -> Optional[object]:
    user_id = get_user_id(topic)
    reply_to_id = await _resolve_reply_id(settings.support_chat_id, message)
    try:
        if text_override is not None:
            return await bot.send_message(
                chat_id=user_id,
                text=text_override,
                reply_to_message_id=reply_to_id,
            )
        return await bot.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            reply_to_message_id=reply_to_id,
        )
    except TelegramForbiddenError:
        await message.reply(msg("user_blocked_bot"))
        async with open_database_session() as close_session:
            await close_support_topic(
                close_session,
                bot,
                settings.support_chat_id,
                int(topic.topic_id),
                closed_by="system_blocked",
                actor_type="system",
            )
        return None
    except TelegramBadRequest as exc:
        await message.reply(f"Не удалось доставить сообщение пользователю: {exc}")
        return None


async def mark_user_message_delivered(bot: Bot, message: Message) -> None:
    """Confirm delivery to the client with a reaction on their message."""
    try:
        await bot.set_message_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji=msg("reaction_delivered"))],
        )
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.info(
            "Cannot set delivery reaction on message %s: %s",
            message.message_id,
            exc,
        )


async def _on_user_message_forwarded(
    bot: Bot,
    settings: Settings,
    user_id: int,
    topic: object,
) -> None:
    async with open_database_session() as session:
        ticket = await repositories.get_active_ticket_by_user(session, user_id)
        if ticket is None:
            return
        now = datetime.now(timezone.utc)
        fields: dict = {"last_user_msg_at": now}
        if ticket.status == TICKET_WAITING_USER:
            fields["status"] = TICKET_IN_PROGRESS
            await repositories.record_ticket_event(
                session,
                ticket_id=int(ticket.id),
                actor_type="user",
                actor_id=user_id,
                event_type="status_changed",
                old_value=TICKET_WAITING_USER,
                new_value=TICKET_IN_PROGRESS,
            )
            await sync_topic_status_from_ticket(
                bot, settings.support_chat_id, topic, TICKET_IN_PROGRESS
            )
        await repositories.update_ticket(session, ticket, **fields)


async def _on_operator_message_sent(
    session: object,
    bot: Bot,
    support_chat_id: int,
    topic: object,
    operator_id: int,
) -> None:
    ticket = await repositories.get_active_ticket_by_user(session, int(topic.user_id))
    if ticket is None:
        return
    now = datetime.now(timezone.utc)
    fields: dict = {"last_operator_msg_at": now}
    if ticket.first_response_at is None:
        fields["first_response_at"] = now
    if ticket.assigned_operator_id is None:
        fields["assigned_operator_id"] = operator_id
    if ticket.status == TICKET_NEW:
        fields["status"] = TICKET_IN_PROGRESS
        await repositories.record_ticket_event(
            session,
            ticket_id=int(ticket.id),
            actor_type="operator",
            actor_id=operator_id,
            event_type="status_changed",
            old_value=TICKET_NEW,
            new_value=TICKET_IN_PROGRESS,
        )
        await sync_topic_status_from_ticket(
            bot, support_chat_id, topic, TICKET_IN_PROGRESS
        )
    await repositories.update_ticket(session, ticket, **fields)


async def _assign_ticket(
    session: object,
    bot: Bot,
    support_chat_id: int,
    topic: object,
    ticket: object,
    operator_id: int,
    *,
    actor_id: Optional[int] = None,
) -> None:
    old_op = getattr(ticket, "assigned_operator_id", None)
    old_status = getattr(ticket, "status", TICKET_NEW)
    fields: dict = {"assigned_operator_id": operator_id}
    if old_status == TICKET_NEW:
        fields["status"] = TICKET_IN_PROGRESS
    await repositories.update_ticket(session, ticket, **fields)
    await repositories.record_ticket_event(
        session,
        ticket_id=int(ticket.id),
        actor_type="operator",
        actor_id=actor_id or operator_id,
        event_type="assigned",
        old_value=str(old_op) if old_op else None,
        new_value=str(operator_id),
    )
    if old_status == TICKET_NEW:
        await sync_topic_status_from_ticket(
            bot, support_chat_id, topic, TICKET_IN_PROGRESS
        )


async def _get_topic_and_active_ticket(
    session: object,
    topic_id: int,
) -> tuple:
    topic = await repositories.get_by_topic_id(session, topic_id)
    if topic is None:
        return None, None
    ticket = await repositories.get_active_ticket_by_user(session, int(topic.user_id))
    return topic, ticket


async def _edit_linked_message(
    bot: Bot,
    chat_id: int,
    message_id: int,
    source: Message,
) -> None:
    try:
        if source.text is not None:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=source.text
            )
        elif source.caption is not None:
            await bot.edit_message_caption(
                chat_id=chat_id, message_id=message_id, caption=source.caption
            )
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


async def delete_message_safely(bot: Bot, message: Message) -> None:
    try:
        await bot.delete_message(message.chat.id, message.message_id)
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.info("Cannot delete message %s: %s", message.message_id, exc)


async def remove_inline_keyboard(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as exc:
        logger.info("Cannot remove keyboard from message %s: %s", message.message_id, exc)


def is_message_thread_not_found(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return "message thread not found" in text or "topic_id_invalid" in text


def get_topic_id(topic: object) -> int:
    return int(topic.topic_id)


def get_user_id(user: object) -> int:
    return int(user.user_id)


def is_open_topic(topic: object) -> bool:
    return getattr(topic, "status", None) == "open"
