"""Read-only helper for enumerating links while researching source contracts."""

from __future__ import annotations

import argparse
import sys
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.tag_counts: dict[str, int] = {}
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        self.tag_counts[normalized_tag] = self.tag_counts.get(normalized_tag, 0) + 1
        if normalized_tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            self.links.append((text, self._href))
            self._href = None
            self._text = []


def fetch(url: str, timeout: float) -> tuple[str, str, str]:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.1",
            "User-Agent": "policy-website-tracker-research/0.1",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        return response.geturl(), content_type, response.read().decode(charset, errors="replace")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--documents-only", action="store_true")
    parser.add_argument(
        "--follow-prefix",
        help="follow each unique same-site link whose path begins with this prefix",
    )
    args = parser.parse_args()

    final_url, content_type, body = fetch(args.url, args.timeout)
    print(f"final_url\t{final_url}")
    print(f"content_type\t{content_type}")
    print(f"characters\t{len(body)}")
    if content_type not in {"text/html", "application/xhtml+xml"}:
        return 0

    link_parser = LinkParser()
    link_parser.feed(body)
    print(f"links\t{len(link_parser.links)}")
    document_links = sum(
        1
        for _, href in link_parser.links
        if urlparse(urljoin(final_url, href)).path.casefold().endswith(
            (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv")
        )
    )
    print(f"document_links\t{document_links}")
    for tag in ("h1", "h2", "h3", "h4", "table", "tr", "form", "select"):
        print(f"tag_{tag}\t{link_parser.tag_counts.get(tag, 0)}")
    if args.follow_prefix:
        followed: set[str] = set()
        for _, href in link_parser.links:
            child_url = urljoin(final_url, href)
            parsed_child = urlparse(child_url)
            if parsed_child.netloc != urlparse(final_url).netloc:
                continue
            if not parsed_child.path.startswith(args.follow_prefix) or child_url in followed:
                continue
            followed.add(child_url)
            time.sleep(1)
            child_final_url, child_content_type, child_body = fetch(child_url, args.timeout)
            child_parser = LinkParser()
            child_parser.feed(child_body)
            child_documents = sum(
                1
                for _, child_href in child_parser.links
                if urlparse(urljoin(child_final_url, child_href)).path.casefold().endswith(
                    (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv")
                )
            )
            print(
                "followed\t"
                f"{child_final_url}\t{child_content_type}\t"
                f"tables={child_parser.tag_counts.get('table', 0)}\t"
                f"rows={child_parser.tag_counts.get('tr', 0)}\t"
                f"document_links={child_documents}"
            )
        return 0
    if args.summary_only:
        return 0
    for text, href in link_parser.links:
        absolute_url = urljoin(final_url, href)
        if args.documents_only:
            path = urlparse(absolute_url).path.casefold()
            if not path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv")):
                continue
        print(f"{text}\t{absolute_url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"inventory probe failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
