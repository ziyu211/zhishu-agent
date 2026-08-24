"""智枢智能体 —— 单进程入口（融合 Agent 引擎 + REST/SSE API + 静态前端）。

去掉原架构中的 Node BFF，由 FastAPI 统一托管：
  * /api/v1/*  —— 业务 API（对话/模型/知识库/鉴权/管理）
  * /health    —— 健康检查
  * /*         —— 编译后的 Vue 前端（SPA fallback，同源访问）
"""
from __future__ import annotations

import argparse
import json
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .core.config import ZhishuConfig
from .context import init_ctx, get_ctx
from .core.media import media_mime, content_disposition, resolve_media_fallback
from . import api as api_pkg

# 单一版本来源：登录页通过 /health 拉取此版本展示
APP_VERSION = "1.0.37"


@asynccontextmanager
async def _media_sweep_loop(media, retention_days: int):
    """后台周期清理过期媒体产物（仅当 retention_days>0 时由 lifespan 启动）。

    每小时检查一次；单轮为同步删除，文件量在常规部署下很小，阻塞可控。
    任何单轮异常仅打印告警，不影响服务主流程。
    """
    import asyncio as _asyncio
    while True:
        await _asyncio.sleep(3600)
        try:
            n = media.sweep_expired(retention_days)
            if n:
                print(f"[智枢] 媒体留存清理：已删除 {n} 个超过 {retention_days} 天的过期文件",
                      file=__import__("sys").stderr, flush=True)
        except Exception as _e:  # noqa: BLE001
            print(f"[智枢] 媒体留存清理失败：{_e!r}", file=__import__("sys").stderr, flush=True)


async def lifespan(app: FastAPI):
    # 启动即异步连接已启用的 MCP 服务器、注册插件/MCP 工具（不阻塞请求）
    import asyncio
    _boot_tasks: list = []
    try:
        # 启动期后台任务需持有引用：否则 GC 可能提前回收（asyncio 只持弱引用），
        # 且关停时无从取消 → "Task was destroyed but it is pending" 噪声与资源泄漏。
        _boot_tasks.append(asyncio.create_task(get_ctx().modules.refresh()))
        # 初始化外部长期记忆 provider（向量记忆 opt-in；未开启时 memory_manager 为 None，跳过）
        if get_ctx().memory_manager is not None:
            _boot_tasks.append(asyncio.create_task(get_ctx().memory_manager.initialize()))
        # 启动定时任务调度器（任务定义持久化，重启后自动续算）
        get_ctx().cron.start()
        # 可选媒体留存清理（仅当 media.retention_days>0 启用；默认 0=永久保留，
        # 避免系统自动删除用户已生成文件而重现「文件已清理」类投诉）。
        if getattr(get_ctx().cfg.media, "retention_days", 0) > 0:
            _boot_tasks.append(asyncio.create_task(
                _media_sweep_loop(get_ctx().media, get_ctx().cfg.media.retention_days)))
    except Exception as _e:
        import traceback as _tb
        print(f"[智枢] 启动期后台任务初始化失败：{_e!r}", file=__import__("sys").stderr, flush=True)
        _tb.print_exc()
    yield
    # ---- 关停：回收全局资源，避免连接 / 子进程 / 后台任务泄漏 ----
    # （此前 lifespan 只有启动分支，没有任何 teardown：HTTP 连接池、MCP 子进程、
    #   cron 循环都靠进程退出被动回收，reload / 多次 create_app 场景会累积泄漏。）
    import sys as _sys
    # 关停期每一步都必须「自包含」：任何一步抛出都不能带走后续步骤，否则先失败的
    # 一步会让排在后面的资源永远不被回收。特别注意 asyncio.CancelledError 继承自
    # BaseException，`except Exception` 抓不到——它一旦冲出 teardown，ASGI 层会把
    # 整个 lifespan 判定为「被取消」（TestClient 表现为关停 CancelledError）。
    try:
        from .core.providers.client import aclose_shared_http
        await aclose_shared_http()
    except (asyncio.CancelledError, Exception) as _e:  # noqa: BLE001
        print(f"[智枢] 关停时关闭 LLM 连接池失败：{_e!r}", file=_sys.stderr, flush=True)
    try:
        await get_ctx().cron.stop()
    except (asyncio.CancelledError, Exception) as _e:  # noqa: BLE001
        print(f"[智枢] 关停时停止定时任务失败：{_e!r}", file=_sys.stderr, flush=True)
    # 回收启动期后台任务（refresh / memory 初始化）：未完成则取消并等待其收敛
    for _t in _boot_tasks:
        try:
            if not _t.done():
                _t.cancel()
            await _t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    try:
        mods = get_ctx().modules
        for _name in list(getattr(mods, "clients", {}).keys()):
            try:
                await mods.clients[_name].close()
            except (asyncio.CancelledError, Exception):
                pass
        getattr(mods, "clients", {}).clear()
    except (asyncio.CancelledError, Exception) as _e:  # noqa: BLE001
        print(f"[智枢] 关停时断开 MCP 连接失败：{_e!r}", file=_sys.stderr, flush=True)


