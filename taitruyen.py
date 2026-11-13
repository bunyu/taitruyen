#!/usr/bin/env python3
"""
taitruyen.py - Download a web novel (one chapter per page) and pack it into an EPUB.

Usage examples:
    # Start from chapter 1 and follow "next chapter" links until the end
    python taitruyen.py https://www.tvtruyen.space/tu-tien-khi-nguoi-lam-mot-viec-den-cuc-han/chuong-1-hua-duong

    # Only the first 50 chapters, custom output file
    python taitruyen.py URL --max 50 -o tu-tien.epub

    # Start from the novel's index page (collects the chapter list from it)
    python taitruyen.py https://www.tvtruyen.space/tu-tien-khi-nguoi-lam-mot-viec-den-cuc-han

Downloaded chapters are cached in a folder (default: .cache/<novel-slug>/) so an
interrupted run can be resumed by running the same command again.
"""

import argparse
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

# CSS selectors commonly used by Vietnamese novel sites for the chapter body.
CONTENT_SELECTORS = [
    "#chapter-c", ".chapter-c", "#chapter-content", ".chapter-content",
    "#chapterContent", ".chapterContent", "#content-chapter", ".content-chapter",
    ".reading-content", ".chapter-body", "#vungdoc", ".box-chap", "#box-chap",
    ".chapter_content", "#chapter_content", ".text-chapter", "#js-read__content",
    "article .content", "#content", ".content", "article",
]

TITLE_SELECTORS = [
    ".chapter-title", "#chapter-title", ".chapter-name", ".chapter_title",
    "h2.title", "h1.title", "h2", "h1",
]

NOVEL_TITLE_SELECTORS = [
    ".truyen-title", ".story-title", ".novel-title", ".book-title",
    "h1.title a", "h1 a", "h3.title",
]

NEXT_TEXT_RE = re.compile(
    r"(chương\s*(sau|tiếp|kế)|chap\s*sau|tiếp\s*theo|next)", re.IGNORECASE
)
NEXT_ATTR_RE = re.compile(r"(^|[-_\s])next([-_\s]|$)|next[-_]?chap", re.IGNORECASE)

JUNK_SELECTORS = [
    "script", "style", "noscript", "iframe", "ins", "form", "button", "nav",
    "header", "footer", "aside", "[class*=ads]", "[id*=ads]", "[class*=banner]",
    "[class*=quang-cao]", "[class*=comment]", "[id*=comment]", "[class*=share]",
]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, delay=1.0, retries=4, timeout=30):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0

    def get(self, url):
        for attempt in range(1, self.retries + 1):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.s.get(url, timeout=self.timeout)
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
                return r.text
            except requests.RequestException as e:
                if attempt == self.retries:
                    raise
                backoff = 2 ** attempt
                print(f"   ! {e} - retry in {backoff}s", file=sys.stderr)
                time.sleep(backoff)
        return None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def clean_text(s):
    return re.sub(r"\s+", " ", s or "").strip()


def novel_prefix(url):
    """'/novel-slug/chuong-1-abc' -> '/novel-slug/'"""
    path = urlparse(url).path.rstrip("/")
    parts = path.split("/")
    if len(parts) >= 3 and re.match(r"(chuong|chap|chapter)[-_]?\d", parts[-1], re.I):
        return "/".join(parts[:-1]) + "/"
    return path + "/"


def is_chapter_url(url):
    last = urlparse(url).path.rstrip("/").split("/")[-1]
    return bool(re.match(r"(chuong|chap|chapter)[-_]?\d", last, re.I))


def chapter_number(url):
    m = re.search(r"(?:chuong|chap|chapter)[-_]?(\d+)", urlparse(url).path, re.I)
    return int(m.group(1)) if m else None


def find_content(soup, selector=None):
    if selector:
        el = soup.select_one(selector)
        if el:
            return el
        print(f"   ! selector {selector!r} not found, falling back to auto-detect",
              file=sys.stderr)
    for sel in CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 200:
            return el
    # Fallback: the block element with the most direct text / <p> children
    best, best_score = None, 0
    for el in soup.find_all(["div", "article", "section", "main"]):
        direct = sum(len(clean_text(t)) for t in el.find_all(string=True, recursive=False))
        ps = sum(len(p.get_text(strip=True)) for p in el.find_all("p", recursive=False))
        brs = len(el.find_all("br", recursive=False))
        score = direct + ps + brs * 20
        if score > best_score:
            best, best_score = el, score
    return best


