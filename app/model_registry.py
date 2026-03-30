MODEL_MAP: dict[str, str] = {
    "claude-opus4.6": "avocado-froyo-medium",
    "claude-sonnet4.6": "almond-croissant-low",
    "gemini-3.1pro": "galette-medium-thinking",
    "gpt-5.2": "oatmeal-cookie",
    "gpt-5.4": "oval-kumquat-medium",
}

MODEL_ALIASES: dict[str, str] = {
    "claude-opus-4-6": "claude-opus4.6",
    "claude-opus-4.6": "claude-opus4.6",
    "claude-sonnet-4-6": "claude-sonnet4.6",
    "claude-sonnet-4.6": "claude-sonnet4.6",
    "gemini-3.1-pro": "gemini-3.1pro",
    "gemini-3.1pro": "gemini-3.1pro",
    "gpt-5.2": "gpt-5.2",
    "gpt-5.4": "gpt-5.4",
}

SEARCH_MODEL_SUFFIX = "-search"

NOTION_MODEL_REVERSE_MAP: dict[str, str] = {
    value: key for key, value in MODEL_MAP.items()
}

DISPLAY_NAMES: dict[str, str] = {
    "claude-opus4.6": "Claude Opus 4.6",
    "claude-sonnet4.6": "Claude Sonnet 4.6",
    "gemini-3.1pro": "Gemini 3.1 Pro",
    "gpt-5.2": "GPT-5.2",
    "gpt-5.4": "GPT-5.4",
}

MODEL_ICONS: dict[str, str] = {
    "claude-opus4.6": "✳️",
    "claude-sonnet4.6": "✳️",
    "gemini-3.1pro": "✦",
    "gpt-5.2": "⚙",
    "gpt-5.4": "⚙",
}

# 默认使用 Sonnet 4.6（速度和质量的最佳平衡）
DEFAULT_MODEL = "claude-sonnet4.6"


def get_notion_model(model_name: str) -> str:
    standard_name = get_standard_model(model_name)
    return MODEL_MAP.get(standard_name, MODEL_MAP[DEFAULT_MODEL])


def is_gemini_model(model_name: str) -> bool:
    standard_name = get_standard_model(model_name)
    if standard_name.startswith("gemini-"):
        return True
    notion_model = get_notion_model(standard_name)
    return notion_model.startswith("vertex-") or notion_model.startswith("galette-")


def get_thread_type(model_name: str) -> str:
    if is_gemini_model(model_name):
        return "markdown-chat"
    return "workflow"


def get_standard_model(model_name: str) -> str:
    raw_name = str(model_name or "").strip()
    search_suffix = raw_name.endswith(SEARCH_MODEL_SUFFIX)
    if search_suffix:
        raw_name = raw_name[: -len(SEARCH_MODEL_SUFFIX)]
    normalized_name = raw_name.lower()
    if normalized_name in MODEL_MAP:
        return normalized_name
    if normalized_name in MODEL_ALIASES:
        return MODEL_ALIASES[normalized_name]
    return NOTION_MODEL_REVERSE_MAP.get(normalized_name, DEFAULT_MODEL)


def is_search_model(model_name: str) -> bool:
    return str(model_name or "").endswith(SEARCH_MODEL_SUFFIX)


def list_available_models() -> list[str]:
    models = list(MODEL_MAP.keys())
    search_models = [f"{model}{SEARCH_MODEL_SUFFIX}" for model in models]
    return models + search_models


def is_supported_model(model_name: str) -> bool:
    return get_standard_model(model_name) in MODEL_MAP


def get_display_name(model_name: str) -> str:
    standard_name = get_standard_model(model_name)
    base_name = DISPLAY_NAMES.get(standard_name, standard_name)
    if is_search_model(model_name):
        return f"{base_name} Search"
    return base_name


def get_model_icon(model_name: str) -> str:
    standard_name = get_standard_model(model_name)
    return MODEL_ICONS.get(standard_name, "")