def create_app(cfg: ZhishuConfig) -> FastAPI:
    import sys

    # 环境变量覆盖签名密钥（便于容器部署无需改配置文件）
    env_secret = os.environ.get("ZHISHU_SECRET", "").strip()
    if env_secret:
        cfg.security.secret = env_secret

    # 安全自检（硬闸门）：默认签名密钥意味着任何人都能离线伪造任意用户/角色的
    # token（含 admin），等同鉴权完全失效。开启鉴权时禁止以默认密钥启动，
    # 仅当显式设置 ZHISHU_ALLOW_INSECURE_DEFAULTS=1（本地开发）才放行。
    if cfg.security.enable_auth and cfg.security.secret == "change-me-zhishu-secret":
        if os.environ.get("ZHISHU_ALLOW_INSECURE_DEFAULTS") == "1":
            print("[智枢][安全警告] 默认 secret + ZHISHU_ALLOW_INSECURE_DEFAULTS=1：仅限本地开发！",
                  file=sys.stderr, flush=True)
        else:
            print("[智枢][安全错误] 检测到默认签名密钥(change-me-zhishu-secret)，"
                  "存在 token 伪造风险，已拒绝启动。请任选其一：\n"
                  "  1. 在部署配置(zhishu.yaml)中设置 security.secret 为强随机值；\n"
                  "  2. 设置环境变量 ZHISHU_SECRET=<强随机值>；\n"
                  "  3. 仅限本地开发：设置 ZHISHU_ALLOW_INSECURE_DEFAULTS=1 跳过本检查。",
                  file=sys.stderr, flush=True)
            raise SystemExit(2)
    # 安全自检：enable_auth=False 时所有请求以 anonymous+admin 身份运行、数据不分
    # 用户混存。仅适合本机单人使用；若同时监听非回环地址（对外可达），等同把
    # 管理员权限暴露给整个网络，硬性拒绝启动（ZHISHU_ALLOW_INSECURE_DEFAULTS=1 可放行）。
    if not cfg.security.enable_auth:
        _loopback = str(getattr(cfg.server, "host", "")).strip() in (
            "127.0.0.1", "localhost", "::1")
        if _loopback or os.environ.get("ZHISHU_ALLOW_INSECURE_DEFAULTS") == "1":
            print("[智枢][安全警告] 鉴权已关闭(enable_auth=false)：所有请求以匿名 admin "
                  "运行、数据不分用户隔离，仅限本机单人使用！",
                  file=sys.stderr, flush=True)
        else:
            print(f"[智枢][安全错误] 鉴权已关闭(enable_auth=false)且监听非回环地址 "
                  f"({cfg.server.host})：任何能访问本服务的人都将获得匿名管理员权限。"
                  "已拒绝启动。请任选其一：\n"
                  "  1. 在部署配置(zhishu.yaml)中开启 security.enable_auth: true；\n"
                  "  2. 将 server.host 改为 127.0.0.1（仅本机访问）；\n"
                  "  3. 明确接受风险：设置 ZHISHU_ALLOW_INSECURE_DEFAULTS=1 跳过本检查。",
                  file=sys.stderr, flush=True)
            raise SystemExit(2)
    if cfg.security.admin_password == "zhishu@2026":
        print("[智枢][安全警告] 正在使用默认管理员口令，存在未授权登录风险！"
              "请在部署配置(zhishu.yaml)中修改 security.admin_password。",
              file=sys.stderr, flush=True)

    init_ctx(cfg)
    app = FastAPI(title="智枢智能体 Zhishu Agent", version=APP_VERSION, lifespan=lifespan)

    # 同源部署（FastAPI 直接托管前端）下本不需 CORS；保留以兼容独立前端/调试。
    # 采用 Bearer Token 鉴权（非 Cookie），故 credentials 置 False，避免
    # allow_origins="*" 与 allow_credentials=True 的浏览器安全冲突。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], allow_credentials=False,
    )

    # ---- 业务路由（需在静态托管前注册）----
    app.include_router(api_pkg.chat_router)
    app.include_router(api_pkg.models_router)
    app.include_router(api_pkg.knowledge_router)
    app.include_router(api_pkg.auth_router)
    app.include_router(api_pkg.users_router)
    app.include_router(api_pkg.admin_router)
    app.include_router(api_pkg.conversations_router)
    app.include_router(api_pkg.modules_router)
    app.include_router(api_pkg.agents_router)
    app.include_router(api_pkg.cron_router)
    app.include_router(api_pkg.settings_router)
    # OpenAI 兼容服务端网关（/v1/chat/completions + /v1/models），须在 SPA catch-all 之前注册
    app.include_router(api_pkg.openai_router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "zhishu-agent", "version": APP_VERSION}

    # ---- 多模态产物托管（/media，需在 SPA catch-all 之前挂载）----
    # 安全：/media 存放所有用户的附件与生成产物，挂载鉴权闸门中间件，
    # 未登录（无有效 token）一律 401。token 来源三选一：
    #   Authorization 头（API 调用） / ?token= 查询参数（外部工具） /
    #   HttpOnly Cookie（浏览器 <img>/<a>，登录及 /auth/me 时自动种下）。
    @app.middleware("http")
    async def media_auth_gate(request, call_next):
        p = request.url.path
        if p == "/media" or p.startswith("/media/"):
            ctx = get_ctx()
            if ctx.cfg.security.enable_auth:
                from urllib.parse import unquote
                token = None
                auth_hdr = request.headers.get("authorization") or ""
                if auth_hdr:
                    token = auth_hdr[7:] if auth_hdr.startswith("Bearer ") else auth_hdr
                if not token:
                    token = request.query_params.get("token")
                if not token:
                    raw_cookie = request.cookies.get("zs_media_token")
                    token = unquote(raw_cookie) if raw_cookie else None
                user = ctx.auth.verify(token) if token else None
                if not user:
                    return JSONResponse({"detail": "未登录或登录已过期，无法访问受保护资源"},
                                        status_code=401)
                # 供 serve_media 回退查找使用（已在网关校验，无额外越权风险）
                request.state.user = user
                # 越权防护：所有媒体按 owner 段位隔离校验，非本人/非管理员拒绝。
                #   /media/<owner>/...             → owner 段为 parts[1]
                #   /media/attachments/<owner>/... → owner 段为 parts[2]
                # 单段 /media/<name>（缺 owner 段，常见于模型省略 owner 的链接）不做段位校验，
                # 交由 serve_media 在用户授权目录内回退查找（已按 user 限定范围，不越权）。
                # 取消「附件目录仅鉴权、凭文件名不可猜测」的弱隔离（security by obscurity），
                # 任何用户的附件均严格按归属校验，杜绝凭 UUID 猜测越权下载他人文件。
                parts = [s for s in p.split("/") if s]
                if len(parts) >= 2 and parts[0] == "media" and len(parts) >= 3:
                    seg = parts[2] if (parts[1] == "attachments") else parts[1]
                    if seg != (user.get("u") or "") and (user.get("r") or "") != "admin":
                        return JSONResponse({"detail": "无权访问该资源"}, status_code=403)
        return await call_next(request)

    media_dir = os.path.join(cfg.server.data_dir, cfg.media.store_dir)
    media_dir_real = os.path.realpath(media_dir)
    os.makedirs(media_dir, exist_ok=True)

    # 自定义 /media 下载路由（替代原生 StaticFiles 挂载）。对比 Hermes Web UI 的
    # /api/files/download，这里要做得更稳妥：
    #   1) 显式 Content-Disposition: attachment —— 让浏览器「下载」而非内联（CSV/HTML 等
    #      直接内联会破坏「生成文件→可点击下载」体验，Hermes 同样显式设 attachment）；
    #   2) filename=ASCII 兜底 + filename*=UTF-8'' 真名 —— 中文/特殊字符文件名不乱码；
    #   3) Content-Type 取自媒体 MIME 单一真源（media.MEDIA_MIME），不再依赖 mimetypes 漂移；
    #   4) realpath 越权防护 —— 即便上层 slug 已防穿越，仍做纵深防御；
    #   5) 单次下载大小上限 —— 防超大文件拖垮服务。
    # 多租户归属隔离与鉴权由上方 media_auth_gate 中间件统一把关，此处只负责安全落地文件流。
    _MEDIA_SERVE_MAX_BYTES = 200 * 1024 * 1024  # 200MB

    @app.get("/media/{file_path:path}")
    async def serve_media(file_path: str, request: Request):
        from .core.media import media_mime, content_disposition
        # 归一化并对齐媒体根目录，杜绝路径穿越（media/owner 之外一律拒绝）
        candidate = os.path.normpath(os.path.join(media_dir_real, file_path))
        if candidate != media_dir_real and not candidate.startswith(media_dir_real + os.sep):
            return JSONResponse({"detail": "非法路径"}, status_code=400)
        if not os.path.isfile(candidate):
            # 容错回退：链接格式偏差（缺 owner 段 / 缺扩展名 / 历史偏差）但文件仍在授权
            # 目录时，按文件名找回真实文件，避免用户看到「文件不存在或已被清理」而文件其实可下载。
            fb = resolve_media_fallback(
                file_path, getattr(request.state, "user", None),
                media_dir_real, _MEDIA_SERVE_MAX_BYTES,
            )
            if not fb:
                return JSONResponse({"detail": "文件不存在或已被清理"}, status_code=404)
            # 纵深防御：回退命中的文件必须仍在媒体根内
            fb = os.path.realpath(fb)
            if fb != media_dir_real and not fb.startswith(media_dir_real + os.sep):
                return JSONResponse({"detail": "非法路径"}, status_code=400)
            candidate = fb
        try:
            size = os.path.getsize(candidate)
        except OSError:
            return JSONResponse({"detail": "无法读取文件"}, status_code=500)
        if size > _MEDIA_SERVE_MAX_BYTES:
            return JSONResponse({"detail": "文件过大，已超过下载上限"}, status_code=413)
        name = os.path.basename(candidate)
        # ASCII 兜底名 + RFC 5987 UTF-8 真名，保证各类浏览器下文件名均正确
        try:
            disp = content_disposition(name)
            mime = media_mime(name)
        except Exception:
            disp = "attachment"
            mime = "application/octet-stream"
        return FileResponse(
            candidate,
            media_type=mime,
            headers={
                "Content-Disposition": disp,
                "Cache-Control": "private, no-store",
            },
        )

    # ---- 静态前端（SPA fallback，同源）----
    static_dir = cfg.server.static_dir
    if not static_dir or not os.path.isdir(static_dir):
        default_static = os.path.join(os.path.dirname(__file__), "static")
        if os.path.isdir(default_static) and os.path.exists(os.path.join(default_static, "index.html")):
            static_dir = default_static

    if static_dir and os.path.isdir(static_dir):
        static_real = os.path.realpath(static_dir)

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            # 防路径穿越：归一化后必须仍位于 static_dir 之内，否则一律回退 index.html
            candidate = os.path.normpath(os.path.join(static_real, full_path))
            if candidate != static_real and not candidate.startswith(static_real + os.sep):
                index = os.path.join(static_real, "index.html")
                if os.path.exists(index):
                    return FileResponse(
                        index, headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
                    )
                return JSONResponse({"detail": "智枢前端未构建"})
            if os.path.isfile(candidate):
                resp = FileResponse(candidate)
                # index.html 不缓存（确保浏览器每次获取最新版本，引用正确的 JS/CSS hash）
                if full_path in ("index.html", "", "/"):
                    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                return resp
            index = os.path.join(static_real, "index.html")
            if os.path.exists(index):
                resp = FileResponse(
                    index, headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
                )
                return resp
            return JSONResponse({"detail": "智枢前端未构建"})
    else:
        @app.get("/{full_path:path}")
        async def spa_missing(full_path: str):
            return JSONResponse({
                "detail": "前端未构建。请先构建 frontend（npm run build），"
                          "或将产物放入 backend/zhishu/static，或通过 --static 指定目录。"
            })

    return app


