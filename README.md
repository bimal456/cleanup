# cleanup

Claude skill that cleans static-site zips before they are uploaded to Cloudflare Workers.

A separate worker script handles `/play`, so the zip must hold only the static site:

- site files at the zip root (`index.html` at the top level)
- no `/play` lines in `_redirects`
- no `play.html`, `play.htm` or `play/` folder
- no worker scripts (`_worker.js`, `_routes.json`, `functions/`, `wrangler.*`)

Every other file stays byte-for-byte the same. The output goes to `processed/` with the same file name.

## Files

- `SKILL.md` - the skill instructions (steps, what to report, safety rules, redeploy with gConsole)
- `process_site_zip.py` - the script the skill runs (Python standard library only)

## Use in Claude

Ask Claude: "read SKILL.md in github.com/bimal456/cleanup and follow it", and attach the zips (or give the Drive folder).

## Run the script by hand

    python3 -I process_site_zip.py <input.zip> <output-dir> [--root <folder inside zip>]

It prints a JSON report. Exit 0 = processed and verified, 3 = skipped (reason in the report), 1 = output failed verification.
