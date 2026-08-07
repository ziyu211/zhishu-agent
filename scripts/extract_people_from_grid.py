# -*- coding: utf-8 -*-
"""从「网格卡片」式导出的 HTML 文本中抽取人员信息（Python2/3 双兼容版）。

设计要点（相比初版的关键改进）：
  * 完全不依赖外层 grid 容器的配平，避免嵌套 </div></div> 把字段截断；
  * 全文档扫描「标签 + 紧随其后的 value」配对，再以「姓名」为锚点把字段归组成人；
  * 兼容 title="..." / title='...' / data-title / 纯文本四种取值；
  * 兼容标签后接 &nbsp; : ： 等任意分隔（甚至无分隔）；
  * 编码自动探测（utf-8 -> gbk -> gb18030 -> 容错）；
  * 兼容 Python 2.7+ 与 Python 3.x：无类型注解、无 f-string、无 __future__ annotations，
    统一把文本转成 unicode(str) 后再做正则匹配；
  * --debug 输出每个标签命中数，方便定位抽不到的真实原因。

用法：
  python extract_people_from_grid.py <文件.html或.txt> [--debug]
  cat 文件 | python extract_people_from_grid.py --debug
"""
from __future__ import print_function

import json
import re
import sys
from collections import Counter

try:
    unicode
except NameError:
    unicode = str

# 关注的字段（顺序无关，按文档出现位置归组）
LABELS = [
    u"姓名", u"证件号", u"手机号", u"年龄", u"性别", u"民族", u"学历",
    u"婚姻状况", u"服务处所", u"户籍地址行政区划", u"户籍地址", u"积分值",
]
LABEL_ALT = {
    u"姓名": [u"姓名", u"名字", u"姓名:"],
    u"证件号": [u"证件号", u"身份证", u"身份证号", u"证件号码"],
    u"手机号": [u"手机号", u"电话", u"手机号码", u"联系电话"],
    u"积分值": [u"积分值", u"积分", u"分值"],
}


def _to_text(s):
    if isinstance(s, bytes):
        return s.decode("utf-8", "ignore")
    return s


def _emit(text, stream=sys.stdout):
    text = _to_text(text)
    try:
        stream.write(text + u"\n")
    except (UnicodeEncodeError, TypeError):
        try:
            buf = getattr(stream, "buffer", None)
            if buf is not None:
                buf.write(text.encode("utf-8") + b"\n")
            else:
                stream.write(text.encode("utf-8") + b"\n")
        except Exception:
            stream.write(str(text) + u"\n")


def _read_text(path):
    try:
        f = open(path, "rb")
    except IOError as e:
        _emit(u"无法打开文件: %s" % e, sys.stderr)
        sys.exit(1)
    data = f.read()
    f.close()
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", "ignore")


def _value_of(attrs, inner):
    """优先取 title / data-title，否则取标签内文本。"""
    m = re.search(r"""title\s*=\s*("|')(.*?)\1""", attrs, re.DOTALL)
    if m:
        return m.group(2).strip()
    m = re.search(r"""data-title\s*=\s*("|')(.*?)\1""", attrs, re.DOTALL)
    if m:
        return m.group(2).strip()
    txt = re.sub(r"<[^>]+>", "", inner or u"")
    return txt.strip()


def extract_people(content, debug=False):
    content = _to_text(content)
    # 全文档捕获 (label, attrs, inner, start_pos)
    token_re = re.compile(
        u"(?P<label>"
        + u"|".join(re.escape(l) for l in LABELS)
        + u""")[^\n<]*</span>\\s*<span(?P<attrs>[^>]*)>(?P<inner>.*?)</span>""",
        re.DOTALL,
    )

    def norm(label):
        for std, alts in LABEL_ALT.items():
            if label in alts:
                return std
        return label if label in LABELS else u""

    tokens = []
    for m in token_re.finditer(content):
        std = norm(m.group("label"))
        if not std:
            continue
        val = _value_of(m.group("attrs"), m.group("inner"))
        tokens.append((std, val, m.start()))

    if debug:
        c = Counter(t for t, _, _ in tokens)
        _emit(u"==DEBUG== 各字段命中数: %s  总配对: %d" % (dict(c), len(tokens)),
              sys.stderr)

    # 以「姓名」为锚点归组
    people = []
    cur = {}
    for std, val, _pos in tokens:
        if std == u"姓名":
            if cur.get(u"姓名"):
                people.append(cur)
            cur = {u"姓名": val}
        else:
            if not cur:
                continue
            cur.setdefault(std, val)
    if cur.get(u"姓名"):
        people.append(cur)

    # 去重（按姓名+证件号）
    seen = set()
    unique = []
    for p in people:
        key = (p.get(u"姓名", u""), p.get(u"证件号", u""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            {
                u"name": p.get(u"姓名", u""),
                u"id_number": p.get(u"证件号", u""),
                u"phone": p.get(u"手机号", u""),
                u"age": p.get(u"年龄", u""),
                u"gender": p.get(u"性别", u""),
                u"ethnicity": p.get(u"民族", u""),
                u"education": p.get(u"学历", u""),
                u"marital": p.get(u"婚姻状况", u""),
                u"workplace": p.get(u"服务处所", u""),
                u"address_division": p.get(u"户籍地址行政区划", u""),
                u"address": p.get(u"户籍地址", u""),
                u"score": p.get(u"积分值", u""),
            }
        )
    return unique


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    debug = "--debug" in sys.argv
    if args:
        content = _read_text(args[0])
    else:
        content = sys.stdin.read()

    people = extract_people(content, debug=debug)
    _emit(u"提取到 %d 人（去重后）\n" % len(people))
    for i, p in enumerate(people, 1):
        extra = u" ".join(
            u"%s:%s" % (k, p[k]) for k in ("id_number", "phone", "age", "gender", "score")
            if p.get(k)
        )
        _emit(u"%d. %s | %s" % (i, p[u"name"], extra))
    _emit(u"\n===JSON===")
    out = json.dumps(people, ensure_ascii=False, indent=2)
    _emit(out)


if __name__ == "__main__":
    main()
