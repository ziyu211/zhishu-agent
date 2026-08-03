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
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import ZhishuConfig
from .context import init_ctx, get_ctx
from . import api as api_pkg


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动即异步连接已启用的 MCP 服务器、注册插件/MCP 工具（不阻塞请求）
    try:
        import asyncio
        asyncio.create_task(get_ctx().modules.refresh())
        # 初始化外部长期记忆 provider（向量记忆 opt-in；未开启时 memory_manager 为 None，跳过）
        if get_ctx().memory_manager is not None:
            asyncio.create_task(get_ctx().memory_manager.initialize())
        # 启动定时任务调度器（任务定义持久化，重启后自动续算）
        get_ctx().cron.start()
    except Exception as _e:
        import traceback as _tb
        print(f"[智枢] 启动期后台任务初始化失败：{_e!r}", file=__import__("sys").stderr, flush=True)
        _tb.print_exc()
    yield


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
    app = FastAPI(title="智枢智能体 Zhishu Agent", version="1.0.0", lifespan=lifespan)

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

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "zhishu-agent", "version": "1.0.0"}

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
                # 越权防护：所有媒体按 owner 段位隔离校验，非本人/非管理员拒绝。
                #   /media/<owner>/...             → owner 段为 parts[1]
                #   /media/attachments/<owner>/... → owner 段为 parts[2]
                # 取消「附件目录仅鉴权、凭文件名不可猜测」的弱隔离（security by obscurity），
                # 任何用户的附件均严格按归属校验，杜绝凭 UUID 猜测越权下载他人文件。
                parts = [s for s in p.split("/") if s]
                if len(parts) >= 2 and parts[0] == "media":
                    seg = parts[2] if (len(parts) >= 3 and parts[1] == "attachments") else parts[1]
                    if seg != (user.get("u") or "") and (user.get("r") or "") != "admin":
                        return JSONResponse({"detail": "无权访问该资源"}, status_code=403)
        return await call_next(request)

    media_dir = os.path.join(cfg.server.data_dir, cfg.media.store_dir)
    os.makedirs(media_dir, exist_ok=True)
    app.mount("/media", StaticFiles(directory=media_dir), name="media")

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
