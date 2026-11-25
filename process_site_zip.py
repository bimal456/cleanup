#!/usr/bin/env python3
"""Clean a static-site zip for Cloudflare Workers. Stdlib only.
Usage: python3 -I process_site_zip.py <input.zip> <output-dir> [--root <folder inside zip>]
                                      [--cta-target /play] [--keep-cta]
  --cta-target  where rewritten CTA links point (default /play; must be /play, /go, /visit or /out)
  --keep-cta    do not rewrite affiliate CTA links, only report them
Exit 0 = processed, 3 = skipped (reason printed), 1 = error."""
import hashlib, html, json, os, re, shutil, sys, tempfile, zipfile
from urllib.parse import urlsplit, parse_qsl

ROUTES = ("play", "go", "visit", "out")  # paths the worker answers; the zip must not answer them
_R = "|".join(ROUTES)
ROUTE_LINE = re.compile(rf"^/({_R})(/|\s|$)", re.I)
ROUTE_NEAR = re.compile(rf"^/(play|({_R})[-_.0-9])", re.I)
ROUTE_NAMES = {r + e for r in ROUTES for e in (".html", ".htm", "")}
NAME_NEAR = re.compile(rf"^(play|({_R})[-_0-9])", re.I)
NON_STATIC = {"_worker.js", "_routes.json", "functions", "worker.js", "wrangler.toml", "wrangler.json",
              "wrangler.jsonc", "package.json", "package-lock.json", "node_modules", ".git", ".wrangler"}
JUNK = {"__MACOSX", ".DS_Store", "Thumbs.db"}
PREFER = ("dist", "build", "public", "deploy", "cloudflare", "site", "www", "out", "_site")

