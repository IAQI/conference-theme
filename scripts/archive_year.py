#!/usr/bin/env python3
"""
Make (or refresh) the static copy of one conference year in its archive.

    python3 -I archive_year.py --venue qcrypt --year 2026 --source https://qcrypt.net/2026/ ../qcrypt.iaqi.org

The last argument is the archive repository (qcrypt.iaqi.org, tqc.iaqi.org or
qip.iaqi.org); run it on a fresh branch. Steps:

 1. mirror --source with wget (recipe below) into a temporary folder
 2. move the site's root-level files the year uses (css/, images/, favicons,
    manifest, ...) inside the year folder, and rewrite the links that pointed
    one level up to them, so the copy is self-contained under /<year>/
 3. turn absolute links to --source into relative ones
 4. remove Subresource-Integrity attributes (integrity=, crossorigin=): wget
    rewrites url(...) inside CSS, so the hashes no longer match and browsers
    would refuse the stylesheets
 5. replace <archive>/<year>/ with the result (refuses if it has uncommitted
    changes), remove Google Analytics snippets
 6. report links that still point to the live site and local links whose
    target is missing
 7. run archive_discovery.py (sitemap.xml, llms.txt, Event data) for the archive

Then check the copy locally (python3 -m http.server in the archive), update
the past-editions table on the archive's landing page, commit and open a PR.
The whole procedure is described in docs/website-guide.md, "Archiving a year".

--keep-mirror DIR keeps wget's raw download there (useful when debugging).
--from-mirror DIR skips wget and uses a raw download kept earlier.
"""
import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEXT = (".html", ".htm", ".css", ".xml", ".js", ".json", ".webmanifest", ".svg")

WGET = ["wget", "--mirror", "--convert-links", "--adjust-extension", "--page-requisites",
        "--no-parent", "--reject-regex", r".*(\?|#).*",
        "--exclude-directories=*/wp-json/*,*/xmlrpc.php*",
        "--wait=0.5", "--random-wait", "--compression=auto", "--no-verbose",
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"]

# Files at the live site's root that describe the live site, not the year.
SITE_FILES = {"robots.txt", "llms.txt", "sitemap.xml", "netlify.toml", "_redirects", "_headers", "404.html"}
# Root-level files that pages may reference by absolute URL and that the copy needs.
ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".css", ".js",
             ".json", ".webmanifest", ".woff", ".woff2", ".ttf", ".pdf")
UA = WGET[-1].split("=", 1)[1]

SRI = re.compile(rb"""\s+(integrity|crossorigin)(\s*=\s*("[^"]*"|'[^']*'|[^\s>]+))?(?=[\s/>])""", re.I)
TAG = re.compile(rb"<(link|script)\b[^>]*>", re.I)


