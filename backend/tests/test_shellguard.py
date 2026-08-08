"""shellguard.check_command 回归测试：
- enforce=false：放行除高危外的任意命令
- enforce=true + 默认白名单：cd / pwd 等只读导航放行，apt-get 等仍拦截
- 高危清单始终拒绝（优先级高于 allowlist）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core import shellguard as sg


def _check(cond, msg):
    assert cond, "FAIL: " + msg
    print("  ✓", msg)


def test_enforce_false_allows_cd_and_apt():
    print("[1] enforce=false：放行 cd / apt-get（仅高危仍拒）")
    _check(sg.check_command("cd /workspace && ls", enforce_allowlist=False) is None,
           "cd 组合命令放行")
    _check(sg.check_command("apt-get install -y nodejs", enforce_allowlist=False) is None,
           "apt-get 放行（enforce 关闭）")
    _check(sg.check_command("rm -rf /", enforce_allowlist=False) is not None,
           "高危 rm -rf / 仍拒绝（优先级最高）")


def test_enforce_true_default_allowlist():
    print("[2] enforce=true + 默认白名单：cd 放行，apt-get 拦截")
    _check(sg.check_command("cd /workspace && pwd", enforce_allowlist=True) is None,
           "cd 已加入默认白名单，放行")
    _check(sg.check_command("apt-get install -y nodejs", enforce_allowlist=True) is not None,
           "apt-get 不在默认白名单，拦截")
    _check("不在白名单内" in (sg.check_command("apt-get update", enforce_allowlist=True) or ""),
           "拦截原因含『不在白名单内』")


def test_deny_always_wins():
    print("[3] 高危清单优先级高于白名单")
    _check(sg.check_command("ls; rm -rf /", allowlist=["ls"], enforce_allowlist=True) is not None,
           "白名单内命令 + 高危组合仍拒绝")


def test_substitution_blocked():
    print("[4] 命令替换 / 进程替换被禁")
    _check(sg.check_command("echo $(whoami)", enforce_allowlist=True) is not None,
           "$( ) 命令替换被拦截")


if __name__ == "__main__":
    test_enforce_false_allows_cd_and_apt()
    test_enforce_true_default_allowlist()
    test_deny_always_wins()
    test_substitution_blocked()
    print("\nALL shellguard TESTS PASSED")
