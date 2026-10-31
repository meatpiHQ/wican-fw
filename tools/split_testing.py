#!/usr/bin/env python3
"""Split TESTING.md's long table rows and narrative sections into pages.

The "test kinds" table of TESTING.md carried the whole history of every
bench in its second cell (75 rows over 1500 characters, 283 KB of a 379 KB
file), which made the file unreadable in one go. This script moves every
row whose detail cell is longer than the threshold into
testing/<slug>.md (the full text, as paragraphs), leaves a one-line row
behind (the title, the verdict line, the duration, the link), and does the
same for the big narrative sections. Nothing is deleted: every character
of a moved cell lands on its page.

  python tools/split_testing.py .  [--apply] [--threshold 400]

Without --apply it prints the plan and the new table; with it, it writes
testing/*.md and rewrites TESTING.md. Written for the one split of
2026-10-31; kept so a row that grows long again can be moved the same way
(a slug that already has a page is suffixed, so merge by hand after).
"""
import argparse
import os
import re
import sys

ROW_RE = re.compile(r"^\|(.*)\|\s*$")
SPLIT_RE = re.compile(r"(?<!\\)\|")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
VERDICT_RE = re.compile(r"`([A-Z][A-Z0-9 /+-]*?(?:PASS|DONE|OK))`")
DURATION_RE = re.compile(r"(~\s?\d+(?:\.\d+)?\s?(?:min|s|h)\b|about \d+ min(?:utes)?)")
STAGE_RE = re.compile(r"test\.ps1 ([a-z0-9]+)")
SCRIPT_RE = re.compile(r"([A-Za-z0-9_]+)\.(?:py|mjs|sh)\b")
LINK_RE = re.compile(r"\]\((tools/|components/|test-reports/|internal/|release-notes/|main/|docs/|drivers/)")

# the narrative sections moved to pages: heading -> slug
SECTIONS = {
    "## The bench preflight (runs first, every kind)": "preflight",
    "## The system bench (the composed main firmware, end to end)": "system_bench",
    "## BenchBoard: the interactive test dashboard (2026-07-26)": "benchboard",
    "## Bench rig: control plane, recovery ladder, timeout policy (2026-07-26)": "bench_rig",
    "## ELM327 app responsiveness bench (PC-run, 2026-09-08)": "elm327_app_bench",
    "## Recording what an OBD app really sends: the ELM TCP tap (2026-09-23)": "elm_tcp_tap",
    "## Large-payload OBD bench: VTFullyRequestCk / 4 KB ISO-TP (PC-run, PCAN, 2026-09-08)": "large_payload_obd",
    "## ATMA flood bench (PC-run, PCAN, 2026-09-08)": "atma_flood",
    "## Autopid DTC/DBC PCAN benches (PC-run, 2026-07-22)": "autopid_dtc_dbc_pcan",
}


def unescape(cell):
    return cell.replace("\\|", "|")


def fix_links(text):
    return LINK_RE.sub(r"](../\1", text)


def paragraphs(text):
    """One table cell (one line of markdown) into paragraphs: a new one
    starts before every bold lead-in that follows a sentence end."""
    text = re.sub(r"(?<=[.)\]]) (?=\*\*)", "\n\n", text)
    text = re.sub(r"(?<=[.:;]) (?=- \*\*)", "\n\n", text)
    return text


def slug_for(cmd, used, title=""):
    m = STAGE_RE.search(cmd)
    probe = re.search(r"(probe_[A-Za-z0-9_]+)\.mjs", cmd)
    be = re.search(r"run_be\.py\s+\S+\s+\S*?([A-Za-z0-9_]+)\.be", cmd)
    if m:
        base = m.group(1)
    elif probe:
        base = probe.group(1)
    elif be:
        base = "be_" + be.group(1)
    else:
        m = SCRIPT_RE.search(cmd)
        if m and m.group(1) not in ("make_preview", "python3", "python"):
            base = m.group(1)
        else:
            words = re.sub(r"[^A-Za-z0-9 ]+", " ", title).split()
            base = "_".join(w.lower() for w in words[:4]) or "row"
    base = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_") or "row"
    slug = base
    n = 2
    while slug in used:
        slug = f"{base}_{n}"
        n += 1
    used.add(slug)
    return slug


def title_for(detail):
    m = BOLD_RE.search(detail)
    if m and m.start() == 0:
        t = m.group(1).strip().rstrip(":").strip()
        if len(t) > 140:
            t = t[:137].rsplit(" ", 1)[0] + "..."
        return t
    t = re.split(r"(?<=[.;:])\s", detail, 1)[0].strip()
    t = t.replace("**", "").replace("`", "").rstrip(".;:")
    if len(t) > 120:
        t = t[:117].rsplit(" ", 1)[0] + "..."
    return t


def summary_for(detail, slug):
    title = title_for(detail)
    bits = []
    v = VERDICT_RE.search(detail)
    if v:
        bits.append(f"prints `{v.group(1)}`")
    d = DURATION_RE.search(detail)
    if d:
        bits.append(d.group(1).replace("~ ", "~"))
    extra = f" ({', '.join(bits)})" if bits else ""
    return (f"**{title}**{extra}. What it covers, the records and the traps: "
            f"[testing/{slug}.md](testing/{slug}.md)")