def content_to_paragraphs(el):
    for sel in JUNK_SELECTORS:
        for j in el.select(sel):
            j.decompose()
    for c in el.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    for br in el.find_all("br"):
        br.replace_with("\n")
    for p in el.find_all(["p", "div", "h3", "h4"]):
        p.insert_before("\n")
        p.insert_after("\n")
    text = el.get_text()
    paras = [clean_text(line) for line in text.split("\n")]
    return [p for p in paras if p]


def find_title(soup, fallback):
    for sel in TITLE_SELECTORS:
        for el in soup.select(sel):
            t = clean_text(el.get_text())
            if t and re.search(r"(chương|chapter|chap)\s*\d+", t, re.I):
                return t
    if soup.title:
        t = clean_text(soup.title.get_text())
        m = re.search(r"((?:chương|chapter)\s*\d+[^|\-–]*)", t, re.I)
        if m:
            return clean_text(m.group(1))
    return fallback


def find_novel_info(soup, url):
    title = None
    for sel in NOVEL_TITLE_SELECTORS:
        el = soup.select_one(sel)
        if el and clean_text(el.get_text()):
            title = clean_text(el.get_text())
            break
    if not title:
        meta = soup.find("meta", property="og:title")
        if meta and meta.get("content"):
            title = re.split(r"\s+[-|–]\s+|\s*(?:chương|chapter)\s*\d+", meta["content"], flags=re.I)[0]
    if not title:
        slug = novel_prefix(url).strip("/").split("/")[-1]
        title = slug.replace("-", " ").title()
    author = None
    for sel in ["[itemprop=author]", ".author", "a[href*=tac-gia]"]:
        el = soup.select_one(sel)
        if el and clean_text(el.get_text()):
            author = clean_text(el.get_text())
            author = re.sub(r"^tác giả\s*:?\s*", "", author, flags=re.I)
            break
    return clean_text(title), author


def find_next_url(soup, current_url):
    prefix = novel_prefix(current_url)
    host = urlparse(current_url).netloc

    def ok(href):
        if not href or href.startswith(("#", "javascript")):
            return None
        u = urljoin(current_url, href)
        p = urlparse(u)
        if p.netloc != host or not p.path.startswith(prefix) or not is_chapter_url(u):
            return None
        if u.rstrip("/") == current_url.rstrip("/"):
            return None
        return u.split("#")[0]

    # 1. <link rel=next> / <a rel=next>
    for el in soup.select("link[rel=next], a[rel=next]"):
        u = ok(el.get("href"))
        if u:
            return u
    # 2. anchors whose id/class says "next" or whose text says "Chương sau"
    for a in soup.find_all("a", href=True):
        attrs = " ".join([a.get("id", "")] + a.get("class", []))
        text = clean_text(a.get_text()) + " " + (a.get("title") or "")
        if NEXT_ATTR_RE.search(attrs) or NEXT_TEXT_RE.search(text):
            u = ok(a["href"])
            if u:
                return u
    # 3. any link in the novel to chapter N+1
    n = chapter_number(current_url)
    if n is not None:
        for a in soup.find_all("a", href=True):
            u = ok(a["href"])
            if u and chapter_number(u) == n + 1:
                return u
    return None


