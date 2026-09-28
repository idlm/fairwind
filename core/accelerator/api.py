"""本机控制面（Clash 兼容子集）+ 静态面板。

形态对齐成熟方案调研结论（`docs/REFERENCE_SOLUTIONS.md`）：内核独立进程、控制面走本机
loopback RESTful + Bearer 令牌、静态前端由宿主在 `/ui` 提供。

安全约束：仅绑定 127.0.0.1；除静态面板外每个请求都要求 `Authorization: Bearer <token>`；
不使用 `*` CORS；请求体有上限；未接入核心时不提供任何"声称已连接"的能力。
"""

import asyncio
import base64
import hmac
import json
import os
import secrets
from pathlib import Path

from aiohttp import web

from accelerator import __version__
from accelerator.errors import SafeError
from accelerator.host import HostService
from accelerator.security import private_directory

TOKEN_NAME = "control.token"
MAX_BODY_BYTES = 64 * 1024
UI_DIRECTORY = Path(__file__).resolve().parent / "ui"
CORE_NOT_INTEGRATED = "NOT_INTEGRATED"
HOST_PREFIX = "/api/host"
PUBLIC_PREFIXES = ("/ui",)
SERVICE_KEY: web.AppKey[HostService] = web.AppKey("service", HostService)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:",
}