def discovery():
    spec = importlib.util.spec_from_file_location("archive_discovery", HERE / "archive_discovery.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mirror(source, dest):
    print(f"mirroring {source} (this takes a few minutes) ...")
    r = subprocess.run(WGET + ["--directory-prefix", str(dest), source])
    # wget exits 8 when some page requisites 404; the mirror is still usable
    if r.returncode not in (0, 8):
        sys.exit(f"wget failed with exit code {r.returncode}")


def assemble(raw, source, year):
    """Return the year folder with the root-level assets moved inside it."""
    u = urllib.parse.urlsplit(source)
    host_dir = raw / u.netloc
    if not host_dir.is_dir():
        sys.exit(f"{host_dir} not found in the mirror")
    year_path = u.path.strip("/")
    year_dir = host_dir / year_path
    if not year_dir.is_dir():
        sys.exit(f"{year_dir} not found in the mirror")
    moved = []
    if year_path.count("/") == 0:  # site root holds the shared assets
        for entry in host_dir.iterdir():
            if entry == year_dir or re.fullmatch(r"\d{4}", entry.name):
                continue
            if entry.name.startswith(".") or entry.name in SITE_FILES:
                continue  # the live site's own files (.netlify, robots.txt, ...), not part of the year
            target = year_dir / entry.name
            if target.exists():
                print(f"warning: {entry.name} exists both at the site root and in {year_path}/; kept the year's copy")
                continue
            shutil.move(str(entry), str(target))
            moved.append(entry.name)
    return year_dir, moved


def fix_links(year_dir, moved, source, year):
    """Rewrite links after moving root assets one level down, and make --source links relative."""
    u = urllib.parse.urlsplit(source)
    src_re = re.compile(rb"https?://" + re.escape((u.netloc + "/" + u.path.strip("/") + "/").encode()))
    names = "|".join(re.escape(n) for n in moved)
    changed = 0
    for p in year_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT:
            continue
        depth = len(p.relative_to(year_dir).parts) - 1
        b = p.read_bytes()
        nb = b
        if moved:
            # "../" * (depth+1) + <moved root entry>  ->  "../" * depth + <entry>
            up = re.escape(b"../" * (depth + 1))
            nb = re.sub(rb"(?<=[\"'=(\s,])" + up + rb"(?=(" + names.encode() + rb")([/\"')\s?#,]|$))",
                        b"../" * depth, nb)
        # absolute links into the year itself -> relative
        nb = src_re.sub(b"../" * depth if depth else b"./", nb)
        if p.suffix.lower() in (".html", ".htm"):
            nb = TAG.sub(lambda m: SRI.sub(b"", m.group(0)), nb)
        if nb != b:
            p.write_bytes(nb)
            changed += 1
    return changed


def localize_root_assets(year_dir, source):
    """Download root-level files of the live site that pages load by absolute URL
    (icons wget does not treat as page requisites, e.g. apple-touch-icon.png) into
    the year folder and link them relatively. Links to the live llms.txt point to
    the archive's own /llms.txt instead."""
    u = urllib.parse.urlsplit(source)
    root_re = re.compile(rb"https?://" + re.escape(u.netloc.encode()) + rb"/([^\s\"'<>()#?]+)")
    fetched, failed = set(), set()

    def get(path):
        dest = year_dir / path
        if dest.exists() or path in fetched:
            return True
        if path in failed:
            return False
        req = urllib.request.Request(f"{u.scheme}://{u.netloc}/{path}", headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        except Exception as e:  # noqa: BLE001 - report and keep the absolute link
            print(f"warning: could not fetch /{path}: {e}")
            failed.add(path)
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        fetched.add(path)
        return True

    for p in year_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT:
            continue
        depth = len(p.relative_to(year_dir).parts) - 1

        def repl(m):
            path = urllib.parse.unquote(m.group(1).decode("utf-8", "replace"))
            if path == "llms.txt":
                return b"/llms.txt"
            if re.match(r"\d{4}/", path) or ".." in path or not path.lower().endswith(ASSET_EXT):
                return m.group(0)  # other years, pages of the live site: leave them
            return (b"../" * depth + m.group(1)) if get(path) else m.group(0)

        b = p.read_bytes()
        nb = root_re.sub(repl, b)
        if nb != b:
            p.write_bytes(nb)
    if fetched:
        print("downloaded root files the pages link to: " + ", ".join(sorted(fetched)))


def check(repo, year, source):
    """Report links to the live site and local links whose target is missing."""
    ydir = repo / str(year)
    live = urllib.parse.urlsplit(source).netloc.encode()
    live_hits, missing = {}, {}
    attr = re.compile(rb"""(?:href|src)\s*=\s*["']?([^"'\s>]+)""", re.I)
    for p in ydir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in (".html", ".htm"):
            continue
        b = p.read_bytes()
        for m in attr.finditer(b):
            link = m.group(1).decode("utf-8", "replace")
            if live in m.group(1):
                live_hits[link] = live_hits.get(link, 0) + 1
                continue
            if re.match(r"^([a-z]+:|//|#)", link, re.I):
                continue
            path = urllib.parse.unquote(link.split("#")[0].split("?")[0])
            if not path:
                continue
            # "/llms.txt" is relative to the archive's root, "x.html" to the page
            target = (repo / path.lstrip("/")) if path.startswith("/") else (p.parent / path)
            if path.endswith("/"):
                target = target / "index.html"
            if not target.exists():
                missing[link] = missing.get(link, 0) + 1
    print(f"{len(live_hits)} distinct links still point to {live.decode()}"
          + (" (fine for pages that stay on the live site, e.g. the current year):" if live_hits else ""))
    for l, n in sorted(live_hits.items(), key=lambda x: -x[1])[:15]:
        print(f"  {n:4d}  {l}")
    print(f"{len(missing)} distinct local links have no file in the archive:")
    for l, n in sorted(missing.items(), key=lambda x: -x[1])[:15]:
        print(f"  {n:4d}  {l}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", required=True, choices=["qip", "qcrypt", "tqc"])
    ap.add_argument("--year", required=True, type=int)
    ap.add_argument("--source", required=True, help="the live site of that year, e.g. https://qcrypt.net/2026/")
    ap.add_argument("--keep-mirror", type=Path, help="keep wget's raw download in this folder")
    ap.add_argument("--from-mirror", type=Path, help="use a raw download kept earlier instead of running wget")
    ap.add_argument("--from-json", help="saved copy of QuantumDB's /api/v1/export/all.json")
    ap.add_argument("repo", type=Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    if not (repo / ".git").exists():
        sys.exit(f"{repo} is not a git checkout")
    year_dir = repo / str(a.year)
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", str(a.year)],
                           capture_output=True, text=True).stdout
    if dirty.strip():
        sys.exit(f"{a.year}/ has uncommitted changes; commit or stash them first")

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw"
        if a.from_mirror:
            shutil.copytree(a.from_mirror, raw, symlinks=True)
        else:
            mirror(a.source, raw)
            if a.keep_mirror:
                shutil.copytree(raw, a.keep_mirror, symlinks=True, dirs_exist_ok=True)
        staged, moved = assemble(raw, a.source, a.year)
        if moved:
            print("moved into the year folder: " + ", ".join(sorted(moved)))
        n = fix_links(staged, moved, a.source, a.year)
        print(f"rewrote links in {n} files")
        localize_root_assets(staged, a.source)
        if year_dir.exists():
            shutil.rmtree(year_dir)
        shutil.copytree(staged, year_dir, symlinks=True)

    d = discovery()
    stripped = 0
    for p in year_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".html", ".htm"):
            b = p.read_bytes()
            nb, _ = d.strip_analytics(b)
            if nb != b:
                p.write_bytes(nb)
                stripped += 1
    print(f"removed Google Analytics from {stripped} files")

    check(repo, a.year, a.source)

    print("running archive_discovery.py ...")
    args = [sys.executable, "-I", str(HERE / "archive_discovery.py"), "--venue", a.venue]
    if a.from_json:
        args += ["--from-json", a.from_json]
    args.append(str(repo))
    subprocess.run(args, check=True)
    print(f"\nnext: check {a.year}/ locally (cd {repo} && python3 -m http.server), update the "
          f"past-editions table on the landing page, commit, open a PR.")


if __name__ == "__main__":
    main()
