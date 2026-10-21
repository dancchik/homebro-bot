from bot.locales import en, ro, ru

LANGS = {
    "ru": "🇷🇺 Русский",
    "ro": "🇷🇴 Română",
    "en": "🇬🇧 English",
}
DEFAULT_LANG = "ru"

CHOOSE_LANG = "🌐 Выберите язык / Alegeți limba / Choose your language"

TEXTS = {"ru": ru.TEXTS, "ro": ro.TEXTS, "en": en.TEXTS}


def t(lang: str | None, key: str) -> str:
    """Return text in the given language, falling back to the default one."""
    return TEXTS.get(lang or DEFAULT_LANG, TEXTS[DEFAULT_LANG])[key]


def all_variants(key: str) -> set[str]:
    """All translations of a key — used to match reply-button presses in any language."""
    return {texts[key] for texts in TEXTS.values()}


MENU_KEYS = (
    "btn_search", "btn_favorites", "btn_subscriptions", "btn_add", "btn_my", "btn_lang",
    "btn_moderation",
)
# Main-menu button texts in all languages: they must never be taken as typed input
MENU_TEXTS = set().union(*(all_variants(key) for key in MENU_KEYS))
