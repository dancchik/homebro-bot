from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from bot import db


class LangMiddleware(BaseMiddleware):
    """Loads the user's saved language and passes it to handlers as `lang`."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        data["lang"] = await db.get_lang(user.id) if user else None
        return await handler(event, data)
