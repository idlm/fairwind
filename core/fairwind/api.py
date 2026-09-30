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
import time
from pathlib import Path

from aiohttp import web

from fairwind import __version__
from fairwind.errors import SafeError
from fairwind.host import SUBSCRIPTION_ACTIONS, HostService
from fairwind.metrics import UNMATCHED_ROUTE, Metrics
from fairwind.security import private_directory

TOKEN_NAME = "control.token"
MAX_BODY_BYTES = 64 * 1024
UI_DIRECTORY = Path(__file__).resolve().parent / "ui"
CORE_NOT_INTEGRATED = "NOT_INTEGRATED"
HOST_PREFIX = "/api/host"
PUBLIC_PREFIXES = ("/ui",)
SERVICE_KEY: web.AppKey[HostService] = web.AppKey("service", HostService)
METRICS_KEY: web.AppKey[Metrics] = web.AppKey("metrics", Metrics)
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


def _route_template(request: web.Request) -> str:
    """只记录路由模板（如 `/api/host/nodes/{id}`），绝不记录其中的用户输入。"""
    try:
        return request.match_info.route.resource.canonical
    except (AttributeError, RuntimeError):
        return UNMATCHED_ROUTE


def _record(
    request: web.Request, status: int, started: float, error_code: str | None = None
) -> None:
    metrics = request.app.get(METRICS_KEY)
    if metrics is None:
        return
    metrics.record(
        _route_template(request), status, (time.perf_counter() - started) * 1000, error_code
    )


def _auth_middleware(token: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        started = time.perf_counter()
        if request.path.startswith(PUBLIC_PREFIXES):
            response = await handler(request)
            response.headers.update(SECURITY_HEADERS)
            _record(request, response.status, started)
            return response
        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
            response = _error("CONTROL_UNAUTHORIZED", 401)
            _record(request, response.status, started, "CONTROL_UNAUTHORIZED")
            return response
        try:
            response = await handler(request)
        except SafeError as error:
            response = _error(error.code, 400)
            _record(request, response.status, started, error.code)
            return response
        except web.HTTPException as error:
            _record(request, error.status, started)
            raise
        except Exception:
            response = _error("INTERNAL_ERROR", 500)
            _record(request, response.status, started, "INTERNAL_ERROR")
            return response
        response.headers["Cache-Control"] = "no-store"
        response.headers.update(SECURITY_HEADERS)
        _record(request, response.status, started)
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
            "core": _service(request).core_state(),
            "note": "CONTROL_API_IS_REFERENCE_HOST",
        }
    )


