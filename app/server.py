import os
import re
import time
import threading
from urllib.parse import unquote
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from contextlib import asynccontextmanager
from app.config import (
    get_account_probe_interval_seconds,
    get_accounts,
    get_admin_auth,
    get_admin_session_ttl_seconds,
    get_allowed_origins,
    get_api_key,
    get_chat_session_ttl_seconds,
    get_config_store,
    is_lite_mode,
    is_standard_mode,
)
from app.account_pool import AccountPool
from app.conversation import ConversationManager
from app.usage import UsageStore
from app.api.admin import router as admin_router
from app.api.chat import gemini_router, router as chat_router
from app.api.models import router as models_router
from app.api.register import router as register_router
from app.logger import logger
from app.limiter import limiter


def _background_account_probe(app: FastAPI, stop_event: threading.Event) -> None:
    while not stop_event.wait(get_account_probe_interval_seconds()):
        try:
            pool = getattr(app.state, "account_pool", None)
            if pool is None:
                continue
            pool.keepalive_accounts(background_mode=True)
            pool.sync_workspaces(background_mode=True)
            try:
                from app.api.register import maybe_start_auto_register

                maybe_start_auto_register(app.state.admin_request_context)
            except Exception:
                logger.warning(
                    "Background auto register scheduling failed",
                    exc_info=True,
                    extra={
                        "request_info": {"event": "background_auto_register_failed"}
                    },
                )
        except Exception:
            logger.warning(
                "Background account probe failed",
                exc_info=True,
                extra={"request_info": {"event": "background_account_probe_failed"}},
            )


def _warmup_account_pool(app: FastAPI) -> None:
    try:
        pool = getattr(app.state, "account_pool", None)
        if pool is None:
            return
        pool.expand_workspaces(background_mode=True)
        pool.probe_accounts(background_mode=True)
    except ValueError as exc:
        logger.warning(
            "Startup account warmup skipped stale client state",
            extra={
                "request_info": {
                    "event": "startup_account_warmup_skipped",
                    "detail": str(exc)[:300],
                }
            },
        )
    except Exception:
        logger.warning(
            "Startup account warmup failed",
            exc_info=True,
            extra={"request_info": {"event": "startup_account_warmup_failed"}},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化状态
    accounts = get_accounts()
    app.state.config_store = get_config_store()
    app.state.admin_auth = get_admin_auth()
    app.state.admin_sessions = {}
    app.state.chat_sessions = {}
    app.state.admin_session_ttl_seconds = get_admin_session_ttl_seconds()
    app.state.chat_session_ttl_seconds = get_chat_session_ttl_seconds()
    app.state.admin_request_context = type("RequestContext", (), {"app": app})()
    app.state.account_pool = AccountPool(accounts)
    app.state.usage_store = UsageStore()
    app.state.account_probe_stop = threading.Event()
    app.state.account_probe_thread = threading.Thread(
        target=_background_account_probe,
        args=(app, app.state.account_probe_stop),
        daemon=True,
        name="account-probe-loop",
    )
    app.state.account_probe_thread.start()
    app.state.account_warmup_thread = threading.Thread(
        target=_warmup_account_pool,
        args=(app,),
        daemon=True,
        name="account-warmup",
    )
    app.state.account_warmup_thread.start()

    # 确定运行模式
    if is_lite_mode():
        mode = "lite"
        logger.info(
            "Service starting up in LITE mode",
            extra={
                "request_info": {
                    "event": "startup",
                    "accounts": len(accounts),
                    "mode": "lite",
                }
            },
        )
    elif is_standard_mode():
        mode = "standard"
        logger.info(
            "Service starting up in STANDARD mode",
            extra={
                "request_info": {
                    "event": "startup",
                    "accounts": len(accounts),
                    "mode": "standard",
                }
            },
        )
    else:
        mode = "heavy"
        app.state.conversation_manager = ConversationManager()
        logger.info(
            "Service starting up in HEAVY mode",
            extra={
                "request_info": {
                    "event": "startup",
                    "accounts": len(accounts),
                    "mode": "heavy",
                }
            },
        )

    app.state.start_time = time.time()
    yield
    # 关闭时清理
    app.state.account_probe_stop.set()
    warmup_thread = getattr(app.state, "account_warmup_thread", None)
    if warmup_thread and warmup_thread.is_alive():
        warmup_thread.join(timeout=1.5)
    probe_thread = getattr(app.state, "account_probe_thread", None)
    if probe_thread and probe_thread.is_alive():
        probe_thread.join(timeout=1.5)
    logger.info("Service shutting down", extra={"request_info": {"event": "shutdown"}})


app = FastAPI(
    title="Notion Opus API",
    description="A FastAPI wrapper providing OpenAI-, Anthropic-, and Gemini-compatible interfaces for the Notion backend.",
    version="1.0.0",
    lifespan=lifespan,
)

# 允许跨域（配合本地前端）
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注入 Limiter
app.state.limiter = limiter


# 自定义 429 速率限制响应
def custom_rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429, content={"error": "Too many requests, please try again later"}
    )


