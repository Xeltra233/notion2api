# 友好名称 → Notion 内部 ID
FRIENDLY_MODEL_MAP: dict[str, str] = {
    "claude-opus4.6": "avocado-froyo-medium",
    "claude-sonnet4.6": "almond-croissant-low",
    "gemini-3.1pro": "galette-medium-thinking",
    "gpt-5.2": "oatmeal-cookie",
}

# Notion 内部 ID 直传（支持通过内部 ID 访问）
INTERNAL_MODEL_MAP: dict[str, str] = {
    "avocado-froyo-medium": "avocado-froyo-medium",
    "almond-croissant-low": "almond-croissant-low",
    "galette-medium-thinking": "galette-medium-thinking",
    "oatmeal-cookie": "oatmeal-cookie",
}

# 合并后的映射表，用于内部查找
MODEL_MAP: dict[str, str] = {**FRIENDLY_MODEL_MAP, **INTERNAL_MODEL_MAP}

DISPLAY_NAMES: dict[str, str] = {
    "claude-opus4.6": "claude-opus4.6",
    "claude-sonnet4.6": "claude-sonnet4.6",
    "gemini-3.1pro": "gemini-3.1pro",
    "gpt-5.2": "gpt-5.2",
    "avocado-froyo-medium": "claude-opus4.6",
    "almond-croissant-low": "claude-sonnet4.6",
    "galette-medium-thinking": "gemini-3.1pro",
    "oatmeal-cookie": "gpt-5.2",
}

MODEL_ICONS: dict[str, str] = {
    "claude-opus4.6": "✳️",
    "claude-sonnet4.6": "✳️",
    "gemini-3.1pro": "✦",
    "gpt-5.2": "⚙",
    "avocado-froyo-medium": "✳️",
    "almond-croissant-low": "✳️",
    "galette-medium-thinking": "✦",
    "oatmeal-cookie": "⚙",
}

DEFAULT_NOTION_MODEL = "avocado-froyo-medium"


def get_notion_model(model_name: str) -> str:
    return MODEL_MAP.get(model_name, DEFAULT_NOTION_MODEL)


def list_available_models() -> list[str]:
    # 仅返回友好名称，用于 /v1/models 列表展示，避免冗余
    return list(FRIENDLY_MODEL_MAP.keys())


def is_supported_model(model_name: str) -> bool:
    # 检查是否是支持的模型（包括友好名称和内部 ID）
    return model_name in MODEL_MAP


def get_display_name(model_name: str) -> str:
    return DISPLAY_NAMES.get(model_name, model_name)


def get_model_icon(model_name: str) -> str:
    return MODEL_ICONS.get(model_name, "")
