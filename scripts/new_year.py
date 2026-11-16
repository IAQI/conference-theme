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

The new year starts with the IAQI stand-in images of the shared theme
(/images/placeholder/: background photo and logo on the home page, header logo
and banner in hugo.toml) and IAQI maroon as its main colour, so that nothing
of the previous year's design shows up. Each stand-in says what to deliver in
its place; see https://github.com/IAQI/iaqi-brand#stand-ins-for-conference-websites

Also:
  * hugo.toml: copies [params.<from-year>] (city, colours, logos) and the
    [[menu.<from-year>]] entries to the new year, next to the previous year's
    blocks; logos and themeColor become the stand-ins. It does not change
    currentYear or the "/" redirect; do that when the new site goes live.
  * assets/style/theme-<year>.scss: copy of the previous year's, with --primary
    set to the stand-in colour
  * data/accepted-papers-<year>.json and posters-<year>.json: empty lists

It prints a checklist of what to adapt by hand. With --check nothing is written.
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote


STAND_IN = "/images/placeholder/"
STAND_IN_COLOUR = "#660038"  # IAQI maroon
STAND_IN_LOGOS = {"header": "header-logo.svg", "banner": "banner.svg", "jumbo": "background.svg"}
PAGE_SUFFIXES = (".md", ".html")


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


def stand_ins(body):
    """Home page: the stand-ins in the jumbo shortcode, and no venue picture."""
    def jumbo(m):
        s = re.sub(r'\bimg\s*=\s*"[^"]*"', f'img="{STAND_IN}background.svg"', m.group(0))
        return re.sub(r'\blogo\s*=\s*"[^"]*"', f'logo="{STAND_IN}logo.svg"', s)
    body = re.sub(r"\{\{[%<]\s*jumbo\b.*?[%>]\}\}", jumbo, body, flags=re.S)

    def location(m):
        return re.sub(r'\s*\bimage\s*=\s*"[^"]*"', "", m.group(0))
    return re.sub(r"\{\{[%<]\s*home-location\b.*?[%>]\}\}", location, body, flags=re.S)


def adapt_body(body, old, new, static=None):
    body = re.sub(rf"\byear\s*=\s*([\"']?){old}\1", rf"year=\g<1>{new}\g<1>", body)  # shortcodes
    body = re.sub(rf"([\w.+-]*){old}([\w.+-]*@)", rf"\g<1>{new}\g<2>", body)            # e-mails
    return move_links(stand_ins(body), old, new, static)


def stand_in_colour(scss, new):
    """theme-<new>.scss: the first --primary becomes the stand-in colour."""
    return re.sub(r"(--primary\s*:\s*)[^;]*;[^\n]*",
                  rf"\g<1>{STAND_IN_COLOUR}; // stand-in (IAQI maroon). TODO: the {new} colour, also themeColor in hugo.toml",
                  scss, count=1)


def copied(rel, src):
    """Top-level pages, every _index page, and pages that are not inside a section
    (no folder above them, within the year, has an _index page)."""
    if len(rel.parts) == 1 or rel.stem == "_index":
        return True
    return not any((src / Path(*rel.parts[:i]) / f"_index{s}").exists()
                   for i in range(1, len(rel.parts)) for s in PAGE_SUFFIXES)


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
            logos = lines[start].strip() == f"[params.{old}.logos]"
            for line in lines[start:end]:
                key = re.match(r"^(\s*)([\w-]+)(\s*=\s*)", line)
                if key and logos and key.group(2) in STAND_IN_LOGOS:
                    line = (f'{key.group(1)}{key.group(2)}{key.group(3)}'
                            f'"{STAND_IN}{STAND_IN_LOGOS[key.group(2)]}" # TODO: the {new} image')
                elif key and key.group(2) == "themeColor":
                    line = (f'{key.group(1)}themeColor{key.group(3)}"{STAND_IN_COLOUR}"'
                            f' # stand-in (IAQI maroon); TODO: same as --primary in theme-{new}.scss')
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
    for page in sorted(src.rglob("*")):
        rel = page.relative_to(src)
        if page.suffix in PAGE_SUFFIXES and page.is_file() and copied(rel, src):
            writes.append((dst / rel, adapt_page(page.read_text(encoding="utf-8"), old, new, static)))

    cfg = site / "hugo.toml"
    cfg_text, n_blocks = copy_config_blocks(cfg.read_text(encoding="utf-8"), old, new, static)
    writes.append((cfg, cfg_text))
    style_old = site / "assets" / "style" / f"theme-{old}.scss"
    if style_old.exists():
        writes.append((site / "assets" / "style" / f"theme-{new}.scss",
                       stand_in_colour(style_old.read_text(encoding="utf-8").replace(old, new), new)))
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
  - hugo.toml [params.{new}]: city, timeanddate_cityid
  - images: replace the stand-ins ({STAND_IN}...) with files in static/images/{new}/:
    the jumbo on the home page (img=, logo=) and [params.{new}.logos]; formats in
    https://github.com/IAQI/iaqi-brand#stand-ins-for-conference-websites
  - colours: --primary in assets/style/theme-{new}.scss and themeColor in hugo.toml
    (now the stand-in IAQI maroon), --primary-txt, ...
  - content/{new}/: update each page, then set draft: false
  - when the {new} site goes live: params.currentYear and the "/" redirect in netlify.toml
  - later: accepted papers, schedule, presenters and videos (README, steps 1-4)""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
