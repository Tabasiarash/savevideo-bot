"""End-to-end routing tests through the real aiogram Dispatcher.

These use the production router wiring (:func:`bot.main.build_dispatcher`) and a
stubbed Bot API session, so a command must both survive router precedence *and*
reach its handler. Regression guard for the bug where ``links.router``'s
catch-all ``F.text`` handler swallowed every command and replied
"Unknown command".

The routers are module-level singletons that aiogram only allows attaching to a
single parent, so the dispatcher is built once for the whole module.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import Response
from aiogram.types import Chat, Message, Update, User
from bot.i18n import get_text
from bot.main import build_dispatcher
from tests.conftest import ADMIN_ID, USER_ID

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


class RecordingSession(BaseSession):
    """A ``BaseSession`` that records method calls and returns valid results."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Any] = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        result = self._result_for(bot, method)
        response_type = Response[method.__returning__]
        response = response_type.model_validate({"ok": True, "result": result}, context={"bot": bot})
        return response.result

    async def stream_content(
        self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True
    ):
        yield b""

    async def close(self) -> None:
        return None

    def _result_for(self, bot, method):
        name = type(method).__name__
        if name == "GetMe":
            return {"id": bot.id, "is_bot": True, "first_name": "Test", "username": "test_bot"}
        if name == "SendMediaGroup":
            return [self._message(getattr(method, "chat_id", 1))]
        if name.startswith(("Send", "Edit", "Forward", "Copy")):
            return self._message(getattr(method, "chat_id", 1))
        return True

    @staticmethod
    def _message(chat_id: int | None) -> dict[str, Any]:
        return {
            "message_id": 1,
            "date": int(time.time()),
            "chat": {"id": chat_id or 1, "type": "private"},
        }

    def replies(self) -> list[tuple[str, str | None, Any]]:
        out = []
        for method in self.calls:
            name = type(method).__name__
            if name in ("SendMessage", "EditMessageText"):
                out.append((name, getattr(method, "text", None), getattr(method, "reply_markup", None)))
        return out


@pytest.fixture(scope="module")
def dispatcher():
    return build_dispatcher()


@pytest.fixture
def aiogram_bot():
    return Bot(
        token=TOKEN,
        session=RecordingSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def _message(text: str, user_id: int) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(
            id=user_id,
            is_bot=False,
            first_name="Tester",
            username="tester",
            language_code="en",
        ),
        text=text,
    )


async def feed(dispatcher, bot: Bot, text: str, user_id: int = USER_ID) -> None:
    await dispatcher.feed_update(bot, Update(update_id=1, message=_message(text, user_id)))


def replies(bot: Bot):
    return bot.session.replies()  # type: ignore[attr-defined]


def texts(bot: Bot) -> list[str]:
    return [body for _, body, _ in replies(bot) if body]


async def test_start_is_not_unknown_command(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/start")
    sent = texts(aiogram_bot)
    assert sent, "handler produced no reply"
    assert "Unknown command" not in " ".join(sent)
    assert get_text("en", "start", name="Tester") in sent


async def test_help_command(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/help")
    assert (
        get_text("en", "help", botname="test_bot", playlist=services.settings.playlist_max_items)
        in texts(aiogram_bot)
    )


async def test_help_command_with_mention(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/help@test_bot")
    assert (
        get_text("en", "help", botname="test_bot", playlist=services.settings.playlist_max_items)
        in texts(aiogram_bot)
    )


async def test_language_command_shows_keyboard(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/language")
    got = replies(aiogram_bot)
    assert got
    assert got[-1][2] is not None, "language command should send a keyboard"
    assert get_text("en", "choose_language") in [body for _, body, _ in got if body]


async def test_unknown_command_falls_back(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/definitely_not_a_command")
    assert get_text("en", "unknown_command") in texts(aiogram_bot)


async def test_plain_text_without_url_falls_back(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "hello there")
    assert get_text("en", "unknown_command") in texts(aiogram_bot)


async def test_url_text_reaches_format_picker(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "https://youtu.be/dQw4w9WgXcQ")
    got = replies(aiogram_bot)
    assert get_text("en", "unknown_command") not in [body for _, body, _ in got if body]
    assert any(name == "EditMessageText" for name, _, _ in got), "picker edit not sent"


async def test_spotify_link_intercepted_even_when_disabled(services, dispatcher, aiogram_bot):
    # With the feature off the Spotify router still answers (DRM message); the
    # link must never fall through to the generic link flow.
    await feed(dispatcher, aiogram_bot, "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC")
    sent = texts(aiogram_bot)
    assert get_text("en", "spotify_disabled") in sent
    assert get_text("en", "url_fetching") not in sent


async def test_admin_command_as_non_admin_denied(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/stats", user_id=USER_ID)
    assert get_text("en", "not_allowed") in texts(aiogram_bot)


async def test_admin_command_as_admin_responds(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/stats", user_id=ADMIN_ID)
    sent = texts(aiogram_bot)
    assert get_text("en", "not_allowed") not in sent
    assert sent, "admin got no stats reply"


async def test_users_command_as_admin_not_unknown(services, dispatcher, aiogram_bot):
    # Regression: cmd_users once lacked its Command("users") decorator, so the
    # command silently fell through to links.router's catch-all.
    await feed(dispatcher, aiogram_bot, "/users", user_id=ADMIN_ID)
    sent = texts(aiogram_bot)
    assert sent, "admin got no /users reply"
    assert get_text("en", "unknown_command") not in sent
    assert get_text("en", "not_allowed") not in sent
    assert any("Users" in body for body in sent)


async def test_users_command_with_limit(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/users 5", user_id=ADMIN_ID)
    sent = texts(aiogram_bot)
    assert sent
    assert get_text("en", "unknown_command") not in sent


async def test_users_command_as_non_admin_denied(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/users", user_id=USER_ID)
    assert get_text("en", "not_allowed") in texts(aiogram_bot)


async def test_cleanup_command_as_admin_responds(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/cleanup", user_id=ADMIN_ID)
    sent = texts(aiogram_bot)
    assert sent, "admin got no /cleanup reply"
    assert get_text("en", "unknown_command") not in sent
    assert get_text("en", "not_allowed") not in sent
    assert any("🧹" in body for body in sent)


async def test_cleanup_command_as_non_admin_denied(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/cleanup", user_id=USER_ID)
    assert get_text("en", "not_allowed") in texts(aiogram_bot)


async def test_health_command_as_admin_responds(services, dispatcher, aiogram_bot):
    await feed(dispatcher, aiogram_bot, "/health", user_id=ADMIN_ID)
    sent = texts(aiogram_bot)
    assert sent, "admin got no /health reply"
    assert get_text("en", "unknown_command") not in sent
    assert get_text("en", "not_allowed") not in sent
