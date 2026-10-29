"""Render an ATS-friendly CV (DOCX, optionally PDF) from a draft markdown file.

Usage:
    python render_cv.py <draft.md> <output.docx> [--start "<line>"] [--paper A4|Letter] [--pdf]

Draft layout (see templates/cv_draft.template.md):
    ## Header        -> line 1 name, line 2 headline, line 3 contact (optional)
    ## <Any section> -> rendered in the order written; the heading text is printed as is,
                        so a Romanian draft simply uses Romanian headings.
       "### A | B | C"  -> entry: A bold, B after a dash, C right-aligned (dates)
       plain line right after "###" -> secondary line (team / location), italic
       "- Label: text"  -> bullet with a bold label when the label is short
       "- text"         -> bullet
       other lines      -> paragraph
--start: render only the block after the line equal to <line>, up to the next "# " heading
         (lets one draft hold an EN and a RO version).
--pdf:   converts with LibreOffice (soffice) or, on Windows, Microsoft Word; then reports the
         page count when pypdf is installed.
"""
import os
import re
import shutil
import subprocess
import sys

import docx
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

FONT = "Arial"
ACCENT = RGBColor(0x1F, 0x3A, 0x5F)
MUTED = RGBColor(0x55, 0x55, 0x55)
HEADER_NAMES = {"header", "antet"}


def parse_md(path, start=None):
    text = re.sub(r"<!--.*?-->", "", open(path, encoding="utf-8").read(), flags=re.S)
    lines = text.splitlines()
    if start:
        try:
            lines = lines[next(i for i, l in enumerate(lines) if l.strip() == start) + 1:]
        except StopIteration:
            sys.exit(f"--start line not found: {start}")
    header, sections, sec = [], [], None
    for raw in lines:
        l = raw.strip()
        if l.startswith("# ") and (sec is not None or header):
            break
        if l.startswith("## "):
            title = l[3:].strip()
            if title.lower() in HEADER_NAMES:
                sec = "header"
            else:
                sec = {"title": title, "items": []}
                sections.append(sec)
            continue
        if sec is None or not l or l == "---":
            continue
        if sec == "header":
            header.append(l)
            continue
        items = sec["items"]
        if l.startswith("### "):
            items.append({"type": "entry", "parts": [p.strip() for p in l[4:].split(" | ")],
                          "sub": None, "bullets": []})
        elif l.startswith("- "):
            if items and items[-1]["type"] == "entry":
                items[-1]["bullets"].append(l[2:].strip())
            else:
                items.append({"type": "bullet", "text": l[2:].strip()})
        elif items and items[-1]["type"] == "entry" and not items[-1]["bullets"] and items[-1]["sub"] is None:
            items[-1]["sub"] = l
        else:
            items.append({"type": "para", "text": l})
    if not header:
        sys.exit("Draft has no '## Header' section (line 1 name, line 2 headline).")
    return header, sections


def run(p, text, size=10, bold=False, italic=False, color=None):
    r = p.add_run(text)
    r.font.name = FONT
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    r.font.size = Pt(size)
    r.bold, r.italic = bold, italic
    if color is not None:
        r.font.color.rgb = color
    return r


def spacing(p, before=0, after=2):
    f = p.paragraph_format
    f.space_before, f.space_after = Pt(before), Pt(after)
    f.line_spacing = 1.0


def bottom_border(p):
    ppr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "1F3A5F")):
        b.set(qn(k), v)
    bdr.append(b)
    ppr.append(bdr)


def bullet(d, text, labeled=True):
    p = d.add_paragraph(style="List Bullet")
    spacing(p, 0, 1)
    if labeled and ": " in text and len(text.split(": ", 1)[0]) <= 45:
        label, rest = text.split(": ", 1)
        run(p, label + ": ", bold=True)
        run(p, rest)
    else:
        run(p, text)


