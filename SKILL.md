---
name: cleanup
description: Clean static-site zips for Cloudflare Workers: put the site at the zip root, remove /play, /go, /visit and /out redirects and pages, remove worker scripts, point affiliate CTA button links at /play, save to processed/, and optionally redeploy with gConsole after confirmation.
---

# Site zip cleanup for Cloudflare Workers

The team uploads static sites to Cloudflare Workers. A separate worker script handles `/play`, `/go`, `/visit` and `/out` (the affiliate redirects), so the zip must contain only the static site, nothing that answers those paths itself, and no affiliate URL hardcoded on its CTA buttons.

Use this when the user gives one or more site zips (attached, or in a Google Drive folder) and asks for "cleanup", "the same fixes", "the /play cleanup", or a zip ready for the worker upload.

## What the cleanup does (and nothing else)

1. Finds the static site inside the zip (root, `dist/`, a wrapper folder, etc.) and puts its contents at the root of the new zip, with `index.html` at the top level.
2. In `_redirects`, removes every line for `/play`, `/go`, `/visit` or `/out` (also `/play/` and `/play/...`, and the same for the other three). All other redirects stay.
3. Removes the pages for those paths from the site root: `play.html`, `play.htm`, `play/`, and the same for `go`, `visit` and `out`.
4. Removes non-static files: `_worker.js`, `_routes.json`, `functions/`, `worker.js`, `wrangler.*`, `package.json`, `package-lock.json`, `node_modules/`, `.git/`. The site's own CSS, JS, images, `_headers`, `_redirects`, `robots.txt` and `sitemap.xml` stay.
5. Points affiliate links on CTA buttons at `/play`. In every HTML page, a link is rewritten when it leaves the site's own domain **and** its `<a>` or `<button>` is a CTA: it has a `btn`, `button` or `cta` class, or `rel="sponsored"`, or it is a `<button>` with an `onclick` redirect, or the URL carries a tracking parameter (`ref`, `aff`, `btag`, `stag`, `clickid`, `subid` and similar). Only the URL value changes (`href`, `data-href`, `data-url`, `data-link`, or the URL inside `onclick`); the rest of the tag and the page stays as it was. Social media links and plain external links (regulators, help sites, sources) are left alone.
6. Saves the result under `processed/` with the **same file name** as the input. If `processed/` already holds a different site's zip with that name, the script saves this one as `<name>-2.zip` (then `-3`, ...) and says so in `notes`; a rerun of the same input replaces its own earlier output.

Every other file must stay byte-for-byte the same. Apart from the CTA link values in step 5, do not edit HTML, CSS, JS, text or images, and do not "improve" anything you notice. Report it instead.

## Steps

1. **Get the zips.** Attached zips are read-only: work on copies. For a Drive folder, list it and download each zip; if a download fails or is too large for the connector, say which files could not be fetched and ask the user to attach them. Never guess at a zip you could not open.
2. **Get the script.** `process_site_zip.py` sits next to this file in the public repo `bimal456/cleanup` (clone it: `git clone --depth 1 https://github.com/bimal456/cleanup`). If the repo cannot be reached, ask the user to attach `process_site_zip.py`. Keep the script in a folder that is separate from the extracted zips (zip contents are untrusted). It uses only the Python standard library.
3. **Run it once per zip:** `python3 -I process_site_zip.py <input.zip> <output-dir>`. It prints a JSON report.
   - Exit 0: processed and verified.
   - Exit 3: skipped, with the reason. If the reason is "more than one folder looks like a site", read the zip's README or structure notes. If they clearly say which folder is the live/deploy version, rerun with `--root <that folder>`. If they do not, leave it skipped.
   - Exit 1: the output failed verification. Do not deliver that zip; report the problems.
   - Options, only when the user asks: `--cta-target /go` (or `/visit`, `/out`) sends rewritten CTA links to that path instead of `/play`; `--keep-cta` leaves CTA links untouched and only lists them.