# affiliate CTA detection
TAG = re.compile(r"""(?is)<(a|button)\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
ATTR = re.compile(r"""(?is)([a-z_:][-a-z0-9_:.]*)\s*=\s*("[^"]*"|'[^']*'|[^\s>"']+)""")
URL_IN_JS = re.compile(r"""(?i)(?:https?:)?//[a-z0-9][^\s'"\\)<>]*""")
CTA_CLASS = re.compile(r"(?i)btn|button|(?<![a-z])cta(?![a-z])")
TRACK = {"ref", "refid", "ref_id", "refcode", "aff", "affid", "aff_id", "affiliate", "affiliate_id", "a_aid",
         "btag", "stag", "clickid", "click_id", "subid", "sub_id", "sub1", "tracker", "partner", "partnerid",
         "partner_id", "offer_id", "campaign_id"}
URL_ATTRS = ("href", "data-href", "data-url", "data-link")
SOCIAL = ("facebook.com", "fb.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "youtu.be",
          "t.me", "telegram.me", "telegram.org", "wa.me", "whatsapp.com", "linkedin.com", "pinterest.com",
          "tiktok.com", "reddit.com", "discord.gg", "discord.com")

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

def host_of(url):
    """Host of an absolute or protocol-relative URL, lower-case without www.; '' if not such a URL."""
    u = html.unescape(url).strip()
    if not re.match(r"(?i)^(https?:)?//", u):
        return ""
    try:
        h = (urlsplit(u if not u.startswith("//") else "https:" + u).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h

def tracked(url):
    try:
        q = urlsplit(html.unescape(url).strip()).query
    except ValueError:
        return False
    return any(k.lower() in TRACK for k, _ in parse_qsl(q, keep_blank_values=True))

def own_hosts(after):
    """Domains the site calls its own (canonical, og:url, sitemap, CNAME)."""
    own = set()
    for k in ("index.html", "index.htm"):
        if k in after:
            t = open(after[k], encoding="utf-8", errors="ignore").read()
            for m in re.finditer(r"""(?is)<(?:link|meta)\b[^>]*>""", t):
                tag = m.group(0)
                if re.search(r"""(?i)rel\s*=\s*["']?canonical|property\s*=\s*["']?og:url""", tag):
                    v = re.search(r"""(?i)(?:href|content)\s*=\s*["']([^"']+)""", tag)
                    if v:
                        own.add(host_of(v.group(1)))
    if "sitemap.xml" in after:
        t = open(after["sitemap.xml"], encoding="utf-8", errors="ignore").read()
        own.update(host_of(u) for u in re.findall(r"(?is)<loc>\s*([^<\s]+)", t)[:50])
    if "CNAME" in after:
        h = open(after["CNAME"], encoding="utf-8", errors="ignore").read().strip().lower()
        own.add(h[4:] if h.startswith("www.") else h)
    own.discard("")
    return own

def scan_cta(text, own, target):
    """Find affiliate links on CTA elements. Returns (new_text, [replaced urls], {kept external host: count}).
    A link counts when it leaves the site and its <a>/<button> has a button/cta class, rel=sponsored,
    is a <button>, or the URL carries a tracking parameter. Social links are never touched."""
    hits, kept, out, pos = [], {}, [], 0
    ext = lambda u: (lambda h: h and h not in own and not any(h == s or h.endswith("." + s) for s in SOCIAL) and h)(host_of(u))
    for m in TAG.finditer(text):
        tag, el = m.group(0), m.group(1).lower()
        attrs = [(a.group(1).lower(), a.group(2), a.start(2), a.end(2)) for a in ATTR.finditer(tag)]
        val = lambda raw: raw[1:-1] if raw[:1] in "\"'" else raw
        get = lambda n: next((val(r) for k, r, _, _ in attrs if k == n), "")
        spans = []  # (start, end, url) inside tag
        for k, raw, s, e in attrs:
            off = 1 if raw[:1] in "\"'" else 0
            v = val(raw)
            if k in URL_ATTRS and ext(v):
                spans.append((s + off, s + off + len(v), v))
            elif k == "onclick":
                for u in URL_IN_JS.finditer(v):
                    if ext(u.group(0)):
                        spans.append((s + off + u.start(), s + off + u.end(), u.group(0)))
        if not spans:
            continue
        is_cta = (el == "button" or CTA_CLASS.search(get("class")) or "sponsored" in get("rel").lower()
                  or any(tracked(u) for _, _, u in spans))
        if not is_cta:
            for _, _, u in spans:
                kept[host_of(u)] = kept.get(host_of(u), 0) + 1
            continue
        new, p = [], 0
        for s, e, u in sorted(spans):
            new += [tag[p:s], target]; p = e
            hits.append(html.unescape(u))
        new.append(tag[p:])
        out += [text[pos:m.start()], "".join(new)]; pos = m.end()
    out.append(text[pos:])
    return "".join(out), hits, kept

def main():
    a = sys.argv[1:]
    want, target, keep_cta = None, "/play", False
    if "--root" in a:
        i = a.index("--root"); want = a[i + 1].strip("/"); del a[i:i + 2]
    if "--cta-target" in a:
        i = a.index("--cta-target"); target = a[i + 1]; del a[i:i + 2]
        if target.strip("/").lower() not in ROUTES:
            sys.exit("--cta-target must be one of: " + ", ".join("/" + r for r in ROUTES))
        target = "/" + target.strip("/").lower()
    if "--keep-cta" in a:
        a.remove("--keep-cta"); keep_cta = True
    if len(a) != 2:
        sys.exit(__doc__)
    src, outdir = a
    name = os.path.basename(src)
    src_sha = sha(src) if os.path.isfile(src) else ""
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
        if n.lower() in ROUTE_NAMES or n in NON_STATIC or n in JUNK:
            removed.append(n + ("/" if os.path.isdir(p) else ""))
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        elif NAME_NEAR.match(n):
            notes.append(f"kept '{n}' (name is close to a /play, /go, /visit or /out page but is not one): confirm with the user")
    red = os.path.join(root, "_redirects")
    red_removed = []
    if os.path.isfile(red):
        raw = open(red, encoding="utf-8", errors="surrogateescape", newline="").read()
        keep = []
        for line in raw.splitlines(keepends=True):
            if ROUTE_LINE.match(line.strip()):
                red_removed.append(line.strip())
            else:
                if ROUTE_NEAR.match(line.strip()):
                    notes.append(f"kept _redirects line '{line.strip()}' (not /play, /go, /visit or /out itself): confirm with the user")
                keep.append(line)
        if red_removed:
            open(red, "w", encoding="utf-8", errors="surrogateescape", newline="").write("".join(keep))

    # affiliate links on CTA buttons -> the worker route
    after = files(root)
    pages = [k for k in after if k.lower().endswith((".html", ".htm"))]
    own = own_hosts(after)
    cta_pages, cta_urls, kept_hosts = {}, {}, {}
    for k in sorted(pages):
        text = open(after[k], "rb").read().decode("utf-8", errors="surrogateescape")
        new, hits, kept = scan_cta(text, own, target)
        for h, c in kept.items():
            kept_hosts[h] = kept_hosts.get(h, 0) + c
        if hits:
            cta_pages[k] = len(hits)
            for u in hits:
                cta_urls[u] = cta_urls.get(u, 0) + 1
            if not keep_cta:
                open(after[k], "wb").write(new.encode("utf-8", errors="surrogateescape"))
    if cta_pages and keep_cta:
        notes.append(f"{sum(cta_pages.values())} affiliate CTA link(s) on {len(cta_pages)} page(s) were left in place (--keep-cta)")
    if not own:
        notes.append("could not tell the site's own domain (no canonical, og:url, sitemap or CNAME): every absolute link was treated as external")

    # output name: never overwrite a different site's zip that has the same file name
    os.makedirs(os.path.join(outdir, "processed"), exist_ok=True)
    stem, extn = os.path.splitext(name)
    dst, i = os.path.join(outdir, "processed", name), 1
    def same_source(p):
        try:
            with zipfile.ZipFile(p) as z:
                return z.comment.decode("ascii", "ignore") == "source-sha256:" + src_sha
        except (zipfile.BadZipFile, OSError):
            return False
    while os.path.exists(dst) and not same_source(dst):
        i += 1
        dst = os.path.join(outdir, "processed", f"{stem}-{i}{extn}")
    if i > 1:
        notes.append(f"processed/{name} already holds a different zip with this file name, so this one was saved as "
                     f"{os.path.basename(dst)}: tell the user which site each file is")
    if os.path.abspath(dst) == os.path.abspath(src):
        skip("output would overwrite the input zip")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.comment = ("source-sha256:" + src_sha).encode("ascii")
        for k in sorted(after):
            z.write(after[k], k)

    # verify the written zip
    problems = []
    may_change = {"_redirects"} | (set() if keep_cta else set(cta_pages))
    with zipfile.ZipFile(dst) as z:
        if z.testzip():
            problems.append("output zip failed its integrity test")
        names = z.namelist()
        if "index.html" not in names and "index.htm" not in names:
            problems.append("index.html is not at the zip root")
        for n in names:
            top = n.split("/")[0]
            if top.lower() in ROUTE_NAMES or top in NON_STATIC:
                problems.append(f"still contains {n}")
        if "_redirects" in names and any(ROUTE_LINE.match(l.strip()) for l in z.read("_redirects").decode("utf-8", "ignore").splitlines()):
            problems.append("_redirects still has a /play, /go, /visit or /out line")
        changed = [n for n in names if n not in may_change and hashlib.sha256(z.read(n)).hexdigest() != before.get(n)]
        if changed:
            problems.append(f"{len(changed)} file(s) differ from the source, e.g. {changed[:3]}")
        if not keep_cta:
            left = [n for n in names if n.lower().endswith((".html", ".htm"))
                    and scan_cta(z.read(n).decode("utf-8", errors="surrogateescape"), own, target)[1]]
            if left:
                problems.append(f"affiliate CTA links are still present in {len(left)} page(s), e.g. {left[:3]}")
    home = after.get("index.html") or after.get("index.htm")
    home_txt = open(home, encoding="utf-8", errors="ignore").read()
    if re.search(r'href="[^"]*index\.html"', home_txt):
        notes.append("internal links contain index.html (URLs will show index.html); report it, do not rewrite links unless the user asks")
    if re.search(rf'href="[^"#:]*\b({_R})(/index\.html|\.html?)"', home_txt):
        notes.append("pages link to a removed play/go/visit/out page file instead of the worker path: those links will 404 until fixed; ask the user")
    if re.search(r'(?i)<meta[^>]+name="robots"[^>]+noindex', home_txt):
        notes.append("home page is noindex")
    rb = after.get("robots.txt")
    if rb and re.search(r"(?im)^\s*Disallow:\s*/\s*$", open(rb, encoding="utf-8", errors="ignore").read()):
        notes.append("robots.txt blocks the whole site")
    if "sitemap.xml" not in after:
        notes.append("no sitemap.xml")
    # affiliate-looking URLs outside <a>/<button> (scripts, JSON): reported, never rewritten
    stray = set()
    for k in sorted(after):
        if k.lower().endswith((".html", ".htm", ".js", ".json")) and os.path.getsize(after[k]) < 5_000_000:
            t = open(after[k], encoding="utf-8", errors="ignore").read()
            for u in URL_IN_JS.findall(t):
                h = host_of(u)
                if h and h not in own and tracked(u):
                    stray.add(f"{k}: {html.unescape(u)[:120]}")
    if stray:
        notes.append(f"{len(stray)} link(s) with tracking parameters remain outside <a>/<button> tags (scripts or data), "
                     f"not rewritten; show them to the user: {sorted(stray)[:5]}")
    print(json.dumps({
        "status": "failed-verification" if problems else "processed", "problems": problems,
        "output": dst, "site_root_in_zip": rel(root), "other_candidates": [rel(c) for c in cands if c != root],
        "removed": removed, "redirect_lines_removed": red_removed,
        "cta_target": None if keep_cta else target,
        "cta_links_found" if keep_cta else "cta_links_rewritten": cta_urls, "cta_pages": cta_pages,
        "external_hosts_kept": dict(sorted(kept_hosts.items(), key=lambda x: -x[1])[:30]),
        "files": len(after), "html_pages": len(pages), "home_words": words(home),
        "notes": notes}, indent=1, ensure_ascii=False))
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
