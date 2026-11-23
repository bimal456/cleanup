#!/usr/bin/env python3
"""Clean a static-site zip for Cloudflare Workers. Stdlib only.
Usage: python3 -I process_site_zip.py <input.zip> <output-dir> [--root <folder inside zip>]
Exit 0 = processed, 3 = skipped (reason printed), 1 = error."""
import hashlib, html, json, os, re, shutil, sys, tempfile, zipfile

PLAY_LINE = re.compile(r"^/play(/|\s|$)", re.I)
PLAY_NAMES = {"play.html", "play.htm", "play"}
NON_STATIC = {"_worker.js", "_routes.json", "functions", "worker.js", "wrangler.toml", "wrangler.json",
              "wrangler.jsonc", "package.json", "package-lock.json", "node_modules", ".git", ".wrangler"}
JUNK = {"__MACOSX", ".DS_Store", "Thumbs.db"}
PREFER = ("dist", "build", "public", "deploy", "cloudflare", "site", "www", "out", "_site")


def skip(reason, **extra):
    print(json.dumps(dict(status="skipped", reason=reason, **extra), indent=1, ensure_ascii=False))
    sys.exit(3)


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def words(p):
    s = open(p, encoding="utf-8", errors="ignore").read()
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    m = re.search(r"(?is)<main\b.*?</main>", s)
    return len(re.sub(r"<[^>]+>", " ", html.unescape(m.group(0) if m else s)).split())


def files(root):
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = p
    return out


def main():
    a = sys.argv[1:]
    want = None
    if "--root" in a:
        i = a.index("--root"); want = a[i + 1].strip("/"); del a[i:i + 2]
    if len(a) != 2:
        sys.exit(__doc__)
    src, outdir = a
    name = os.path.basename(src)
    tmp = tempfile.mkdtemp(prefix="sitezip-")
    ex = os.path.join(tmp, "x")
    try:
        with zipfile.ZipFile(src) as z:
            if z.testzip():
                skip("zip is corrupt")
            for n in z.namelist():
                if n.startswith("/") or ".." in n.split("/") or re.match(r"^[A-Za-z]:", n):
                    skip(f"unsafe path inside zip: {n}")
            z.extractall(ex)
    except zipfile.BadZipFile:
        skip("not a valid zip file")

    # candidate roots: folders with index.html that are not inside another candidate
    cands = []
    for d, ds, fs in os.walk(ex):
        ds[:] = sorted(x for x in ds if x not in JUNK and x != "node_modules")
        if "index.html" in fs or "index.htm" in fs:
            cands.append(d); ds[:] = []
    rel = lambda d: os.path.relpath(d, ex).replace(os.sep, "/")
    if not cands:
        skip("no index.html found anywhere in the zip")
    if want is not None:
        root = os.path.join(ex, want) if want not in ("", ".") else ex
        if root not in cands:
            skip(f"--root '{want}' has no index.html", candidates=[rel(c) for c in cands])
    elif len(cands) == 1:
        root = cands[0]
    else:
        pref = [c for c in cands if any(k in os.path.basename(c).lower() for k in PREFER)]
        if len(pref) == 1:
            root = pref[0]
        else:
            skip("more than one folder looks like a site; read the zip's README and rerun with --root, or skip",
                 candidates=[rel(c) for c in cands])

    before = {k: sha(p) for k, p in files(root).items()}
    removed, notes = [], []
    for n in sorted(os.listdir(root)):
        p = os.path.join(root, n)
        if n.lower() in PLAY_NAMES or n in NON_STATIC or n in JUNK:
            removed.append(n + ("/" if os.path.isdir(p) else ""))
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        elif n.lower().startswith("play"):
            notes.append(f"kept '{n}' (starts with play but is not play.html/play.htm/play/): confirm with the user")
    red = os.path.join(root, "_redirects")
    red_removed = []
    if os.path.isfile(red):
        raw = open(red, encoding="utf-8", errors="surrogateescape").read()
        keep = []
        for line in raw.splitlines(keepends=True):
            if PLAY_LINE.match(line.strip()):
                red_removed.append(line.strip())
            else:
                if line.strip().lower().startswith("/play"):
                    notes.append(f"kept _redirects line '{line.strip()}' (not /play itself): confirm with the user")
                keep.append(line)
        if red_removed:
            open(red, "w", encoding="utf-8", errors="surrogateescape").write("".join(keep))

    os.makedirs(os.path.join(outdir, "processed"), exist_ok=True)
    dst = os.path.join(outdir, "processed", name)
    if os.path.abspath(dst) == os.path.abspath(src):
        skip("output would overwrite the input zip")
    after = files(root)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for k in sorted(after):
            z.write(after[k], k)

    # verify the written zip
    problems = []
    with zipfile.ZipFile(dst) as z:
        if z.testzip():
            problems.append("output zip failed its integrity test")
        names = z.namelist()
        if "index.html" not in names and "index.htm" not in names:
            problems.append("index.html is not at the zip root")
        for n in names:
            top = n.split("/")[0]
            if top.lower() in PLAY_NAMES or top in NON_STATIC:
                problems.append(f"still contains {n}")
        if "_redirects" in names and any(PLAY_LINE.match(l.strip()) for l in z.read("_redirects").decode("utf-8", "ignore").splitlines()):
            problems.append("_redirects still has a /play line")
        changed = [n for n in names if n != "_redirects" and hashlib.sha256(z.read(n)).hexdigest() != before.get(n)]
        if changed:
            problems.append(f"{len(changed)} file(s) differ from the source, e.g. {changed[:3]}")
    pages = [k for k in after if k.lower().endswith((".html", ".htm"))]
    home = after.get("index.html") or after.get("index.htm")
    home_txt = open(home, encoding="utf-8", errors="ignore").read()
    if re.search(r'href="[^"]*index\.html"', home_txt):
        notes.append("internal links contain index.html (URLs will show index.html); report it, do not rewrite links unless the user asks")
    if re.search(r'href="[^"#]*\bplay(/index\.html|\.html?)"', home_txt):
        notes.append("pages link to the removed play page file instead of /play: those links will 404 until fixed; ask the user")
    if re.search(r'(?i)<meta[^>]+name="robots"[^>]+noindex', home_txt):
        notes.append("home page is noindex")
    rb = after.get("robots.txt")
    if rb and re.search(r"(?im)^\s*Disallow:\s*/\s*$", open(rb, encoding="utf-8", errors="ignore").read()):
        notes.append("robots.txt blocks the whole site")
    if "sitemap.xml" not in after:
        notes.append("no sitemap.xml")
    print(json.dumps({
        "status": "failed-verification" if problems else "processed", "problems": problems,
        "output": dst, "site_root_in_zip": rel(root), "other_candidates": [rel(c) for c in cands if c != root],
        "removed": removed, "redirect_lines_removed": red_removed,
        "files": len(after), "html_pages": len(pages), "home_words": words(home),
        "notes": notes}, indent=1, ensure_ascii=False))
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