app.add_exception_handler(RateLimitExceeded, custom_rate_limit_exceeded_handler)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled application exception",
        exc_info=True,
        extra={
            "request_info": {
                "event": "unhandled_exception",
                "method": request.method,
                "path": request.url.path,
            }
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "Internal server error",
                "type": "server_error",
            }
        },
    )


# 结构化日志中间件
@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    start_time = time.time()

    # 跳过高频且不重要的日志打印，避免刷屏
    skip_logging = request.url.path in ["/health", "/favicon.ico"]

    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception as e:
        status_code = 500
        logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
        raise
    finally:
        process_time = time.time() - start_time
        client_ip = request.client.host if request.client else "unknown"

        if not skip_logging:
            log_level = logger.error if status_code >= 400 else logger.info
            log_level(
                "Request processed",
                extra={
                    "request_info": {
                        "method": request.method,
                        "path": request.url.path,
                        "ip": client_ip,
                        "status_code": status_code,
                        "duration_ms": round(process_time * 1000, 2),
                    }
                },
            )

    return response


def _is_safe_public_media_path(path: str) -> bool:
    normalized = unquote(str(path or "")).replace("\\", "/")
    if not normalized.startswith("/v1/media/"):
        return False
    media_id = normalized[len("/v1/media/") :]
    if not media_id or "/" in media_id:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", media_id))


# 简易 API Key 鉴权中间件
@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    # 如果环境配置中未设置 API_KEY，则全局不验证
    api_key = get_api_key()
    if api_key:
        public_api_paths = {
            "/v1/admin/oauth/callback",
        }
        request_path = request.url.path
        # 跳过 OPTIONS 请求和非受保护的静态路由（如果以后有的话）
        if (
            request_path.startswith("/v1") or request_path.startswith("/v1beta")
        ) and request.method != "OPTIONS":
            if request_path in public_api_paths:
                return await call_next(request)
            if request_path.startswith("/v1/media/"):
                if _is_safe_public_media_path(request_path):
                    return await call_next(request)
                return JSONResponse(status_code=404, content={"detail": "Media not found."})
            auth_header = request.headers.get("Authorization", "")
            x_api_key = str(request.headers.get("x-api-key", "") or "")
            bearer_token = ""
            if auth_header.startswith("Bearer "):
                bearer_token = auth_header.split(" ", 1)[1]
            if bearer_token != api_key and x_api_key != api_key:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "message": "Error: API KEY doesn't match.",
                            "type": "invalid_request_error",
                            "code": "invalid_api_key",
                        }
                    },
                )
    return await call_next(request)


# 挂载路由，前缀统一为 /v1
app.include_router(chat_router, prefix="/v1")
app.include_router(gemini_router, prefix="/v1beta")
app.include_router(models_router, prefix="/v1")
app.include_router(register_router, prefix="/v1")
app.include_router(admin_router, prefix="/v1")


# 挂载健康检查
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon", status_code=204)


@app.get("/health", tags=["system"])
def health_check(request: Request):
    uptime = time.time() - request.app.state.start_time
    pool = request.app.state.account_pool
    status = pool.get_status_summary()
    return {
        "status": "ok",
        "accounts": status["active"],
        "accounts_total": status["total"],
        "accounts_cooling": status["cooling"],
        "accounts_invalid": status.get("invalid", 0),
        "uptime": int(uptime),
    }


# 挂载静态前端到根目录
frontend_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"
)
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