def collect_toc(fetcher, index_url):
    """Collect chapter links from a novel index page (follows ?page=N pagination)."""
    prefix = novel_prefix(index_url)
    host = urlparse(index_url).netloc
    seen, ordered = set(), []
    page_url, page = index_url, 1
    while page_url and page < 1000:
        print(f"[toc] {page_url}")
        h = fetcher.get(page_url)
        if not h:
            break
        soup = BeautifulSoup(h, "html.parser")
        new = 0
        for a in soup.find_all("a", href=True):
            u = urljoin(page_url, a["href"]).split("#")[0]
            p = urlparse(u)
            if p.netloc == host and p.path.startswith(prefix) and is_chapter_url(u) and u not in seen:
                seen.add(u)
                ordered.append(u)
                new += 1
        if new == 0:
            break
        page += 1
        nxt = None
        for a in soup.find_all("a", href=True):
            if clean_text(a.get_text()) == str(page) or re.search(rf"[?&/]page[=/-]?{page}\b", a["href"]):
                nxt = urljoin(page_url, a["href"])
                break
        page_url = nxt
    ordered.sort(key=lambda u: (chapter_number(u) is None, chapter_number(u) or 0))
    return ordered, BeautifulSoup(fetcher.get(index_url) or "", "html.parser")


# --------------------------------------------------------------------------- #
# EPUB writer (EPUB 3 with EPUB 2 NCX for older readers)
# --------------------------------------------------------------------------- #
CSS = """body{font-family:serif;line-height:1.6;margin:0 4%;}
h1{font-size:1.4em;text-align:center;margin:1.5em 0 1em;}
p{text-indent:1.5em;margin:0 0 .6em;text-align:justify;}
.title-page{text-align:center;margin-top:30%;}
.title-page h1{font-size:2em;} .title-page p{text-indent:0;text-align:center;}
"""


def xhtml(title, body, lang):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">
<head><meta charset="utf-8"/><title>{html.escape(title)}</title>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
{body}
</body>
</html>
"""


def write_epub(path, title, author, chapters, lang="vi", source=None):
    e = html.escape
    book_id = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, source or title))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    author = author or "Unknown"

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zc = zipfile.ZIP_DEFLATED
        z.writestr("META-INF/container.xml", """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""", compress_type=zc)
        z.writestr("OEBPS/style.css", CSS, compress_type=zc)

        src_line = f"<p>Nguồn: {e(source)}</p>" if source else ""
        z.writestr("OEBPS/title.xhtml", xhtml(title, f"""<div class="title-page">
<h1>{e(title)}</h1><p>{e(author)}</p><p>{len(chapters)} chương</p>{src_line}</div>""", lang),
                   compress_type=zc)

        manifest = ['<item id="css" href="style.css" media-type="text/css"/>',
                    '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                    '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                    '<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>']
        spine = ['<itemref idref="title"/>']
        nav_items, ncx_points = [], []

        for i, ch in enumerate(chapters, 1):
            fn = f"chap{i:05d}.xhtml"
            body = f"<h1>{e(ch['title'])}</h1>\n" + "\n".join(f"<p>{e(p)}</p>" for p in ch["paragraphs"])
            z.writestr(f"OEBPS/{fn}", xhtml(ch["title"], body, lang), compress_type=zc)
            manifest.append(f'<item id="c{i}" href="{fn}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
            nav_items.append(f'<li><a href="{fn}">{e(ch["title"])}</a></li>')
            ncx_points.append(f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{e(ch["title"])}</text></navLabel>'
                              f'<content src="{fn}"/></navPoint>')

        z.writestr("OEBPS/nav.xhtml", xhtml("Mục lục", f"""<nav epub:type="toc" id="toc"><h1>Mục lục</h1>