def main():
    parser = argparse.ArgumentParser(prog="zhishu", description="智枢智能体（国产融合版）")
    parser.add_argument("--config", default=os.environ.get("ZHISHU_CONFIG", "deploy/zhishu.yaml"))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--static", default=None, help="前端构建产物目录")
    args = parser.parse_args()

    cfg = ZhishuConfig.load(args.config)
    if args.host:
        cfg.server.host = args.host
    if args.port:
        cfg.server.port = args.port
    if args.static:
        cfg.server.static_dir = args.static

    app = create_app(cfg)
    if cfg.security.enable_sm:
        try:
            import gmssl  # noqa: F401
        except Exception:
            print("[智枢][安全告警] 未检测到国密库 gmssl：Provider 密钥等敏感字段将以 XOR 混淆而非 SM4 加密落盘"
                  "（非强加密）。生产环境建议 `pip install gmssl` 后重启。")
    if getattr(cfg.server, "workers", 1) and cfg.server.workers > 1:
        print(f"[智枢][架构告警] workers={cfg.server.workers}：本服务使用进程内单例"
              "（限流器/配额/定时调度/工具注册表/会话缓存），多进程下各进程状态独立，将导致限流与定时任务行为不一致。"
              "生产建议 workers=1；若需横向扩展请改用共享存储（Redis/外部数据库）并启用单进程定时协调。")
    import uvicorn
    print(f"[智枢] 启动于 http://{cfg.server.host}:{cfg.server.port}  "
          f"(鉴权={'开' if cfg.security.enable_auth else '关'}, "
          f"默认模型={cfg.default_model})")
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, workers=cfg.server.workers)


if __name__ == "__main__":
    main()
