#!/usr/bin/env python3
"""
Start a new conference year from the previous one.

    python3 new_year.py --year 2028 [--from-year 2027] [--site .] [--check]

Copies, from content/<from-year>/ to content/<year>/, the pages that every
year has: the home page, the top-level pages (charter, history, code of
conduct, ...), the pages in plain folders without an _index.md (attend/,
technical/: call, registration, ...) and the _index.md of each section
(schedule, sessions, speakers, team, partners, ...). The pages inside sections
(individual talks, speakers, committee members and partners) are NOT copied, so
nothing of the previous conference shows up by accident. All copied pages get
`year: <year>`, their menu entries move to the new year, and they are set to
`draft: true`: publish each one once it has been updated.

In the copied pages, shortcode parameters (`year=<from-year>`), e-mail
addresses (`<from-year>@...`, `pcchairs<from-year>@...`) and links to pages
(`/<from-year>/...`) move to the new year. Links to files that exist in
static/ (images, PDFs) keep pointing to the previous year until replaced.
Other mentions of the previous year (dates, "QCrypt <from-year>") are left
for you to update.

Also:
  * hugo.toml: copies [params.<from-year>] (city, colours, logos) and the
    [[menu.<from-year>]] entries to the new year, next to the previous year's
    blocks. It does not change currentYear or the "/" redirect; do that when
    the new site goes live.
  * assets/style/theme-<year>.scss: copy of the previous year's (colours!)
  * data/accepted-papers-<year>.json and posters-<year>.json: empty lists

It prints a checklist of what to adapt by hand. With --check nothing is written.
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote


def latest_year(site):
    years = sorted(int(p.name) for p in (site / "content").iterdir() if p.is_dir() and p.name.isdigit())
    if not years:
        raise SystemExit("no content/<year>/ folders found")
    return str(years[-1])


def move_links(text, old, new, static=None):
    """/<old>/... -> /<new>/..., except links to files that exist in static/."""
    def link(m):
        url = m.group(0)
        if static is not None and (static / unquote(url.lstrip("/"))).is_file():
            return url  # an existing image or PDF of the previous year
        return url.replace(f"/{old}/", f"/{new}/")
    return re.sub(rf"(?:/[^\s\"'()<>\[\]]*)?/{old}/[^\s\"'()<>\[\]]*", link, text)


def adapt_body(body, old, new, static=None):
    body = re.sub(rf"\byear\s*=\s*([\"']?){old}\1", rf"year=\g<1>{new}\g<1>", body)  # shortcodes
    body = re.sub(rf"([\w.+-]*){old}([\w.+-]*@)", rf"\g<1>{new}\g<2>", body)            # e-mails
    return move_links(body, old, new, static)


def copied(rel, src):
    """Top-level pages, every _index.md, and pages that are not inside a section
    (no folder above them, within the year, has an _index.md)."""
    if len(rel.parts) == 1 or rel.name == "_index.md":
        return True
    return not any((src / Path(*rel.parts[:i]) / "_index.md").exists() for i in range(1, len(rel.parts)))


def adapt_page(text, old, new, static=None):
    m = re.match(r"(---\s*\n)(.*?)(\n---\s*\n?)(.*)", text, re.S)
    if not m:
        return text
    front, body = m.group(2), m.group(4)
    front = re.sub(rf"^year:\s*{old}\s*$", f"year: {new}", front, flags=re.M)
    if not re.search(r"^year:", front, re.M):
        front += f"\nyear: {new}"
    front = re.sub(rf"^(\s+){old}:\s*$", rf"\g<1>{new}:", front, flags=re.M)   # menu: <year>:
    front = move_links(front, old, new)  # aliases: two pages must not share one
    front = re.sub(r"^draft:.*$", "draft: true", front, flags=re.M)
    if not re.search(r"^draft:", front, re.M):
        front += "\ndraft: true"
    body = adapt_body(body, old, new, static)
    return m.group(1) + front + m.group(3) + body


def copy_config_blocks(text, old, new, static=None):
    """Copy the [params.<old>...] and [[menu.<old>]] blocks to <new>. The copies
    go right after the last block of the same kind (params or menus)."""
    lines = text.split("\n")
    blocks = []  # (kind, start, end)
    for i, line in enumerate(lines):
        h = re.match(r"^\[\[?([^\]]+)\]\]?$", line.strip())
        if not h:
            continue
        if blocks and blocks[-1][2] is None:
            blocks[-1][2] = i
        name = h.group(1).split(".")
        if len(name) > 1 and name[0] in ("params", "menu", "menus") and name[1] == old:
            blocks.append([("params" if name[0] == "params" else "menu"), i, None])
    if not blocks:
        return text, 0
    if blocks[-1][2] is None:
        blocks[-1][2] = len(lines)

    inserts = {}
    for kind in ("params", "menu"):
        mine = [b for b in blocks if b[0] == kind]
        if not mine:
            continue
        copy = []
        for _, start, end in mine:
            for line in lines[start:end]:
                line = re.sub(rf"(\[\[?(?:params|menu|menus)\.){old}\b", rf"\g<1>{new}", line)
                line = move_links(line, old, new, static)
                line = line.replace(f"'/{old}'", f"'/{new}'").replace(f'"/{old}"', f'"/{new}"')
                copy.append(line)
        while copy and not copy[-1].strip():
            copy.pop()
        end = mine[-1][2]
        while end > mine[-1][1] + 1 and not lines[end - 1].strip():
            end -= 1  # insert before the blank lines that end the old block
        inserts[end] = [""] + copy
    for at in sorted(inserts, reverse=True):
        lines[at:at] = inserts[at]
    return "\n".join(lines), len(blocks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", required=True)
    parser.add_argument("--from-year")
    parser.add_argument("--site", default=".")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    site, new = Path(args.site), str(args.year)
    old = args.from_year or latest_year(site)
    src, dst = site / "content" / old, site / "content" / new
    if dst.exists():
        raise SystemExit(f"{dst} already exists")
    if not src.exists():
        raise SystemExit(f"{src} not found")

    static = site / "static"
    writes = []
    for page in sorted(src.rglob("*.md")):
        rel = page.relative_to(src)
        if copied(rel, src):
            writes.append((dst / rel, adapt_page(page.read_text(encoding="utf-8"), old, new, static)))

    cfg = site / "hugo.toml"
    cfg_text, n_blocks = copy_config_blocks(cfg.read_text(encoding="utf-8"), old, new, static)
    writes.append((cfg, cfg_text))
    style_old = site / "assets" / "style" / f"theme-{old}.scss"
    if style_old.exists():
        writes.append((site / "assets" / "style" / f"theme-{new}.scss",
                       style_old.read_text(encoding="utf-8").replace(old, new)))
    for name in (f"accepted-papers-{new}.json", f"posters-{new}.json"):
        if not (site / "data" / name).exists():
            writes.append((site / "data" / name, "[]\n"))

    for path, text in writes:
        print(("would write " if args.check else "wrote ") + str(path.relative_to(site)))
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    print(f"""
{len(writes) - 1} files from {old}; {n_blocks} hugo.toml blocks copied to {new}. Still to do by hand:
  - hugo.toml [params.{new}]: city, timeanddate_cityid, themeColor, logos
  - static/images/{new}/: logos and banner
  - assets/style/theme-{new}.scss: colours (--primary, --primary-txt, ...)
  - content/{new}/: update each page, then set draft: false
  - when the {new} site goes live: params.currentYear and the "/" redirect in netlify.toml
  - later: accepted papers, schedule, presenters and videos (README, steps 1-4)""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
