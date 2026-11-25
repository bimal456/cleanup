# cleanup

Claude skill that cleans static-site zips before they are uploaded to Cloudflare Workers.

A separate worker script handles `/play`, `/go`, `/visit` and `/out`, so the zip must hold only the static site:

- site files at the zip root (`index.html` at the top level)
- no `/play`, `/go`, `/visit` or `/out` lines in `_redirects`
- no `play.html`, `play.htm` or `play/` folder (same for `go`, `visit`, `out`)
- no worker scripts (`_worker.js`, `_routes.json`, `functions/`, `wrangler.*`)
- no affiliate URL on CTA buttons: those links are pointed at `/play`

Every other file stays byte-for-byte the same. The output goes to `processed/` with the same file name; if a different site's zip with that name is already there, the new one is saved as `<name>-2.zip`.

## Files

- `SKILL.md` - the skill instructions (steps, what to report, safety rules, redeploy with gConsole)
- `process_site_zip.py` - the script the skill runs (Python standard library only)

## Use in Claude

Ask Claude: "read SKILL.md in github.com/bimal456/cleanup and follow it", and attach the zips (or give the Drive folder).

## Run the script by hand

    python3 -I process_site_zip.py <input.zip> <output-dir> [--root <folder inside zip>] [--cta-target /play] [--keep-cta]

- `--cta-target` - where rewritten CTA links point: `/play` (default), `/go`, `/visit` or `/out`
- `--keep-cta` - leave CTA links as they are and only list them

A CTA link is an `<a>` or `<button>` that leaves the site's own domain and has a `btn`/`button`/`cta` class, `rel="sponsored"`, an `onclick` redirect on a `<button>`, or a tracking parameter (`ref`, `aff`, `btag`, `clickid`, ...). Social links and plain external links are kept.

It prints a JSON report. Exit 0 = processed and verified, 3 = skipped (reason in the report), 1 = output failed verification.
