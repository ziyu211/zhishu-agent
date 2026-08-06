"""从「网格卡片」式导出的 HTML 文本中抽取人员信息。

适用格式示例（本次报错文件 yj-2026-06-18...txt）：
    <div class="grid grid-cols-4 gap-y-3 gap-x-2" data-v-6b15a765="">
      <div class="flex items-start" ...>
        <span ...>姓名&nbsp;:</span>
        <span ... title="李龙" ...>李龙</span>
      </div>
      ... 证件号 / 手机号 / 积分值 等 ...
    </div>

要点（与原脚本的差别）：
  * 标签用 &nbsp; 而非空格，故用 [^<]* 吃掉「姓名」到 </span> 之间的任意内容；
  * grid 容器带额外 class（gap-y-3 等）与 data-v-* 属性，故用
    class="grid grid-cols-4[^"]*"[^>]*> 匹配开头；
  * 每个字段独立按「标签 + 紧随的 title 属性」抽取，顺序无关、缺字段不崩。
"""
import json
import re
import sys


def extract_people(content: str) -> list[dict]:
    # 每个人员卡片：一个 grid 容器
    grid_re = re.compile(
        r'<div class="grid grid-cols-4[^"]*"[^>]*>(.*?)</div>\s*</div>', re.DOTALL
    )

    def field(block: str, label: str) -> str:
        m = re.search(
            re.escape(label) + r'[^<]*</span>\s*<span[^>]*title="([^"]*)"', block
        )
        return m.group(1).strip() if m else ""

    people: list[dict] = []
    for gm in grid_re.finditer(content):
        block = gm.group(1)
        name = field(block, "姓名")
        if not name:
            continue
        people.append(
            {
                "name": name,
                "id_number": field(block, "证件号"),
                "phone": field(block, "手机号"),
                "age": field(block, "年龄"),
                "gender": field(block, "性别"),
                "ethnicity": field(block, "民族"),
                "education": field(block, "学历"),
                "marital": field(block, "婚姻状况"),
                "workplace": field(block, "服务处所"),
                "address_division": field(block, "户籍地址行政区划"),
                "address": field(block, "户籍地址"),
                "score": field(block, "积分值"),
            }
        )

    # 去重（按姓名）
    seen: set[str] = set()
    unique: list[dict] = []
    for p in people:
        if p["name"] not in seen:
            seen.add(p["name"])
            unique.append(p)
    return unique


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    else:
        content = sys.stdin.read()

    people = extract_people(content)
    print(f"提取到 {len(people)} 人（去重后）\n")
    for i, p in enumerate(people, 1):
        print(
            f"{i}. {p['name']} | 证件号:{p['id_number']} | 手机:{p['phone']} "
            f"| 年龄:{p['age']} | 性别:{p['gender']} | 积分:{p['score']}"
        )
    # 同时输出 JSON，方便后续处理
    print("\n===JSON===")
    print(json.dumps(people, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
