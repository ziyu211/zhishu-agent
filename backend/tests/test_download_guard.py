"""下载链接护栏单测：覆盖 needs_guard 判定与 guard_download_links 兜底行为。

场景：
  1. 无 media link 时不触发（正常回复不受干扰）
  2. final 已透传 /media 链接时不触发（模型已正确展示）
  3. 模型搪塞且未透传链接 → 触发，补回链接、清除搪塞句
  4. 中文逗号长搪塞句整体被清、链接补回
  5. 链接去重保序
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.agent.download_guard import (
    extract_media_links,
    needs_guard,
    guard_download_links,
    find_leaked_paths,
    strip_leaked_paths,
    strip_evasion,
)

passed = 0
failed = 0

def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {msg}")
    else:
        failed += 1
        print(f"  [FAIL] {msg}")

LINKS = ["/media/alice/ip_20.1.106.250_log.csv", "/media/alice/ip_unique.csv"]

# 1. 无 media link
check(needs_guard("这是普通回复", []) is False, "无 media link 不触发")
check(guard_download_links("普通回复", [])[1] is False, "无 media link 不改写")

# 2. 已透传链接
final_ok = "已处理完成，下载：[ip.csv](/media/alice/ip.csv)"
check(needs_guard(final_ok, LINKS) is False, "已透传链接不触发")
check(guard_download_links(final_ok, LINKS)[0] == final_ok, "已透传链接保持原样")

# 3. 搪塞 + 未透传 → 触发并补回
evasion = ("当前内网沙箱环境不支持生成外网可点击下载链接，"
           "请联系管理员从上述沙箱路径获取文件")
new, trig = guard_download_links(evasion, LINKS)
check(trig is True, "搪塞+未透传 → 触发")
check("/media/alice/ip_20.1.106.250_log.csv" in new, "补回了第一个链接")
check("/media/alice/ip_unique.csv" in new, "补回了第二个链接")
check("请联系管理员" not in new, "搪塞特征句被清除")
check("外网" not in new, "外网措辞被清除")

# 4. 模型先正常输出、末尾搪塞 → 正常内容保留、搪塞段删除
mixed = ("我已为你提取完成 IP 日志。\n"
         "当前内网沙箱环境不支持生成外网可点击下载链接，请联系管理员从上述沙箱路径获取文件")
new2, trig2 = guard_download_links(mixed, LINKS)
check(trig2 is True, "混合内容触发")
check("我已为你提取完成 IP 日志" in new2, "正常内容保留")
check("请联系管理员" not in new2, "搪塞段删除")

# 5. 去重保序
dup = ["/media/a/x.csv", "/media/a/x.csv", "/media/a/y.csv"]
_, _ = guard_download_links(evasion, dup)
# 直接验证 extract 去重在 guard 内
n3, _ = guard_download_links(evasion, dup)
check(n3.count("/media/a/x.csv") == 1, "重复链接去重")
check(n3.index("/media/a/x.csv") < n3.index("/media/a/y.csv"), "链接顺序保持")

# 6. 泄漏内部绝对路径：提取
leaked = ("⬇️ 获取方式\n请联系管理员从沙箱路径获取文件：\n"
          "/app/backend/data/sandbox/2026年4月工资表_总结.txt\n"
          "/app/backend/data/output/2026年4月工资表_总结.txt\n"
          "或者您可以直接复制上面的内容使用。")
paths = find_leaked_paths(leaked)
check("/app/backend/data/sandbox/2026年4月工资表_总结.txt" in paths, "识别沙箱泄漏路径")
check("/app/backend/data/output/2026年4月工资表_总结.txt" in paths, "识别 output 泄漏路径")
check(extract_media_links(leaked) == [], "不含 /media 链接时不误报")

# 7. 剥离泄漏路径 + 搪塞话术
clean = strip_evasion(leaked)
clean = strip_leaked_paths(clean)
check("/app/backend/data/sandbox/" not in clean, "沙箱路径被剥离")
check("/app/backend/data/output/" not in clean, "output 路径被剥离")
check("请联系管理员" not in clean, "搪塞句被剥离")
check("获取方式" not in clean, "孤立「获取方式」标题被清理")

# 8. /media 链接不被当作泄漏路径误删
media_only = "下载：[x.csv](/media/alice/x.csv)"
check(find_leaked_paths(media_only) == [], "/media 链接不算泄漏路径")
check(strip_leaked_paths(media_only) == media_only, "/media 链接原样保留")

# 9. 系统提示不再暴露沙箱绝对路径
from zhishu.core.agent.system_prompt import _TOOL_GUIDANCE as _TG
check("data/sandbox" not in _TG, "系统提示不再含 data/sandbox 路径示例")
check("沙箱" not in _TG, "系统提示不再含「沙箱」字样")
check("/media/" in _TG, "系统提示仍指引使用 /media 下载链接")

print(f"\n结果：{passed} 通过 / {failed} 失败")
sys.exit(1 if failed else 0)