<ol>
{chr(10).join(nav_items)}
</ol></nav>""", lang), compress_type=zc)

        z.writestr("OEBPS/toc.ncx", f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{book_id}"/><meta name="dtb:depth" content="1"/>
<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>
<docTitle><text>{e(title)}</text></docTitle>
<navMap>
{chr(10).join(ncx_points)}
</navMap>
</ncx>""", compress_type=zc)

        z.writestr("OEBPS/content.opf", f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{book_id}</dc:identifier>
<dc:title>{e(title)}</dc:title>
<dc:creator>{e(author)}</dc:creator>
<dc:language>{lang}</dc:language>
<meta property="dcterms:modified">{now}</meta>
</metadata>
<manifest>
{chr(10).join(manifest)}
</manifest>
<spine toc="ncx">
{chr(10).join(spine)}
</spine>
</package>""", compress_type=zc)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def slugify(s):
    s = unicodedata.normalize("NFKD", s.replace("đ", "d").replace("Đ", "D"))
    s = s.encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower() or "novel"


def parse_chapter(page_html, url, selector):
    soup = BeautifulSoup(page_html, "html.parser")
    next_url = find_next_url(soup, url)
    n = chapter_number(url)
    title = find_title(soup, f"Chương {n}" if n else url)
    el = find_content(soup, selector)
    paras = content_to_paragraphs(el) if el else []
    # Drop a leading paragraph that just repeats the chapter title
    if paras and clean_text(paras[0]).lower() == title.lower():
        paras = paras[1:]
    novel_title, novel_author = find_novel_info(soup, url)
    return soup, {"url": url, "title": title, "paragraphs": paras, "next": next_url,
                  "novel_title": novel_title, "novel_author": novel_author}


def main():
    ap = argparse.ArgumentParser(description="Download a web novel into an EPUB file.")
    ap.add_argument("url", help="URL of the first chapter (or of the novel's index page)")
    ap.add_argument("-o", "--output", help="output .epub file (default: <novel-title>.epub)")
    ap.add_argument("-m", "--max", type=int, default=0, help="max number of chapters (0 = all)")
    ap.add_argument("-d", "--delay", type=float, default=1.0, help="seconds between requests (default 1)")
    ap.add_argument("--title", help="override the book title")
    ap.add_argument("--author", help="override the author")
    ap.add_argument("--selector", help="CSS selector of the chapter text, if auto-detect fails")
    ap.add_argument("--cache", help="cache folder (default: .cache/<novel-slug>)")
    ap.add_argument("--no-cache", action="store_true", help="always re-download chapters")
    ap.add_argument("--lang", default="vi", help="book language code (default vi)")
    args = ap.parse_args()

    fetcher = Fetcher(delay=args.delay)
    cache_dir = Path(args.cache or Path(".cache") / novel_prefix(args.url).strip("/").split("/")[-1])
    cache_dir.mkdir(parents=True, exist_ok=True)

    def load(url):
        key = cache_dir / (hashlib.md5(url.encode()).hexdigest() + ".json")
        if not args.no_cache and key.exists():
            return None, json.loads(key.read_text(encoding="utf-8"))
        h = fetcher.get(url)
        if h is None:
            return None, None
        soup, ch = parse_chapter(h, url, args.selector)
        key.write_text(json.dumps(ch, ensure_ascii=False), encoding="utf-8")
        return soup, ch

    title, author = args.title, args.author
    chapters = []

    if is_chapter_url(args.url):
        queue = None  # follow "next" links
        url = args.url
    else:
        queue, index_soup = collect_toc(fetcher, args.url)
        if not queue:
            sys.exit("No chapter links found on the index page. Pass the URL of chapter 1 instead.")
        print(f"[toc] found {len(queue)} chapters")
        t, a = find_novel_info(index_soup, args.url)
        title, author = title or t, author or a
        url = queue[0]

    visited = set()
    i = 0
    try:
        while url and url not in visited:
            visited.add(url)
            i += 1
            soup, ch = load(url)
            if ch is None:
                print(f"[{i}] {url} -> not found, stopping")
                break
            title = title or ch.get("novel_title")
            author = author or ch.get("novel_author")
            words = sum(len(p.split()) for p in ch["paragraphs"])
            print(f"[{i}] {ch['title']}  ({words} words){'  [cache]' if soup is None else ''}")
            if not ch["paragraphs"]:
                print("   ! no text found - try --selector", file=sys.stderr)
            chapters.append(ch)
            if args.max and len(chapters) >= args.max:
                break
            url = queue[i] if queue is not None and i < len(queue) else (ch["next"] if queue is None else None)
    except KeyboardInterrupt:
        print("\nInterrupted - writing what was downloaded so far.")

    if not chapters:
        sys.exit("Nothing downloaded.")

    title = title or "Novel"
    out = Path(args.output or f"{slugify(title)}.epub")
    write_epub(out, title, author, chapters, lang=args.lang, source=args.url)
    print(f"\nDone: {out}  ({len(chapters)} chapters, title: {title!r}, author: {author or 'Unknown'})")


if __name__ == "__main__":
    main()
