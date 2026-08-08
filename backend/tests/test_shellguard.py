"""shellguard.check_command 回归测试：
- enforce=false：放行除高危外的任意命令
- enforce=true + 默认白名单：which / cd / apt-get 放行，gcc 等仍拦截
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
    print("[2] enforce=true + 默认白名单：which / cd / apt-get 放行，gcc 拦截")
    _check(sg.check_command("which node", enforce_allowlist=True) is None,
           "which 已加入默认白名单，放行")
    _check(sg.check_command("cd /workspace && pwd", enforce_allowlist=True) is None,
           "cd 已加入默认白名单，放行")
    _check(sg.check_command("apt-get install -y nodejs", enforce_allowlist=True) is None,
           "apt-get 已加入默认白名单，放行")
    _check(sg.check_command("gcc -o a a.c", enforce_allowlist=True) is not None,
           "gcc 不在默认白名单，拦截")
    _check("不在白名单内" in (sg.check_command("gcc main.c", enforce_allowlist=True) or ""),
           "拦截原因含『不在白名单内』")


def test_deny_always_wins():
    print("[3] 高危清单优先级高于白名单")
    _check(sg.check_command("ls; rm -rf /", allowlist=["ls"], enforce_allowlist=True) is not None,
           "白名单内命令 + 高危组合仍拒绝")


def test_substitution_blocked():
    print("[4] 命令替换 / 进程替换被禁")
    _check(sg.check_command("echo $(whoami)", enforce_allowlist=True) is not None,
           "$( ) 命令替换被拦截")


def test_multiline_scripts_allowed():
    print("[5] 多行 / 引号内脚本正确放行（方案 A 修复）")
    heredoc = "python3 <<'EOF'\nimport os\nprint(os.getcwd())\nEOF"
    _check(sg.check_command(heredoc, enforce_allowlist=True) is None,
           "python3 <<'EOF' heredoc 放行（正文不再当命令）")
    heredoc2 = "python3 <<PYEOF\nimport json\nprint(json.dumps({}))\nPYEOF"
    _check(sg.check_command(heredoc2, enforce_allowlist=True) is None,
           "python3 <<PYEOF 无引号分隔符 heredoc 放行")
    multiline = 'python3 -c "import json\nprint(json.dumps({}))"'
    _check(sg.check_command(multiline, enforce_allowlist=True) is None,
           'python3 -c "含换行" 引号内换行不当分隔符，放行')
    semi = "python3 -c 'a=1; b=2; print(a+b)'"
    _check(sg.check_command(semi, enforce_allowlist=True) is None,
           "python3 -c '含;' 引号内分号不当分隔符，放行")
    pipe = "cat a.txt | python3 -c 'import sys; print(len(sys.stdin.read()))'"
    _check(sg.check_command(pipe, enforce_allowlist=True) is None,
           "引号内 ; 与管道组合放行（python3 在白名单）")


def test_heredoc_high_risk_still_blocked():
    print("[6] heredoc 正文内的高危命令仍被拒绝清单拦下")
    bad = "python3 <<'EOF'\nimport os\nos.system('rm -rf /')\nEOF"
    _check(sg.check_command(bad, enforce_allowlist=True) is not None,
           "heredoc 正文里的 rm -rf / 仍被拒绝（完整文本检查）")


if __name__ == "__main__":
    test_enforce_false_allows_cd_and_apt()
    test_enforce_true_default_allowlist()
    test_deny_always_wins()
    test_substitution_blocked()
    test_multiline_scripts_allowed()
    test_heredoc_high_risk_still_blocked()
    print("\nALL shellguard TESTS PASSED")
