# taitruyen

Tải truyện chữ từ web (mỗi trang = 1 chương) và gộp thành file **EPUB**.
Download a web novel (one page per chapter) and bundle it into an EPUB ebook.

## Cài đặt / Install (Windows)

```bat
cd D:\work\taitruyen
pip install -r requirements.txt
```

## Sử dụng / Usage

```bat
python taitruyen.py https://www.tvtruyen.space/tu-tien-khi-nguoi-lam-mot-viec-den-cuc-han/chuong-1-hua-duong
```

The script starts at the given chapter, follows the "Chương sau" (next chapter) link until
the last chapter, then writes `<ten-truyen>.epub`.

The ebook contains a title page, a **Mục lục** (chapter list) page with a link to every
chapter, and then the chapters. Each chapter ends with an "↑ Mục lục" link back to the list.

| Option | Meaning |
|---|---|
| `-o FILE.epub` | output file name |
| `-m N` / `--max N` | download at most N chapters (good for testing: `-m 3`) |
| `--from N`, `--to N` | only chapters N to N (chapter numbers as written in the page address). Without `--to`: until the end |
| `--split N` | write one EPUB every N chapters, named `<ten-truyen>-0001-0100.epub`, `<ten-truyen>-0101-0200.epub`, ... |
| `--ignore FILE` | keyword file (default: `ignore.md` next to the script), see below |
| `--show-ignored` | print every line that was left out because of the keyword file |
| `-d SECONDS` | delay between requests (default 1s, be polite to the site) |
| `--title`, `--author` | override book title / author |
| `--selector CSS` | CSS selector of the chapter text if auto-detect picks the wrong block (e.g. `--selector "#chapter-c"`) |
| `--no-cache` | re-download everything |

You can also pass the novel's index page URL (without `/chuong-...`); the script then
collects the chapter list from that page.

## Một file mỗi N chương / One file every N chapters

```bat
python taitruyen.py URL-of-chapter-1 --from 501 --to 1000 --split 100
```

This writes 5 files of 100 chapters each (501-600, 601-700, ...). Leave out `--to` to go
until the last chapter. When the URL is a chapter page, the script still has to read the
chapters before `--from` to follow the "next chapter" links — instant if they are already
in the cache; otherwise pass the URL of the starting chapter itself.

## Bỏ dòng rác / Removing text added by the website

`ignore.md` holds one keyword per line (lines starting with `#` are comments). Any line of
a chapter that contains one of the keywords is left out of the EPUB. Upper/lower case does
not matter. The filter is applied when the EPUB is written, so after editing `ignore.md`
just run the same command again: nothing is downloaded again.

Choose keywords that cannot appear in the story itself (a site name, `https://`, ...):
the **whole line** is removed, even if the keyword is only a small part of it.

## Cache

Downloaded chapters are cached in `.cache\<novel-slug>\`. If the run stops (network
error, Ctrl+C), just run the same command again — cached chapters are not re-downloaded.
Ctrl+C also writes an EPUB with the chapters downloaded so far.