def render(header, sections, out, paper):
    d = docx.Document()
    s = d.sections[0]
    if paper.lower() == "letter":
        s.page_width, s.page_height = Inches(8.5), Inches(11)
    else:
        s.page_width, s.page_height = Inches(8.27), Inches(11.69)
    for side in ("left_margin", "right_margin"):
        setattr(s, side, Inches(0.7))
    s.top_margin = s.bottom_margin = Inches(0.6)
    d.styles["Normal"].font.name = FONT
    d.styles["Normal"].font.size = Pt(10)
    text_width = s.page_width - s.left_margin - s.right_margin

    p = d.add_paragraph()
    spacing(p, 0, 0)
    run(p, header[0], size=20, bold=True, color=ACCENT)
    if len(header) > 1:
        p = d.add_paragraph()
        spacing(p, 0, 2)
        run(p, header[1], size=11, bold=True)
    for extra in header[2:]:
        p = d.add_paragraph()
        spacing(p, 0, 2)
        run(p, extra, size=9, color=MUTED)

    for sec in sections:
        p = d.add_paragraph()
        spacing(p, 8, 3)
        run(p, sec["title"].upper(), size=11, bold=True, color=ACCENT)
        bottom_border(p)
        for it in sec["items"]:
            if it["type"] == "entry":
                parts = it["parts"]
                p = d.add_paragraph()
                spacing(p, 4, 0)
                p.paragraph_format.keep_with_next = True
                run(p, parts[0], size=10.5, bold=True)
                middle = parts[1:-1] if len(parts) > 2 else parts[1:]
                if middle:
                    run(p, " — " + " | ".join(middle), size=10.5)
                if len(parts) > 2:
                    p.paragraph_format.tab_stops.add_tab_stop(text_width, WD_TAB_ALIGNMENT.RIGHT)
                    run(p, "\t" + parts[-1], size=9.5, color=MUTED)
                if it["sub"]:
                    p = d.add_paragraph()
                    spacing(p, 0, 1)
                    p.paragraph_format.keep_with_next = True
                    run(p, it["sub"], size=9, italic=True, color=MUTED)
                for b in it["bullets"]:
                    bullet(d, b, labeled=False)
            elif it["type"] == "bullet":
                bullet(d, it["text"])
            else:
                p = d.add_paragraph()
                spacing(p, 0, 2)
                run(p, it["text"])
    d.save(out)
    print("saved", out)


def to_pdf(docx_path):
    pdf = os.path.splitext(docx_path)[0] + ".pdf"
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if office:
        subprocess.run([office, "--headless", "--convert-to", "pdf", "--outdir",
                        os.path.dirname(os.path.abspath(docx_path)), docx_path], check=True,
                       capture_output=True)
    elif os.name == "nt":
        src, dst = os.path.abspath(docx_path), os.path.abspath(pdf)
        ps = ("$w = New-Object -ComObject Word.Application; $w.Visible = $false; "
              f"$d = $w.Documents.Open('{src}'); $d.SaveAs2('{dst}', 17); $d.Close(); $w.Quit()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)
    else:
        print("WARN no PDF converter found (LibreOffice or Word); export the DOCX to PDF manually")
        return None
    print("saved", pdf)
    try:
        from pypdf import PdfReader
        print("pages", len(PdfReader(pdf).pages))
    except ImportError:
        print("NOTE install pypdf to get the page count")
    return pdf


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    opts = {"--start": None, "--paper": "A4"}
    for k in list(opts):
        if k in args:
            i = args.index(k)
            opts[k] = args[i + 1]
            args = args[:i] + args[i + 2:]
    want_pdf = "--pdf" in args
    args = [a for a in args if a != "--pdf"]
    if len(args) != 2:
        sys.exit(__doc__)
    md, out = args
    header, sections = parse_md(md, opts["--start"])
    render(header, sections, out, opts["--paper"])
    leftovers = [l for l in open(md, encoding="utf-8") if "[~" in l or "[TODO" in l]
    if leftovers:
        print(f"WARN {len(leftovers)} placeholder line(s) still in the draft")
    if want_pdf:
        to_pdf(out)


if __name__ == "__main__":
    main()
