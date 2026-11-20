#!/usr/bin/env python3
"""
Make a static conference archive (qip.iaqi.org, tqc.iaqi.org, qcrypt.iaqi.org)
easy to find and read for search engines, LLMs and agents.

    python3 -I archive_discovery.py --venue qcrypt ../qcrypt.iaqi.org
    python3 -I archive_discovery.py --venue qcrypt --strip-analytics [--year 2026] ../qcrypt.iaqi.org

Run it from anywhere; the last argument is the archive repository (a git
checkout whose top-level folders are the conference years). It writes, at the
root of the archive:

  robots.txt   every crawler, AI crawlers included, is welcome; points to the sitemap
  sitemap.xml  every archived HTML page and PDF (from `git ls-files`), with the
               date of the last real change as <lastmod>
  llms.txt     a Markdown map of the archive for LLMs and agents: one section per
               year with dates, place and its key pages (program, accepted papers,
               committees, ...), and links to the structured data in QuantumDB

and it adds to the HTML

  <year>/index.html   a schema.org Event (JSON-LD) with name, dates, place and
                      links, between <!-- iaqi-archive:event --> markers
  index.html          <link rel="alternate" type="text/markdown" href="/llms.txt">
  landing page        a visible "Data (QuantumDB)" link next to the other links
                      (the page with the past-editions table)

Dates, places and links come from QuantumDB (https://quantumdb.iaqi.org, CC0):
/api/v1/export/all.json, or --from-json FILE for a saved copy. A year folder
QuantumDB does not know (e.g. qip.iaqi.org/1900) is left alone.

--strip-analytics removes old Google Analytics snippets (gtag.js, analytics.js,
ga.js/_gaq, urchin, Tag Manager) and Netlify's injected Real User Monitoring
script from the archived HTML: archived pages should not report visits to
anyone. Only elements that load or configure analytics and nothing else are
removed; scripts that merely mention it among
other things (Drupal settings, Cloudflare loader) are kept and listed.
--year limits this to one year folder.

The script is idempotent: running it twice changes nothing the second time.
Commit its output with a subject starting "archive-tools:" so that those
commits do not count as content changes for <lastmod>.
"""
import argparse
import datetime
import html
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://quantumdb.iaqi.org/api/v1/export/all.json"
QUANTUMDB = "https://quantumdb.iaqi.org"

VENUES = {
    "qip": {
        "db": "QIP",
        "name": "QIP",
        "full": "Quantum Information Processing (QIP)",
        "host": "qip.iaqi.org",
        "current": "https://qipconference.org/",
    },
    "qcrypt": {
        "db": "QCRYPT",
        "name": "QCrypt",
        "full": "International Conference on Quantum Cryptography (QCrypt)",
        "host": "qcrypt.iaqi.org",
        "current": "https://qcrypt.net/",
    },
    "tqc": {
        "db": "TQC",
        "name": "TQC",
        "full": "Conference on the Theory of Quantum Computation, Communication and Cryptography (TQC)",
        "host": "tqc.iaqi.org",
        "current": "https://tqc-conference.org/",
    },
}

# Folders inside a year that are crawler noise rather than content (wget/WordPress leftovers).
NOISE = re.compile(r"(^|/)(feed|comments|wp-json|wp-content|wp-includes|author|tag|xmlrpc\.php|cdn-cgi|static|css|js|fonts?|images?|img)(/|$)")
# Folders of detail pages (one talk, one news post): never a "key page" of the year.
DETAIL = re.compile(r"(^|/)(sessions?|announcements?|news|posts?|blog|category|people|speakers?|talks?|papers?)/")
KEY_PAGES = ["program", "schedule", "accepted", "papers", "talks", "posters",
             "invited", "speakers", "committee", "proceedings"]
MAX_KEY_PAGES = 8
TOOL_SUBJECT = "archive-tools:"

EVENT_START = b"<!-- iaqi-archive:event -->"
EVENT_END = b"<!-- /iaqi-archive:event -->"
ALT_MARK = b"<!-- iaqi-archive:llms -->"
DATA_MARK = b"<!-- iaqi-archive:data-link -->"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=off", *args],
                          check=True, capture_output=True).stdout


def decode(b):
    # old archives contain file names that are not UTF-8; keep their bytes intact
    return b.decode("utf-8", "surrogateescape")


def tracked(repo):
    return [decode(p) for p in git(repo, "ls-files", "-z").split(b"\0") if p]


