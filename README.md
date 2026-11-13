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
| `-d SECONDS` | delay between requests (default 1s, be polite to the site) |
| `--title`, `--author` | override book title / author |
| `--selector CSS` | CSS selector of the chapter text if auto-detect picks the wrong block (e.g. `--selector "#chapter-c"`) |
| `--no-cache` | re-download everything |

You can also pass the novel's index page URL (without `/chuong-...`); the script then
collects the chapter list from that page.

Downloaded chapters are cached in `.cache\<novel-slug>\`. If the run stops (network
error, Ctrl+C), just run the same command again — cached chapters are not re-downloaded.
Ctrl+C also writes an EPUB with the chapters downloaded so far.