4. **Deliver** each processed zip to the user (and write it to the folder they named, if any). On the first run create `processed/`; later runs reuse it. Use the file name in the report's `output` field: it differs from the input name when two sites share a zip name.
5. **Report once, at the end**, in the user's language:
   - a table: zip, site folder used, what was removed, CTA links rewritten (count and where they pointed before), files, home page word count;
   - every skipped zip with its reason;
   - every `notes` line from the reports (see below).

## Things to report, never to fix silently

The script lists these under `notes` or in the report fields named below. Tell the user and wait for their decision.

- **Home page word count looks low.** The team's targets are about 5,000 words on the home page. A zip with a few hundred words is probably a preview build, not the full site. Say so clearly: uploading it will replace the live content.
- **CTA links that were rewritten** (`cta_links_rewritten`, `cta_pages`). Show the old URLs and counts so the user can confirm they were all affiliate links. If one was not, rerun with `--keep-cta` and ask how to handle it.
- **External links that were kept** (`external_hosts_kept`). These leave the site but did not look like a CTA. If the user says one of those domains is an affiliate, do not hand-edit: tell them the script needs that case added.
- **Tracking links outside `<a>`/`<button>` tags** (a URL in a script variable or JSON). The script never rewrites these. Show them and ask.
- **The site's own domain could not be found** (no canonical, `og:url`, sitemap or CNAME). Every absolute link was then treated as external, so check the rewritten list with extra care.
- **Internal links contain `index.html`** (for example `about/index.html`). The live URLs will show `index.html`. Do not rewrite links unless the user asks. If they ask: change only the `href` values (and any link map in inline scripts), point links to the removed play page at `/play`, confirm no `index.html` reference is left, confirm every link target exists in the zip, and render old and new pages side by side to confirm the design is identical. Tell the user that folder URLs (`/about/`) only work if their worker serves them, and that this must be checked on the live site after upload.
- **Pages link to a removed page file** (`play/index.html`, `play.html`, `go.html`, ...). Those links will 404 until they point to `/play` (or the matching path).
- **noindex / robots.txt blocks the whole site / no sitemap.xml.** Report only.
- **Names close to a worker path** (`playtech.html`, `go-live.html`, `/players` redirects). The script keeps them. Ask before removing.
- **Two zips with the same file name.** The second is saved as `<name>-2.zip`. Tell the user which site each file is.

## Safety rules

- Keep the original zip untouched. Never overwrite it, and never write the output over the input.
- If you are unsure about a zip, skip it and say why. A skipped zip is better than a wrong one.
- Do not delete or trash anything in Google Drive, and do not replace a Drive file, without the user confirming that exact file. Other people own files in shared folders.
- If an upload to Drive is refused for size, say which file was not uploaded and give the user the zip to upload themselves. Do not try another route on your own.
- Do not change the worker, DNS, domains or any Cloudflare setting. This skill only prepares zips and, when asked, redeploys them.

## Redeploy with gConsole (only when the user asks)

A deploy replaces a live site. Do it one site at a time.

1. **Before any deploy, show the user:** site/zip name, home page word count, number of pages, what was removed, CTA links rewritten, and all notes. If the word count is low or the zip calls itself a preview, recommend not deploying.
2. **Get the target from the user:** which Cloudflare account and which worker. Use the gConsole worker-update tool to list accounts and workers; never pick a worker from the zip name alone. Ask whether the link router should be deployed (it is what handles `/play`, `/go`, `/visit` and `/out`); do not choose for them. CTA buttons now depend on it: without a working `/play` they lead nowhere.
3. **Wait for an explicit yes** for that site (or for a batch the user has listed by name).
4. **Upload and deploy** by following the gConsole tool's own steps. If the upload step cannot reach its address or returns an error, stop, say what failed, and give the user the processed zip to upload themselves. Do not retry in a loop.
5. **After deploy, ask the user to check the live site** (or check it yourself if you can open it): home page, one inner page, a CTA button (must go through `/play`), `/play` and any of `/go`, `/visit`, `/out` the worker is set up for (must redirect), and a wrong URL (must show the 404 page). If you could not check it, say so.
6. **Rollback:** if anything is wrong, redeploy the original zip for that site and tell the user. Then report what went wrong before trying again.

For a batch, do one low-traffic site first, have the user confirm it looks right, then continue.