async def handle_configs(request: web.Request) -> web.Response:
    """只读配置摘要：未连接时不虚构监听端口（连上了才报真实回环端口）。"""
    service = _service(request)
    runtime = service.adapter.runtime
    connected = service.controller.state.value == "CONNECTED" and runtime.socks_port is not None
    return _json(
        {
            "mode": "rule",
            "port": runtime.api_port if connected else 0,
            "socks-port": runtime.socks_port if connected else 0,
            "mixed-port": 0,
            "allow-lan": False,
            "bind-address": "127.0.0.1",
            "mode_source": "CORE_LOOPBACK_INBOUND" if connected else "REFERENCE_DEFAULT",
            "core": service.core_state(),
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
    return _json(
        {
            "proxies": proxies,
            "count": listing["count"],
            "core": _service(request).core_state(),
        }
    )


async def handle_connections(request: web.Request) -> web.Response:
    service = _service(request)
    history = service.connection_history(int(request.query.get("limit", "10")))
    traffic = await service.traffic()
    return _json(
        {
            "downloadTotal": traffic["downlink"],
            "uploadTotal": traffic["uplink"],
            "connections": history["history"],
            "core": service.core_state(),
            "measured": traffic["measured"],
            "note": traffic["note"],
        }
    )


async def handle_traffic(request: web.Request) -> web.Response:
    """真实流量字节：来自核心统计 API；未连接时 `null`，不是 0。"""
    service = _service(request)
    traffic = await service.traffic()
    return _json(
        {
            "up": traffic["uplink"],
            "down": traffic["downlink"],
            "measured": traffic["measured"],
            "traffic_measured": traffic["measured"],
            "core": service.core_state(),
            "note": traffic["note"],
        }
    )


async def handle_host_status(request: web.Request) -> web.Response:
    return _json(_service(request).status())


async def handle_host_capabilities(request: web.Request) -> web.Response:
    return _json(_service(request).capabilities())


async def handle_host_nodes(request: web.Request) -> web.Response:
    return _json(_service(request).list_nodes(request.query.get("country")))


async def handle_host_best(request: web.Request) -> web.Response:
    return _json(_service(request).best_nodes(request.query.get("country")))


async def handle_host_node_detail(request: web.Request) -> web.Response:
    """节点详情 + 分数解释 + 资格解释（只读，字段不含凭据）。"""
    return _json(_service(request).node_detail(request.match_info["id"]))


async def handle_host_route_explain(request: web.Request) -> web.Response:
    """路由解释；缺 host 或非法 port 直接拒绝，不猜测。"""
    host = request.query.get("host")
    if not host:
        raise SafeError("ARGUMENT_INVALID")
    port = request.query.get("port")
    if port is not None and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise SafeError("ARGUMENT_INVALID")
    return _json(
        _service(request).explain_route(
            host,
            int(port) if port is not None else None,
            request.query.get("protocol"),
            request.query.get("process"),
        )
    )


async def handle_host_routing(request: web.Request) -> web.Response:
    return _json(_service(request).routing_rules())


async def handle_host_subscriptions(request: web.Request) -> web.Response:
    return _json(_service(request).subscriptions())


async def handle_host_summary(request: web.Request) -> web.Response:
    return _json(_service(request).node_summary())


async def handle_host_dns(request: web.Request) -> web.Response:
    return _json(_service(request).dns_policy())


async def handle_host_profiles(request: web.Request) -> web.Response:
    return _json(_service(request).profile_versions())


async def handle_host_history(request: web.Request) -> web.Response:
    limit = request.query.get("limit", "10")
    if not limit.isdigit() or not 1 <= int(limit) <= 100:
        raise SafeError("ARGUMENT_INVALID")
    return _json(_service(request).connection_history(int(limit)))


async def handle_host_diagnostic(request: web.Request) -> web.Response:
    """离线自检：只读本机状态，不联网、不修改任何东西。"""
    return _json(_service(request).diagnostic())


async def handle_host_metrics(request: web.Request) -> web.Response:
    """进程内指标：只统计本进程真实发生过的请求；流量未测量，不做任何推算。"""
    metrics = request.app.get(METRICS_KEY)
    payload = metrics.snapshot() if metrics is not None else {}
    service = _service(request)
    traffic = await service.traffic()
    payload["traffic"] = {**payload.get("traffic", {}), **traffic}
    return _json({**payload, "core": service.core_state()})


async def handle_subscriptions_update(request: web.Request) -> web.Response:
    payload = await _body(request, ("master_url", "force", "interval"))
    service = _service(request)
    summary = await service.update_subscriptions(
        payload.get("master_url"),
        bool(payload.get("force", False)),
        int(payload.get("interval", 21600)),
    )
    return _json(summary)


async def handle_subscriptions_add(request: web.Request) -> web.Response:
    """手动加入订阅源：URL 加密落库，响应里不回显。"""
    payload = await _body(request, ("url",))
    url = payload.get("url")
    if not isinstance(url, str) or not url:
        raise SafeError("ARGUMENT_INVALID")
    return _json(_service(request).add_subscription(url))


async def handle_subscription_action(request: web.Request) -> web.Response:
    """暂停 / 恢复 / 移除单个订阅源；句柄是 URL 摘要前缀，不是 URL 本身。"""
    payload = await _body(request, ("action",))
    action = payload.get("action")
    if action not in SUBSCRIPTION_ACTIONS:
        raise SafeError("ARGUMENT_INVALID")
    service = _service(request)
    handle = request.match_info["handle"]
    if action == "remove":
        return _json(service.remove_subscription(handle))
    return _json(service.set_subscription_state(handle, action == "pause"))


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
    """连接一条线路：成功返回真实状态与节点；核心未接入时固定 `CORE_NOT_INTEGRATED`。"""
    payload = await _body(request, ())
    node_id = payload.get("node_id") or payload.get("nodeId")
    country = payload.get("country")
    for value, code in ((node_id, "NODE_ID_INVALID"), (country, "ARGUMENT_INVALID")):
        if value is not None and not isinstance(value, str):
            raise SafeError(code)
    return _json(await _service(request).connect(node_id, country))


async def handle_disconnect(request: web.Request) -> web.Response:
    """断开：停止核心进程并删除临时配置（凭据不留在磁盘上）。"""
    return _json(await _service(request).disconnect())


async def handle_ui(request: web.Request) -> web.Response:
    raise web.HTTPFound("/ui/")


async def handle_panel(request: web.Request) -> web.Response:
    """面板是单文件、零构建、零外部资源；不用静态目录处理器，避免目录穿越与目录列表。"""
    panel = UI_DIRECTORY / "index.html"
    if not panel.is_file():
        return _error("PANEL_MISSING", 500)
    response = web.FileResponse(panel)
    response.headers.update(SECURITY_HEADERS)
    return response


def build_app(service: HostService, token: str, metrics: Metrics | None = None) -> web.Application:
    app = web.Application(
        middlewares=[_auth_middleware(token)],
        client_max_size=MAX_BODY_BYTES,
    )
    app[SERVICE_KEY] = service
    app[METRICS_KEY] = metrics or Metrics()
    app.router.add_get("/version", handle_version)
    app.router.add_get("/configs", handle_configs)
    app.router.add_get("/proxies", handle_proxies)
    app.router.add_get("/connections", handle_connections)
    app.router.add_get("/traffic", handle_traffic)
    app.router.add_get(f"{HOST_PREFIX}/status", handle_host_status)
    app.router.add_get(f"{HOST_PREFIX}/capabilities", handle_host_capabilities)
    app.router.add_get(f"{HOST_PREFIX}/nodes", handle_host_nodes)
    app.router.add_get(f"{HOST_PREFIX}/nodes/best", handle_host_best)
    app.router.add_get(f"{HOST_PREFIX}/nodes/{{id}}", handle_host_node_detail)
    app.router.add_get(f"{HOST_PREFIX}/routing", handle_host_routing)
    app.router.add_get(f"{HOST_PREFIX}/route", handle_host_route_explain)
    app.router.add_get(f"{HOST_PREFIX}/subscriptions", handle_host_subscriptions)
    app.router.add_get(f"{HOST_PREFIX}/summary", handle_host_summary)
    app.router.add_get(f"{HOST_PREFIX}/dns", handle_host_dns)
    app.router.add_get(f"{HOST_PREFIX}/profiles", handle_host_profiles)
    app.router.add_get(f"{HOST_PREFIX}/history", handle_host_history)
    app.router.add_get(f"{HOST_PREFIX}/diagnostic", handle_host_diagnostic)
    app.router.add_get(f"{HOST_PREFIX}/metrics", handle_host_metrics)
    app.router.add_post(f"{HOST_PREFIX}/connect", handle_connect)
    app.router.add_post(f"{HOST_PREFIX}/disconnect", handle_disconnect)
    app.router.add_post(f"{HOST_PREFIX}/subscriptions/update", handle_subscriptions_update)
    app.router.add_post(f"{HOST_PREFIX}/subscriptions", handle_subscriptions_add)
    app.router.add_post(f"{HOST_PREFIX}/subscriptions/{{handle}}", handle_subscription_action)
    app.router.add_post(f"{HOST_PREFIX}/nodes/test", handle_nodes_test)
    app.router.add_post(f"{HOST_PREFIX}/profiles", handle_profiles_apply)
    app.router.add_post(f"{HOST_PREFIX}/connect", handle_connect)
    app.router.add_get("/ui", handle_ui)
    app.router.add_get("/ui/", handle_panel)
    app.router.add_get("/ui/index.html", handle_panel)
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