def load_or_create_token(data_dir: Path) -> str:
    """读取或创建控制面令牌；文件 0600，内容从不写入日志。"""
    private_directory(data_dir)
    path = data_dir / TOKEN_NAME
    if path.is_symlink():
        raise SafeError("UNSAFE_STORAGE_PATH")
    if path.is_file():
        token = path.read_text(encoding="ascii").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(token + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return token


def _json(payload: object) -> web.Response:
    return web.json_response(payload, dumps=lambda value: json.dumps(value, ensure_ascii=False))


def _error(code: str, status: int) -> web.Response:
    """构造固定错误码响应；注意 Response.set_status 不返回自身，故单独设置。"""
    response = _json({"error": code})
    response.set_status(status)
    return response


def _auth_middleware(token: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.path.startswith(PUBLIC_PREFIXES):
            response = await handler(request)
            response.headers.update(SECURITY_HEADERS)
            return response
        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
            return _error("CONTROL_UNAUTHORIZED", 401)
        try:
            response = await handler(request)
        except SafeError as error:
            return _error(error.code, 400)
        except web.HTTPException:
            raise
        except Exception:
            return _error("INTERNAL_ERROR", 500)
        response.headers["Cache-Control"] = "no-store"
        response.headers.update(SECURITY_HEADERS)
        return response

    return middleware


async def _body(request: web.Request, allowed: tuple[str, ...]) -> dict:
    if not request.can_read_body:
        return {}
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise SafeError("ARGUMENT_INVALID") from None
    if not isinstance(payload, dict) or set(payload) - set(allowed):
        raise SafeError("ARGUMENT_INVALID")
    return payload


def _service(request: web.Request) -> HostService:
    return request.app[SERVICE_KEY]


async def handle_version(request: web.Request) -> web.Response:
    return _json(
        {
            "version": __version__,
            "meta": False,
            "premium": False,
            "core": CORE_NOT_INTEGRATED,
            "note": "CONTROL_API_IS_REFERENCE_HOST",
        }
    )


async def handle_configs(request: web.Request) -> web.Response:
    """只读配置摘要：未接入核心时不虚构监听端口。"""
    return _json(
        {
            "mode": "rule",
            "port": 0,
            "socks-port": 0,
            "mixed-port": 0,
            "allow-lan": False,
            "bind-address": "127.0.0.1",
            "core": CORE_NOT_INTEGRATED,
        }
    )


async def handle_proxies(request: web.Request) -> web.Response:
    """Clash 兼容子集：只暴露真实节点，不虚构 DIRECT/REJECT 等内置代理。"""
    listing = _service(request).list_nodes(request.query.get("country"))
    proxies = {
        row["id"]: {
            "type": row["protocol"],
            "country": row["country"],
            "state": row["state"],
            "history": [],
            "score": row["score"],
            "latency_ms": row["latency_ms"],
        }
        for row in listing["nodes"]
    }
    return _json({"proxies": proxies, "count": listing["count"], "core": CORE_NOT_INTEGRATED})


async def handle_connections(request: web.Request) -> web.Response:
    return _json(
        {"downloadTotal": 0, "uploadTotal": 0, "connections": [], "core": CORE_NOT_INTEGRATED}
    )


async def handle_traffic(request: web.Request) -> web.Response:
    return _json({"up": 0, "down": 0, "core": CORE_NOT_INTEGRATED})


async def handle_host_status(request: web.Request) -> web.Response:
    return _json(_service(request).status())


async def handle_host_capabilities(request: web.Request) -> web.Response:
    return _json(_service(request).capabilities())


async def handle_host_nodes(request: web.Request) -> web.Response:
    return _json(_service(request).list_nodes(request.query.get("country")))


async def handle_host_best(request: web.Request) -> web.Response:
    return _json(_service(request).best_nodes(request.query.get("country")))


async def handle_host_routing(request: web.Request) -> web.Response:
    return _json(_service(request).routing_rules())


async def handle_subscriptions_update(request: web.Request) -> web.Response:
    payload = await _body(request, ("master_url", "force", "interval"))
    service = _service(request)
    summary = await service.update_subscriptions(
        payload.get("master_url"),
        bool(payload.get("force", False)),
        int(payload.get("interval", 21600)),
    )
    return _json(summary)


async def handle_nodes_test(request: web.Request) -> web.Response:
    payload = await _body(request, ("samples", "concurrency"))
    service = _service(request)
    report = await service.test_nodes(
        samples=int(payload.get("samples", 3)),
        concurrency=int(payload.get("concurrency", 8)),
    )
    return _json(report)


async def handle_profiles_apply(request: web.Request) -> web.Response:
    payload = await _body(request, ("envelope", "platform"))
    envelope = payload.get("envelope")
    if not isinstance(envelope, str):
        raise SafeError("ARGUMENT_INVALID")
    try:
        raw = base64.b64decode(envelope, validate=True)
    except (ValueError, TypeError):
        raise SafeError("ARGUMENT_INVALID") from None
    platform = payload.get("platform")
    if platform is not None and not isinstance(platform, str):
        raise SafeError("ARGUMENT_INVALID")
    return _json(_service(request).apply_profiles(raw, platform=platform))


async def handle_connect(request: web.Request) -> web.Response:
    """未接入核心前必须明确失败：控制面不允许让 UI 声称已连接。"""
    return _error("CORE_NOT_INTEGRATED", 400)


async def handle_ui(request: web.Request) -> web.Response:
    return web.HTTPFound("/ui/")


def build_app(service: HostService, token: str) -> web.Application:
    app = web.Application(
        middlewares=[_auth_middleware(token)],
        client_max_size=MAX_BODY_BYTES,
    )
    app[SERVICE_KEY] = service
    app.router.add_get("/version", handle_version)
    app.router.add_get("/configs", handle_configs)
    app.router.add_get("/proxies", handle_proxies)
    app.router.add_get("/connections", handle_connections)
    app.router.add_get("/traffic", handle_traffic)
    app.router.add_get(f"{HOST_PREFIX}/status", handle_host_status)
    app.router.add_get(f"{HOST_PREFIX}/capabilities", handle_host_capabilities)
    app.router.add_get(f"{HOST_PREFIX}/nodes", handle_host_nodes)
    app.router.add_get(f"{HOST_PREFIX}/nodes/best", handle_host_best)
    app.router.add_get(f"{HOST_PREFIX}/routing", handle_host_routing)
    app.router.add_post(f"{HOST_PREFIX}/subscriptions/update", handle_subscriptions_update)
    app.router.add_post(f"{HOST_PREFIX}/nodes/test", handle_nodes_test)
    app.router.add_post(f"{HOST_PREFIX}/profiles", handle_profiles_apply)
    app.router.add_post(f"{HOST_PREFIX}/connect", handle_connect)
    app.router.add_get("/ui", handle_ui)
    app.router.add_static("/ui/", UI_DIRECTORY, show_index=True)
    return app


async def serve(service: HostService, token: str, port: int, on_start=None) -> None:
    """启动控制面并阻塞运行；仅绑定 127.0.0.1，端口 0 时由系统分配。

    `on_start(host, port, token)` 在监听成功后回调，便于调用方打印面板地址与断言绑定地址。
    """
    if not 0 <= port <= 65535:
        raise SafeError("ARGUMENT_INVALID")
    app = build_app(service, token)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        host, bound = "127.0.0.1", port
        if runner.addresses:
            bound = runner.addresses[0][1]
        if on_start is not None:
            on_start(host, bound)
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
