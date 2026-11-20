# Changelog

Releases are git tags `vX.Y.Z`; a site uses one with
`go get github.com/IAQI/conference-theme@vX.Y.Z`. While the version is 0.x, a
minor release (0.2.0) may need changes in a site; they are listed here under
**Sites must**. Patch releases (0.1.1) never do.

## v0.1.0 (2026-11-20)

The first release: the shared theme of the QCrypt, TQC and QIP websites,
now set up so that other conferences can use it too.

- Layouts and styles of all pages: home page blocks, team, speakers,
  sponsors, text pages, header, footer, menus.
- The program: schedule (side-by-side parallel sessions, topic colours, time
  zone switch), session pages, accepted papers and posters, presenters,
  videos and slides, all keyed by the HotCRP paper id.
- Scripts from the HotCRP list of accepted papers to a linked schedule, and
  `new_year.py` for setting up a new year.
- Information for search engines and AI agents: JSON-LD (the conference and
  each session as schema.org `Event`, the session's `superEvent` with the
  conference's full details), `/llms.txt`, program exports as JSON and iCal,
  robots.txt.
- New: [`exampleSite/`](exampleSite/), a small fictional conference to start
  a site from, and a "Using the theme for your conference" section in the
  README. GitHub Actions builds it and runs the script tests on every pull
  request.
- Neutral IAQI defaults instead of QCrypt ones: `programFilePrefix` (the
  prefix of older slide file names) now defaults to `"IAQI"`.
- The footer no longer shows an empty "Follow us" heading when
  `data/footer.yml` has no `follow` links.

**Sites must:** set `programFilePrefix = "QCrypt"` (or their own prefix) in
`[params]` only if they still have slide files named
`QCrypt<YEAR>TalkSlides<NNN>…`. None of the IAQI sites currently has any.
