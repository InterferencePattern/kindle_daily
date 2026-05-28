"""Probe whether trafilatura preserves <img> tags across output formats.

Fetches a handful of representative article URLs and runs
``trafilatura.extract`` with ``include_images=True`` against several
output formats. Reports, per (url, format) pair:

- Whether the output contains image markers at all
- The first ~3 image-bearing snippets so we can eyeball positioning
- The count of <img>/<graphic>/markdown-image tokens

This is a one-off diagnostic to decide whether inline image
preservation can use trafilatura's HTML output, or whether we need to
fall back to XML/markdown or custom DOM walking.
"""

from __future__ import annotations

import re
import sys

import trafilatura
from trafilatura.settings import use_config

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)
_CONFIG = use_config()
_CONFIG.set("DEFAULT", "USER_AGENTS", _BROWSER_UA)

URLS = [
    "https://www.theverge.com/2024/1/8/24029881/ces-2024-news-announcements-day-1",
    "https://arstechnica.com/science/2024/01/the-james-webb-telescopes-2023-was-just-as-amazing-as-its-2022/",
    "https://www.theatlantic.com/science/archive/2024/01/peter-higgs-physics-boson/677074/",
]

FORMATS = ["html", "xml", "markdown"]

# Patterns to count image markers per format
_PATTERNS = {
    "html": re.compile(r"<img\b", re.IGNORECASE),
    "xml": re.compile(r"<(graphic|img)\b", re.IGNORECASE),
    "markdown": re.compile(r"!\[[^\]]*\]\("),
}


def summarize(url: str) -> None:
    print(f"\n{'=' * 70}\nURL: {url}")
    downloaded = trafilatura.fetch_url(url, config=_CONFIG)
    if not downloaded:
        print("  fetch FAILED")
        return
    raw_imgs = len(re.findall(r"<img\b", downloaded, flags=re.IGNORECASE))
    print(f"  raw HTML <img> count: {raw_imgs}")

    for fmt in FORMATS:
        out = trafilatura.extract(
            downloaded,
            output_format=fmt,
            include_images=True,
            include_links=True,
            include_formatting=True,
            include_tables=True,
            favor_recall=True,
        )
        if not out:
            print(f"  [{fmt:8}] extract returned None")
            continue
        pattern = _PATTERNS[fmt]
        matches = pattern.findall(out)
        print(f"  [{fmt:8}] length={len(out):>6} img-markers={len(matches)}")
        if matches:
            # print first few image-bearing lines for eyeball check
            shown = 0
            for line in out.splitlines():
                if pattern.search(line):
                    snippet = line.strip()[:160]
                    print(f"             > {snippet}")
                    shown += 1
                    if shown >= 3:
                        break


def main(argv: list[str]) -> int:
    urls = argv[1:] if len(argv) > 1 else URLS
    for url in urls:
        try:
            summarize(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR on {url}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