def last_changes(repo):
    """path -> YYYY-MM-DD of the newest commit touching it, ignoring archive-tools commits."""
    out = decode(git(repo, "log", "--format=%x01%cs%x02%s", "--name-only", "-z"))
    seen = {}
    for chunk in out.split("\x01")[1:]:
        head, _, files = chunk.partition("\x02")
        subject, _, files = files.partition("\n")
        if subject.startswith(TOOL_SUBJECT):
            continue
        for f in files.split("\0"):
            f = f.strip("\n")
            if f and f not in seen:
                seen[f] = head
    return seen


def year_of(path):
    m = re.match(r"(\d{4})/", path)
    return int(m.group(1)) if m else None


def url_for(host, path):
    if path == "index.html":
        path = ""
    elif path.endswith("/index.html"):
        path = path[: -len("index.html")]
    raw = path.encode("utf-8", "surrogateescape")
    return f"https://{host}/" + urllib.parse.quote(raw, safe="/@&=+,;:!$'()*~")


def page_title(file):
    try:
        b = file.read_bytes()[:20000]
    except OSError:
        return ""
    m = re.search(rb"<title[^>]*>(.*?)</title>", b, re.S | re.I)
    if not m:
        return ""
    t = m.group(1).decode("utf-8", "replace")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t))).strip()


# ---------------------------------------------------------------- QuantumDB