def split_rows(lines, threshold, used):
    """Yield (new_lines, pages): pages = {slug: (title, cmd, detail, needs)}."""
    out = []
    pages = {}
    in_table = False
    for line in lines:
        m = ROW_RE.match(line)
        if not m:
            in_table = False
            out.append(line)
            continue
        cells = [c.strip() for c in SPLIT_RE.split(m.group(1))]
        if cells and cells[0] == "Command":
            in_table = True
            out.append(line)
            continue
        if not in_table or len(cells) < 2 or set(cells[0]) <= set("-: "):
            if in_table and len(cells) != 3:
                print(f"  skipped ({len(cells)} cells): {line[:90]}")
            out.append(line)
            continue
        # an unescaped pipe inside the detail cell splits it: the first cell
        # is the command, the last the needs, the middle is the detail; a
        # row of two cells has no needs cell
        if len(cells) == 2:
            cmd, detail, needs = cells[0], cells[1], None
        else:
            cmd, detail, needs = cells[0], " | ".join(cells[1:-1]), cells[-1]
        if len(detail) <= threshold:
            out.append(line)
            continue
        slug = slug_for(cmd, used, title_for(detail))
        pages[slug] = (title_for(detail), cmd, detail, needs)
        # the index keeps the command alone (its first code span); the
        # options and the notes that followed it are on the page
        m = re.match(r"(`[^`]+`)", cmd)
        short_cmd = m.group(1) if m else cmd
        tail = f" | {needs} |" if needs is not None else " |"
        out.append(f"| {short_cmd} | {summary_for(detail, slug)}{tail}")
    return out, pages


def split_sections(lines, used):
    """Move the named sections to pages, leaving a two-line stub."""
    out = []
    pages = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.rstrip() in SECTIONS:
            slug = SECTIONS[line.rstrip()]
            used.add(slug)
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## "):
                j += 1
            body = lines[i + 1:j]
            # the stub: the heading, the first paragraph trimmed, the link
            first = []
            for b in body:
                if b.strip() == "" and first:
                    break
                if b.strip():
                    first.append(b.strip())
            stub = " ".join(first)
            if len(stub) > 320:
                stub = stub[:317].rsplit(" ", 1)[0] + "..."
            title = line.rstrip()[3:]
            pages[slug] = (title, None, "\n".join(body).strip("\n"), None)
            out.append(line)
            out.append("")
            out.append(stub)
            out.append("")
            out.append(f"The whole section: [testing/{slug}.md](testing/{slug}.md).")
            out.append("")
            i = j
            continue
        out.append(line)
        i += 1
    return out, pages


def page_text(title, cmd, detail, needs, is_section):
    head = [f"# {title}", ""]
    if is_section:
        head.append("Moved from `TESTING.md` on 2026-10-31 (the section, whole). The index "
                    "row and the other stages: [../TESTING.md](../TESTING.md).")
        head.append("")
        body = fix_links(detail)
        return "\n".join(head) + "\n" + body + "\n"
    head.append(f"Command: {cmd}")
    head.append("")
    if needs:
        head.append(f"Needs: {needs}")
        head.append("")
    head.append("Moved from the `TESTING.md` test-kinds table on 2026-10-31 (the row's "
                "detail cell, whole, as paragraphs). The index row and the other stages: "
                "[../TESTING.md](../TESTING.md).")
    head.append("")
    body = paragraphs(fix_links(unescape(detail)))
    return "\n".join(head) + "\n" + body + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--threshold", type=int, default=400)
    a = ap.parse_args()
    path = os.path.join(a.repo, "TESTING.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    before = len(text.encode("utf-8"))
    lines = text.split("\n")
    used = set()
    lines, row_pages = split_rows(lines, a.threshold, used)
    lines, sec_pages = split_sections(lines, used)
    new_text = "\n".join(lines)
    # the long dash rule: an en dash in the moved text or the index becomes a hyphen
    dashes = new_text.count("–") + sum(p[2].count("–") for p in list(row_pages.values()) + list(sec_pages.values()))
    new_text = new_text.replace("–", "-")
    after = len(new_text.encode("utf-8"))
    print(f"TESTING.md: {before} -> {after} bytes; rows moved {len(row_pages)}, "
          f"sections moved {len(sec_pages)}, en dashes replaced {dashes}")
    for slug, (title, cmd, detail, needs) in row_pages.items():
        print(f"  row  {slug:36s} {len(detail):6d} B  {title[:70]}")
    for slug, (title, cmd, detail, needs) in sec_pages.items():
        print(f"  sect {slug:36s} {len(detail):6d} B  {title[:70]}")
    if not a.apply:
        print("\n(dry run: --apply writes testing/*.md and TESTING.md)")
        return 0
    outdir = os.path.join(a.repo, "testing")
    os.makedirs(outdir, exist_ok=True)
    for slug, (title, cmd, detail, needs) in row_pages.items():
        with open(os.path.join(outdir, slug + ".md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(page_text(title, cmd, detail, needs, False).replace("–", "-"))
    for slug, (title, cmd, detail, needs) in sec_pages.items():
        with open(os.path.join(outdir, slug + ".md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(page_text(title, cmd, detail, needs, True).replace("–", "-"))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_text)
    print(f"written: {len(row_pages) + len(sec_pages)} pages under testing/, TESTING.md rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
