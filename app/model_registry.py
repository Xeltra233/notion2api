MODEL_MAP: dict[str, str] = {
    # 友好名称 → Notion 内部 ID
    "claude-opus": "avocado-froyo-medium",
    "claude-sonnet": "almond-croissant-low",
    "gemini-pro": "galette-medium-thinking",
    "gpt-5": "oatmeal-cookie",
    # Notion 内部 ID 直传（自映射）
    "avocado-froyo-medium": "avocado-froyo-medium",
    "almond-croissant-low": "almond-croissant-low",
    "galette-medium-thinking": "galette-medium-thinking",
    "oatmeal-cookie": "oatmeal-cookie",
}

DISPLAY_NAMES: dict[str, str] = {
    "claude-opus": "Opus 4.6",
    "claude-sonnet": "Sonnet 4.6",
    "gemini-pro": "Gemini 3.1 Pro",
    "gpt-5": "GPT-5.2",
    "avocado-froyo-medium": "Opus 4.6",
    "almond-croissant-low": "Sonnet 4.6",
    "galette-medium-thinking": "Gemini 3.1 Pro",
    "oatmeal-cookie": "GPT-5.2",
}

MODEL_ICONS: dict[str, str] = {
    "claude-opus": "✳️",
    "claude-sonnet": "✳️",
    "gemini-pro": "✦",
    "gpt-5": "⚙",
    "avocado-froyo-medium": "✳️",
    "almond-croissant-low": "✳️",
    "galette-medium-thinking": "✦",
    "oatmeal-cookie": "⚙",
}

DEFAULT_NOTION_MODEL = "avocado-froyo-medium"


def get_notion_model(model_name: str) -> str:
    return MODEL_MAP.get(model_name, DEFAULT_NOTION_MODEL)


def list_available_models() -> list[str]:
    return list(MODEL_MAP.keys())


def get_display_name(model_name: str) -> str:
    return DISPLAY_NAMES.get(model_name, model_name)


def get_model_icon(model_name: str) -> str:
    return MODEL_ICONS.get(model_name, "")
