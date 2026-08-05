"""回归测试：办公文档 ZIP 劫持修复（闭环审计后的文件处理组件校验）。

历史严重缺陷（初版起存在）：
  rag.py 的 read_file_text 把 `.zip` 与「magic bytes = PK\\x03\\x04」的通用压缩包分支
  放在所有扩展名派发之前。但 .docx/.xlsx/.pptx/.odt/.ods/.odp/.epub 内部本身就是 ZIP
  容器，因此 100% 命中该分支，永远走不到专用解析器，被当成普通压缩包吐出原始 XML
  标签噪声（file_type 还被错标成 ZIP），知识库检索质量严重劣化。

本测试构造各类型样本，验证：
  1. OOXML/ODF/EPUB 走到专用解析器（file_type 正确，文本不含原始 XML 噪声）；
  2. 扩展名错配但 magic 实为办公文档时，经嗅探纠正为正确类型；
  3. 真正的 .zip 仍被正确识别为 ZIP 并解包；
  4. PDF/RTF/TXT/CSV 等既有路径不受影响。
"""
import io
import sys
import os
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zhishu.core.rag import read_file_text

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


def z(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data, *rest in members:
            if rest and rest[0]:
                zf.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_STORED)
            else:
                zf.writestr(name, data)
    return buf.getvalue()


def docx_bytes():
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           '<w:p><w:r><w:t>AlphaPara</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>BetaPara</w:t></w:r></w:p>'
           '</w:body></w:document>')
    return z([("[Content_Types].xml", '<?xml version="1.0"?><Types/>'),
              ("_rels/.rels", '<?xml version="1.0"?><Relationships><Relationship Id="rId1" Type="x" Target="word/document.xml"/></Relationships>'),
              ("word/document.xml", doc)])


def xlsx_bytes():
    ss = ('<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1"><si><t>CellA1</t></si></sst>')
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>')
    return z([("[Content_Types].xml", '<?xml version="1.0"?><Types/>'),
              ("xl/sharedStrings.xml", ss),
              ("xl/worksheets/sheet1.xml", sheet)])


def pptx_bytes():
    slide = ('<?xml version="1.0"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
             'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>SlideText</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    return z([("[Content_Types].xml", '<?xml version="1.0"?><Types/>'),
              ("ppt/slides/slide1.xml", slide)])


def odt_bytes():
    content = ('<?xml version="1.0"?><office:document-content xmlns:office="http://openoffice.org/2000/office" '
               'xmlns:text="http://openoffice.org/2000/text"><office:body><office:text>'
               '<text:p>ODFPara</text:p></office:text></office:body></office:document-content>')
    return z([("mimetype", "application/vnd.oasis.opendocument.text", True),
              ("content.xml", content)])


def epub_bytes():
    container = ('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           '<metadata></metadata>'
           '<manifest><item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/></manifest>'
           '<spine><itemref idref="c1"/></spine></package>')
    chap = '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body><p>EpubChapter</p></body></html>'
    return z([("mimetype", "application/epub+zip", True),
              ("META-INF/container.xml", container),
              ("OEBPS/content.opf", opf),
              ("OEBPS/chapter1.xhtml", chap)])


def rtf_bytes():
    return "{\\rtf1\\ansi RTF line one\\par RTF line two\\par}".encode("utf-8")


def pdf_bytes():
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Hello PDF text")
    data = doc.tobytes()
    doc.close()
    return data


def _assert_office(name, raw, expect_type, expect_substr):
    text, ftype = read_file_text(name, raw)
    check(ftype == expect_type, f"{name}: file_type={ftype} (expect {expect_type})")
    check(text.strip() != "", f"{name}: 提取到非空文本")
    check("<w:document" not in text and "PK" not in text[:10],
          f"{name}: 未被当成 ZIP 吐出原始 XML 噪声")
    check(expect_substr in text, f"{name}: 含预期文本片段「{expect_substr}」")


print("== 办公文档 ZIP 劫持修复回归 ==")

_assert_office("demo.docx", docx_bytes(), "DOCX", "AlphaPara")
_assert_office("demo.xlsx", xlsx_bytes(), "XLSX", "CellA1")
_assert_office("demo.pptx", pptx_bytes(), "PPTX", "SlideText")
_assert_office("demo.odt", odt_bytes(), "ODT", "ODFPara")
_assert_office("demo.epub", epub_bytes(), "EPUB", "EpubChapter")

# 扩展名错配但实为 docx：应经嗅探纠正为 DOCX（而非 ZIP）
text, ftype = read_file_text("mystery.bin", docx_bytes())
check(ftype == "DOCX", "mystery.bin(实为docx): 嗅探纠正为 DOCX")
check("AlphaPara" in text, "mystery.bin: 提取到正确正文")

# 真正的压缩包：仍应被识别为 ZIP 并解包
zip_raw = z([("inner.txt", "zip-inner-text")])
text, ftype = read_file_text("archive.zip", zip_raw)
check(ftype == "ZIP", "archive.zip: 仍正确识别为 ZIP")
check("zip-inner-text" in text, "archive.zip: 解包得到内部文本")

# 既有路径不受影响
t, ft = read_file_text("demo.pdf", pdf_bytes())
check(ft == "PDF" and "Hello PDF" in t, "PDF 路径正常")
t, ft = read_file_text("demo.rtf", rtf_bytes())
check(ft == "RTF" and "RTF line one" in t, "RTF 路径正常")
t, ft = read_file_text("demo.txt", "plain text hello".encode("utf-8"))
check(ft == "TXT" and "plain text" in t, "TXT 路径正常")
t, ft = read_file_text("demo.csv", "a,b\n1,2".encode("utf-8"))
check(ft == "CSV" and "1,2" in t, "CSV 路径正常")

print(f"\n结果：{passed} 通过 / {failed} 失败")
sys.exit(1 if failed else 0)
