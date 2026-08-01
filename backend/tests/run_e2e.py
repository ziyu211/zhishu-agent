#!/usr/bin/env python3
"""智枢 e2e 总入口 —— 依次运行所有端到端测试套件，任一失败即非零退出。

设计要点：
  * 用「子进程」分别运行各测试文件，避免不同套件之间的全局上下文污染
    （多 Agent 测试会 init_ctx 改全局 ctx，HTTP 测试依赖 get_ctx，混跑会串）。
  * 每个测试文件自身已保证不依赖外部 LLM / 网络（用 FakeLLM 打桩），可重复运行。
  * 不依赖 pytest，纯标准库即可执行；也兼容在 CI 中直接调用。

运行：
  python tests/run_e2e.py            # 静默汇总（失败才打印详细）
  python tests/run_e2e.py --verbose # 实时透传各套件输出
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)

# 端到端套件清单（按依赖/稳定性顺序排列）
TESTS = [
    "test_multiagent_e2e.py",   # 进程内直调 Agent：委派/超时/RAG 继承/审计
    "test_chat_http_e2e.py",    # 真实 HTTP 全链路：鉴权/限流接入/SSE 流式
]


def run_one(fname: str, verbose: bool) -> int:
    path = os.path.join(HERE, fname)
    print(f"\n===== 运行 {fname} =====", flush=True)
    env = dict(os.environ)
    # 本地开发用默认密钥放行（HTTP 测试用同源密钥自签 token 绕登录）
    env.setdefault("ZHISHU_ALLOW_INSECURE_DEFAULTS", "1")
    # 子进程实時透传输出，便于在 CI 日志里直接看到进度
    proc = subprocess.run(
        [sys.executable, path],
        cwd=BACKEND,
        env=env,
        stdout=None if verbose else subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if not verbose and proc.stdout:
        sys.stdout.buffer.write(proc.stdout)
        sys.stdout.buffer.flush()
    return proc.returncode


def main() -> int:
    verbose = "--verbose" in sys.argv[1:] or "-v" in sys.argv[1:]
    failed = []
    for t in TESTS:
        rc = run_one(t, verbose)
        status = "PASS" if rc == 0 else f"FAIL(rc={rc})"
        print(f"----- {t}: {status} -----", flush=True)
        if rc != 0:
            failed.append(t)

    print("\n" + "=" * 50, flush=True)
    if failed:
        print(f"E2E 失败套件：{failed}")
        return 1
    print("ALL E2E SUITES PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
