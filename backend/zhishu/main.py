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
        # 初始化外部长期记忆 provider（向量记忆 opt-in；未开启时 initialize 为 no-op）
        asyncio.create_task(get_ctx().memory_manager.initialize())
        # 启动定时任务调度器（任务定义持久化，重启后自动续算）
        get_ctx().cron.start()
    except Exception as _e:
        import traceback as _tb
        print(f"[智枢] 启动期后台任务初始化失败：{_e!r}", file=__import__("sys").stderr, flush=True)
        _tb.print_exc()
    yield


def create_app(cfg: ZhishuConfig) -> FastAPI:
    init_ctx(cfg)
    # 安全自检：使用默认签名密钥或可猜测的管理员口令时发出告警（不阻断启动，
    # 但提醒运维在部署配置中覆盖 security.secret / security.admin_password）。
    if cfg.security.secret == "change-me-zhishu-secret" or cfg.security.admin_password == "zhishu@2026":
        import sys
        print("[智枢][安全警告] 正在使用默认 secret / 管理员口令，存在 token 伪造与未授权登录风险！"
              "请在部署配置(zhishu.yaml)中修改 security.secret 与 security.admin_password。",
              file=sys.stderr, flush=True)
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
        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            candidate = os.path.join(static_dir, full_path)
            if os.path.isfile(candidate):
                resp = FileResponse(candidate)
                # index.html 不缓存（确保浏览器每次获取最新版本，引用正确的 JS/CSS hash）
                if full_path == "index.html" or full_path == "" or full_path == "/":
                    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                return resp
            index = os.path.join(static_dir, "index.html")
            if os.path.exists(index):
                resp = FileResponse(index)
                resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
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
    import uvicorn
    print(f"[智枢] 启动于 http://{cfg.server.host}:{cfg.server.port}  "
          f"(鉴权={'开' if cfg.security.enable_auth else '关'}, "
          f"默认模型={cfg.default_model})")
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, workers=cfg.server.workers)


if __name__ == "__main__":
    main()