def load_conferences(venue, from_json):
    if from_json:
        data = json.loads(Path(from_json).read_text())
    else:
        req = urllib.request.Request(API, headers={"User-Agent": "iaqi-archive-tools"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    return {c["year"]: c for c in data["conferences"] if c["venue"] == VENUES[venue]["db"]}


def place(c):
    return ", ".join(x for x in (c.get("city"), c.get("country")) if x)


def dates(c):
    s, e = c.get("start_date"), c.get("end_date")
    if not s:
        return ""
    if not e or e == s:
        return s
    return f"{s} to {e}"


# ---------------------------------------------------------------- generated files

def sitemap(repo, host, files):
    changed = last_changes(repo)
    urls = {}
    for f in files:
        if not f.lower().endswith((".html", ".htm", ".pdf")):
            continue
        if "/" in f and not re.match(r"\d{4}/", f):
            continue  # only root pages and year folders; not backups, tools or scratch folders
        if re.fullmatch(r"google[0-9a-f]+\.html", f):
            continue  # Search Console verification file
        if not f.lower().endswith(".pdf") and NOISE.search(f.split("/", 1)[1] if "/" in f else ""):
            continue  # PDFs (abstracts, slides) are content wherever wget stored them
        u = url_for(host, f)
        # wget stores both x.html and x/index.html; keep one URL per page
        alt = f[:-5] + "/index.html" if f.endswith(".html") and not f.endswith("index.html") else None
        if alt and alt in files:
            continue
        urls[u] = changed.get(f, "")
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in sorted(urls):
        lm = f"<lastmod>{urls[u]}</lastmod>" if urls[u] else ""
        out.append(f"  <url><loc>{html.escape(u, quote=False)}</loc>{lm}</url>")
    out.append("</urlset>\n")
    return "\n".join(out), len(urls)


def robots(host):
    return (f"# Static archive of past conference websites, maintained by IAQI.\n"
            f"# Crawlers and AI agents are welcome. A map for LLMs: https://{host}/llms.txt\n"
            f"User-agent: *\nAllow: /\n\nSitemap: https://{host}/sitemap.xml\n")


def key_pages(repo, host, year, files):
    cands = []
    for f in files:
        if not f.startswith(f"{year}/") or not f.endswith(".html"):
            continue
        rel = f[len(f"{year}/"):]
        if NOISE.search(rel) or DETAIL.search(rel) or rel.count("/") > 2:
            continue
        stem = rel[: -len("/index.html")] if rel.endswith("/index.html") else rel[:-5]
        last = stem.rsplit("/", 1)[-1].lower()  # the page's own name, not its folder
        hit = next((i for i, k in enumerate(KEY_PAGES) if k in last), None)
        if hit is None or re.search(r"\d{3,}", last):
            continue  # skip numbered detail pages (sessions/123, 2023 cross-links)
        cands.append((hit, stem.count("/"), len(stem), stem, f))
    seen_stems, seen_keys, pages = set(), set(), []
    for hit, _, _, stem, f in sorted(cands):
        if stem in seen_stems or hit in seen_keys:
            continue  # one page per kind (one program, one committee page, ...)
        seen_stems.add(stem)
        seen_keys.add(hit)
        text = label(page_title(repo / f), stem)
        if any(text == t for t, _ in pages):
            text = label("", stem)  # two pages titled "Programme": name them by their path
        pages.append((text, url_for(host, f)))
        if len(pages) >= MAX_KEY_PAGES:
            break
    return pages


def label(title, stem):
    """The informative part of a page title ("QCrypt 2024 | Accepted Papers" -> "Accepted Papers")."""
    parts = [p.strip(" '\"") for p in re.split(r"\s+[|–—:-]\s+|\s+[|–—]|[|–—]\s+", title) if p.strip()]
    keyed = [p for p in parts if any(k in p.lower() for k in KEY_PAGES)]
    if keyed:
        text = keyed[0]
    else:
        text = stem.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").replace(".php", "")
        text = text[:1].upper() + text[1:]
    return text[:80]


def llms_txt(repo, venue, confs, years, files):
    v = VENUES[venue]
    host = v["host"]
    archived = [y for y in sorted(years, reverse=True) if y in confs]
    missing = [y for y in sorted(confs, reverse=True) if y not in years]
    out = [f"# {v['name']} archive", "",
           f"> Static copies of the websites of past {v['full']} conferences, "
           f"{min(archived)}–{max(archived)}, preserved by the International Association for "
           f"Quantum Information (IAQI). Pages are archived as they were published; "
           f"programs, accepted papers, abstracts and slides are mostly in HTML and PDF.",
           "",
           f"The current conference is at {v['current']}. For structured, machine-readable data "
           f"(talks, posters, authors with ORCID, committees, videos) use QuantumDB, "
           f"which covers the same conferences: {QUANTUMDB}/llms.txt (data under CC0 1.0).",
           "",
           "## Structured data (recommended for agents)", "",
           f"- [All conferences, with links to every per-year export]({QUANTUMDB}/api/v1/export/all.json)",
           f"- One conference as JSON: `{QUANTUMDB}/api/v1/export/conferences/{venue}-<year>`; "
           f"CSV: append `/talks.csv`, `/posters.csv` or `/committees.csv`",
           f"- [QuantumDB guide for LLMs]({QUANTUMDB}/llms.txt)",
           "",
           "## Archived editions", ""]
    for y in archived:
        c = confs[y]
        bits = [b for b in (dates(c), place(c)) if b]
        if c.get("is_virtual"):
            bits.append("online")
        elif c.get("is_hybrid"):
            bits.append("hybrid")
        out.append(f"### {c.get('name') or v['name'] + ' ' + str(y)}")
        out.append("")
        if bits:
            out.append("; ".join(bits))
            out.append("")
        out.append(f"- [Archived website]({url_for(host, f'{y}/index.html')})")
        for title, u in key_pages(repo, host, y, files):
            out.append(f"- [{title}]({u})")
        links = c.get("links") or {}
        if links.get("json"):
            out.append(f"- Data: [JSON]({links['json']}), [talks CSV]({links.get('talks_csv')}), "
                       f"[posters CSV]({links.get('posters_csv')}), [committees CSV]({links.get('committees_csv')}), "
                       f"[QuantumDB page]({links.get('page')})")
        out.append("")
    if missing:
        out += ["## Editions without an archived copy here", ""]
        for y in missing:
            c = confs[y]
            site = c.get("website_url")
            page = (c.get("links") or {}).get("page")
            parts = [p for p in (f"[original website]({site})" if site else "",
                                 f"[QuantumDB]({page})" if page else "") if p]
            out.append(f"- {c.get('name') or y}: " + ", ".join(parts))
        out.append("")
    out += ["## Rights", "",
            "Facts about the conferences (titles, authors, dates) are free to use; QuantumDB "
            "dedicates its data to the public domain (CC0 1.0). Abstracts, slides, photos and "
            "recordings remain the property of their authors.", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- HTML edits

def event_jsonld(venue, c, host):
    v = VENUES[venue]
    ev = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": c.get("name") or f"{v['name']} {c['year']}",
        "description": f"{v['full']} {c['year']}"
                       + (f", {place(c)}" if place(c) else "")
                       + ". Archived conference website maintained by IAQI.",
        "url": url_for(host, f"{c['year']}/index.html"),
        "eventStatus": "https://schema.org/EventScheduled",
        "organizer": {"@type": "Organization", "name": "International Association for Quantum Information (IAQI)",
                      "url": "https://iaqi.org/"},
    }
    if c.get("start_date"):
        ev["startDate"] = c["start_date"]
    if c.get("end_date"):
        ev["endDate"] = c["end_date"]
    physical = None
    if c.get("city") or c.get("country"):
        addr = {"@type": "PostalAddress"}
        if c.get("city"):
            addr["addressLocality"] = c["city"]
        if c.get("country"):
            addr["addressCountry"] = c["country"]
        physical = {"@type": "Place", "name": place(c), "address": addr}
    online = {"@type": "VirtualLocation", "url": ev["url"]}
    if c.get("is_virtual"):
        ev["eventAttendanceMode"] = "https://schema.org/OnlineEventAttendanceMode"
        ev["location"] = online
    elif c.get("is_hybrid") and physical:
        ev["eventAttendanceMode"] = "https://schema.org/MixedEventAttendanceMode"
        ev["location"] = [physical, online]
    elif physical:
        ev["eventAttendanceMode"] = "https://schema.org/OfflineEventAttendanceMode"
        ev["location"] = physical
    same = [x for x in ((c.get("links") or {}).get("page"), c.get("website_url"))
            if x and host not in x]
    if same:
        ev["sameAs"] = same
    # ensure_ascii: the block is plain ASCII, safe whatever the page's charset
    body = json.dumps(ev, ensure_ascii=True, indent=1).replace("</", "<\\/")
    return (EVENT_START + b'\n<script type="application/ld+json">\n' + body.encode()
            + b"\n</script>\n" + EVENT_END)


def put_block(b, block, start, end, before=rb"</head\s*>|<body\b"):
    """Insert or replace start..end in bytes b, before the first match of `before`."""
    if start in b and end in b:
        i, j = b.index(start), b.index(end) + len(end)
        return b[:i] + block + b[j:]
    m = re.search(before, b, re.I)
    if not m:
        return None
    return b[:m.start()] + block + b"\n" + b[m.start():]


def write_if_changed(path, data):
    data = data.encode() if isinstance(data, str) else data
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


# ---------------------------------------------------------------- analytics

SCRIPT = re.compile(rb"<script\b([^>]*)>(.*?)</script\s*>[ \t]*\n?", re.S | re.I)
# Loader scripts of Google Analytics, and Netlify's Real User Monitoring, which
# Netlify injects into every page it serves (so it ends up in wget copies).
GA_SRC = re.compile(rb"""src\s*=\s*["']?[^"'\s>]*(googletagmanager\.com/gtag/js|google-analytics\.com/(analytics|ga|urchin)\.js|\.netlify/scripts/rum)""", re.I)
GA_BODY = re.compile(rb"_gaq|gtag\(|GoogleAnalyticsObject|_gat\._getTracker|gaJsHost|urchinTracker|"
                     rb"__CF\.AJS\s*=\s*\{\s*\"ga_key\"|window\.ga\s*=\s*window\.ga\s*\|\||ga\(\s*['\"]create")
NOT_ONLY_GA = re.compile(rb"Drupal|CloudFlare\s*=\s*\[|jQuery|@context|addEventListener\(\s*['\"]DOMContentLoaded", re.I)
GA_LINK = re.compile(rb"<link\b[^>]*(dns-prefetch|preconnect)[^>]*(google-analytics\.com|googletagmanager\.com)[^>]*>[ \t]*\n?", re.I)
# Tag Manager's no-JavaScript fallback: <noscript><iframe src=".../googletagmanager.com/ns.html..."></iframe></noscript>
GTM_NOSCRIPT = re.compile(rb"<noscript\b[^>]*>\s*<iframe\b[^>]*googletagmanager\.com/ns\.html[^>]*>\s*</iframe>\s*</noscript>", re.I)


def strip_analytics(b):
    kept = []

    def repl(m):
        attrs, body = m.group(1), m.group(2)
        if GA_SRC.search(attrs) and not body.strip():
            return b""
        if GA_BODY.search(body):
            if NOT_ONLY_GA.search(body) or len(body) > 2000:
                kept.append(re.sub(rb"\s+", b" ", body)[:100])
                return m.group(0)
            return b""
        return m.group(0)

    return GTM_NOSCRIPT.sub(b"", GA_LINK.sub(b"", SCRIPT.sub(repl, b))), kept


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venue", required=True, choices=sorted(VENUES))
    ap.add_argument("--from-json", help="saved copy of QuantumDB's /api/v1/export/all.json")
    ap.add_argument("--strip-analytics", action="store_true", help="only remove Google Analytics snippets")
    ap.add_argument("--year", type=int, help="with --strip-analytics: only this year folder")
    ap.add_argument("repo", type=Path, help="archive repository (git checkout)")
    a = ap.parse_args()
    repo = a.repo.resolve()
    if not (repo / ".git").exists():
        sys.exit(f"{repo} is not a git checkout")
    files = tracked(repo)
    v = VENUES[a.venue]

    if a.strip_analytics:
        prefix = f"{a.year}/" if a.year else ""
        n, kept_all = 0, 0
        for f in files:
            if not f.startswith(prefix) or not f.lower().endswith((".html", ".htm")):
                continue
            p = repo / f
            b = p.read_bytes()
            nb, kept = strip_analytics(b)
            for k in kept:
                kept_all += 1
                print(f"kept (not only analytics): {f}: {k.decode('utf-8', 'replace')}")
            if nb != b:
                p.write_bytes(nb)
                n += 1
        print(f"analytics removed from {n} files; {kept_all} mixed scripts kept")
        return

    confs = load_conferences(a.venue, a.from_json)
    years = {int(d.name) for d in repo.iterdir() if d.is_dir() and re.fullmatch(r"\d{4}", d.name)}
    years &= set(confs)
    host = v["host"]
    changed = []

    xml, n = sitemap(repo, host, set(files))
    if write_if_changed(repo / "sitemap.xml", xml):
        changed.append("sitemap.xml")
    if write_if_changed(repo / "robots.txt", robots(host)):
        changed.append("robots.txt")
    if write_if_changed(repo / "llms.txt", llms_txt(repo, a.venue, confs, years, files)):
        changed.append("llms.txt")

    for y in sorted(years):
        p = repo / str(y) / "index.html"
        if not p.exists():
            print(f"warning: {y}/index.html missing, no Event data added")
            continue
        b = p.read_bytes()
        nb = put_block(b, event_jsonld(a.venue, confs[y], host), EVENT_START, EVENT_END)
        if nb is None:
            print(f"warning: {y}/index.html has neither </head> nor <body>, no Event data added")
        elif nb != b:
            p.write_bytes(nb)
            changed.append(f"{y}/index.html")

    root = repo / "index.html"
    if root.exists():
        b = root.read_bytes()
        alt = ALT_MARK + b'<link rel="alternate" type="text/markdown" title="Guide for LLMs" href="/llms.txt">'
        if ALT_MARK not in b:
            nb = put_block(b, alt, ALT_MARK, b'href="/llms.txt">')
            if nb and nb != b:
                root.write_bytes(nb)
                changed.append("index.html")
    # The visible link goes into the row of links above the past-editions table
    # (<p class="qc-links">) or, where there is none, at the end of its intro text.
    landing = [repo / n for n in ("index.html", "previousqips.html") if (repo / n).exists()]
    href = f"{QUANTUMDB}/conferences".encode()
    if not any(DATA_MARK in p.read_bytes() for p in landing):
        for pattern, link in (
            (rb'(<p class="qc-links">.*?)(\s*</p>)',
             b'\n      <span class="qc-sep">&middot;</span>\n      ' + DATA_MARK + b'<a href="' + href +
             b'" title="Talks, posters, authors and committees as open data (CC0)">Data (QuantumDB)</a>'),
            (rb'(<p class="qip-intro">.*?)(</p>)',
             b' ' + DATA_MARK + b'The talks, posters, authors and committees are also open data in <a href="' +
             href + b'">QuantumDB</a>.'),
        ):
            hit = next(((p, m) for p in landing
                        for m in [re.search(pattern, p.read_bytes(), re.S)] if m), None)
            if hit:
                p, m = hit
                b = p.read_bytes()
                p.write_bytes(b[:m.end(1)] + link + b[m.end(1):])
                changed.append(p.name)
                break

    print(f"{v['host']}: {n} URLs in sitemap, {len(years)} archived years known to QuantumDB")
    changed = list(dict.fromkeys(changed))
    print("changed: " + (", ".join(changed) if changed else "nothing"))


if __name__ == "__main__":
    main()
