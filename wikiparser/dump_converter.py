from __future__ import annotations

import argparse
import bz2
from html.parser import HTMLParser
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Sequence
from urllib.parse import quote, urljoin, urlparse

import mwparserfromhell
import requests

from .ontology_writer import finalize_wiki_system, language_code_for, write_page_to_ontology_layout
from .s3_io import load_s3_config, upload_directory_to_s3


IMAGE_ALIASES = ("file", "image", "файл", "изображение")
IMAGE_LINK_RE = re.compile(
    r"\[\[\s*(?P<prefix>File|Image|Файл|Изображение)\s*:\s*(?P<name>[^\]|#<>{}\n]+)",
    re.IGNORECASE,
)
MEDIA_LINK_RE = re.compile(r"\[\[\s*(?:File|Image|Файл|Изображение)\s*:[^\]]+\]\]", re.IGNORECASE)
TALK_PAREN_RE = re.compile(r"\s*\((?:talk|user talk|обсуждение|обс\.)\)", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(18[89]\d|19\d{2}|20\d{2})\b")
MOVIE_HINT_RE = re.compile(r"\b(film|movie|фильм|мультфильм|телефильм)\b|infobox\s+film", re.IGNORECASE)
IMAGE_ALIASES = ("file", "image", "\u0444\u0430\u0439\u043b", "\u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435")
IMAGE_LINK_RE = re.compile(
    r"\[\[\s*(?P<prefix>File|Image|\u0424\u0430\u0439\u043b|\u0418\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435)\s*:\s*(?P<name>[^\]|#<>{}\n]+)",
    re.IGNORECASE,
)
MEDIA_LINK_RE = re.compile(
    r"\[\[\s*(?:File|Image|\u0424\u0430\u0439\u043b|\u0418\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435)\s*:[^\]]+\]\]",
    re.IGNORECASE,
)
TALK_PAREN_RE = re.compile(
    r"\s*\((?:talk|user talk|\u043e\u0431\u0441\u0443\u0436\u0434\u0435\u043d\u0438\u0435|\u043e\u0431\u0441\.)\)",
    re.IGNORECASE,
)
MOVIE_HINT_RE = re.compile(
    r"\b(film|movie|\u0444\u0438\u043b\u044c\u043c|\u043c\u0443\u043b\u044c\u0442\u0444\u0438\u043b\u044c\u043c|\u0442\u0435\u043b\u0435\u0444\u0438\u043b\u044c\u043c)\b|infobox\s+film",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^(?P<marks>={2,6})\s*(?P<title>.*?)\s*(?P=marks)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ConversionSummary:
    pages_seen: int
    pages_written: int
    files_written: int
    output_dir: Path


@dataclass(frozen=True)
class ArchiveConversionSummary:
    source: str
    output_dir: Path
    pages_written: int
    files_written: int


@dataclass(frozen=True)
class CatalogConversionSummary:
    catalog_url: str
    archives_seen: int
    archives_processed: int
    pages_written: int
    files_written: int
    output_dir: Path
    archives: tuple[ArchiveConversionSummary, ...]


class ArchiveLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"}


def infer_wiki_base_url(source: str) -> str:
    parsed = urlparse(source)
    for segment in parsed.path.split("/"):
        match = re.fullmatch(r"([a-z][a-z-]*)wiki", segment, flags=re.IGNORECASE)
        if match:
            return f"https://{match.group(1).lower()}.wikipedia.org/wiki/"

    match = re.search(r"([a-z][a-z-]*)wiki", source, flags=re.IGNORECASE)
    if not match:
        return "https://en.wikipedia.org/wiki/"
    if match.group(1).lower() == "media":
        return "https://en.wikipedia.org/wiki/"
    return f"https://{match.group(1).lower()}.wikipedia.org/wiki/"


def is_catalog_source(source: str) -> bool:
    return is_url(source) and source.rstrip().endswith("/")


def archive_output_name(source: str) -> str:
    path = urlparse(source).path if is_url(source) else source
    name = Path(path).name
    for suffix in (".xml.bz2", ".bz2", ".xml"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name or "archive"


def extract_archive_links(catalog_url: str, html: str) -> list[str]:
    parser = ArchiveLinkParser()
    parser.feed(html)

    links: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        absolute_url = urljoin(catalog_url, href)
        path = urlparse(absolute_url).path.lower()
        if not path.endswith(".xml.bz2"):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        links.append(absolute_url)
    return links


def list_catalog_archives(catalog_url: str) -> list[str]:
    with requests.get(catalog_url, timeout=(15, 120)) as response:
        response.raise_for_status()
        return extract_archive_links(catalog_url, response.text)


@contextmanager
def open_dump_stream(source: str) -> Iterator[BinaryIO]:
    if is_url(source):
        with requests.get(source, stream=True, timeout=(15, 120)) as response:
            response.raise_for_status()
            response.raw.decode_content = True
            if source.lower().endswith(".bz2"):
                with bz2.BZ2File(response.raw) as stream:
                    yield stream
            else:
                yield response.raw
        return

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Input dump does not exist: {path}")

    if path.suffix.lower() == ".bz2":
        with bz2.open(path, "rb") as stream:
            yield stream
    else:
        with path.open("rb") as stream:
            yield stream


def strip_namespace(tag: str) -> str:
    if "}" not in tag:
        return tag
    return tag.rsplit("}", 1)[1]


def child_text(element: ET.Element, name: str, default: str = "") -> str:
    child = first_child(element, name)
    if child is None or child.text is None:
        return default
    return child.text


def first_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if strip_namespace(child.tag) == name:
            return child
    return None


def child_int(element: ET.Element, name: str, default: int = 0) -> int:
    value = child_text(element, name).strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def direct_children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if strip_namespace(child.tag) == name]


def normalize_image_name(name: str) -> str:
    return name.strip().replace(" ", "_")


def build_image_url(image_name: str, wiki_base_url: str) -> str:
    base = wiki_base_url.rstrip("/")
    normalized = normalize_image_name(image_name)
    return f"{base}/Special:FilePath/{quote(normalized, safe='')}"


def extract_images(wikitext: str, wiki_base_url: str) -> list[str]:
    seen: set[str] = set()
    images: list[str] = []

    for match in IMAGE_LINK_RE.finditer(wikitext):
        prefix = match.group("prefix").casefold()
        if prefix not in IMAGE_ALIASES:
            continue
        image_name = normalize_image_name(match.group("name"))
        if not image_name or image_name in seen:
            continue
        seen.add(image_name)
        images.append(build_image_url(image_name, wiki_base_url))

    return images


def remove_media_links(wikitext: str) -> str:
    return MEDIA_LINK_RE.sub("", wikitext)


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]

    cleaned_lines: list[str] = []
    previous_blank = False
    for line in lines:
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            if not previous_blank and cleaned_lines:
                cleaned_lines.append("")
            previous_blank = True
            continue
        cleaned_lines.append(line)
        previous_blank = False

    return "\n".join(cleaned_lines).strip()


def clean_wiki_text(wikitext: str, *, remove_media: bool = True) -> str:
    if not wikitext.strip():
        return ""

    source = remove_media_links(wikitext) if remove_media else wikitext
    parsed = mwparserfromhell.parse(source)
    text = parsed.strip_code(normalize=True, collapse=True)
    text = TALK_PAREN_RE.sub("", text)
    return normalize_whitespace(text)


def strip_wikitext(wikitext: str) -> str:
    return clean_wiki_text(wikitext, remove_media=True)


def extract_year_candidates(title: str, wikitext: str) -> list[int]:
    source = "\n".join([title or "", "\n".join(wikitext.splitlines()[:120])])
    if not MOVIE_HINT_RE.search(source):
        return []

    years: list[int] = []
    seen: set[int] = set()
    for match in YEAR_RE.finditer(source):
        year = int(match.group(1))
        if year in seen:
            continue
        seen.add(year)
        years.append(year)
    return years


def unique_title(title: str, chapters: OrderedDict[str, dict[str, object]]) -> str:
    clean_title = strip_wikitext(title).strip() or "Untitled"
    if clean_title not in chapters:
        return clean_title

    index = 2
    while f"{clean_title} ({index})" in chapters:
        index += 1
    return f"{clean_title} ({index})"


def split_chapters(
    wikitext: str,
    *,
    wiki_base_url: str,
    lead_title: str = "Introduction",
) -> OrderedDict[str, dict[str, object]]:
    chapters: OrderedDict[str, dict[str, object]] = OrderedDict()
    matches = list(HEADING_RE.finditer(wikitext))

    if not matches:
        text = strip_wikitext(wikitext)
        title = lead_title if text else "Empty"
        chapters[title] = {"text": text, "images": extract_images(wikitext, wiki_base_url)}
        return chapters

    lead = wikitext[: matches[0].start()].strip()
    if lead:
        chapters[lead_title] = {"text": strip_wikitext(lead), "images": extract_images(lead, wiki_base_url)}

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(wikitext)
        raw_title = match.group("title")
        body = wikitext[start:end].strip()
        title = unique_title(raw_title, chapters)
        chapters[title] = {"text": strip_wikitext(body), "images": extract_images(body, wiki_base_url)}

    return chapters


def contributor_name(revision: ET.Element | None) -> str:
    if revision is None:
        return ""

    contributor = first_child(revision, "contributor")
    if contributor is None:
        return ""

    username = child_text(contributor, "username").strip()
    if username:
        return username

    ip = child_text(contributor, "ip").strip()
    if ip:
        return ip

    contributor_id = child_text(contributor, "id").strip()
    return contributor_id


def parse_page_element(
    page: ET.Element,
    *,
    wiki_base_url: str,
    lead_title: str,
) -> dict[str, object]:
    revisions = direct_children(page, "revision")
    revision = revisions[-1] if revisions else None
    text_element = first_child(revision, "text") if revision is not None else None
    wikitext = text_element.text if text_element is not None and text_element.text is not None else ""
    title = clean_wiki_text(child_text(page, "title"), remove_media=False)
    year_candidates = extract_year_candidates(title, wikitext)

    return {
        "title": title,
        "page_id": child_int(page, "id"),
        "namespace": child_int(page, "ns"),
        "revision_id": child_int(revision, "id") if revision is not None else 0,
        "timestamp": child_text(revision, "timestamp") if revision is not None else "",
        "comment": clean_wiki_text(child_text(revision, "comment"), remove_media=False) if revision is not None else "",
        "contributor": contributor_name(revision),
        "publication_year": year_candidates[0] if year_candidates else None,
        "year_candidates": year_candidates,
        "chapters": split_chapters(wikitext, wiki_base_url=wiki_base_url, lead_title=lead_title),
    }


def page_has_redirect(page: ET.Element) -> bool:
    return first_child(page, "redirect") is not None


def iter_dump_pages(
    stream: BinaryIO,
    *,
    wiki_base_url: str,
    lead_title: str,
    namespaces: set[int] | None = None,
    skip_redirects: bool = True,
    limit: int | None = None,
) -> Iterator[dict[str, object]]:
    emitted = 0

    for _event, element in ET.iterparse(stream, events=("end",)):
        if strip_namespace(element.tag) != "page":
            continue

        namespace = child_int(element, "ns")
        should_emit = (namespaces is None or namespace in namespaces) and not (skip_redirects and page_has_redirect(element))
        if should_emit:
            yield parse_page_element(element, wiki_base_url=wiki_base_url, lead_title=lead_title)
            emitted += 1

        element.clear()
        if limit is not None and emitted >= limit:
            break


def parse_namespaces(raw_namespaces: str | None) -> set[int] | None:
    if raw_namespaces is None or raw_namespaces.strip().lower() in {"", "all", "*"}:
        return None

    namespaces: set[int] = set()
    for part in raw_namespaces.split(","):
        value = part.strip()
        if not value:
            continue
        namespaces.add(int(value))
    return namespaces


def write_json_array(path: Path, pages: Sequence[dict[str, object]]) -> None:
    path.write_text(json.dumps(list(pages), ensure_ascii=False, indent=2), encoding="utf-8")


def flush_chunk(output_dir: Path, chunk_index: int, pages: Sequence[dict[str, object]]) -> Path:
    output_path = output_dir / f"pages-{chunk_index:05d}.json"
    write_json_array(output_path, pages)
    return output_path


def convert_dump(
    *,
    source: str,
    output_dir: str | Path,
    pages_per_file: int = 1000,
    wiki_base_url: str | None = None,
    lead_title: str = "Introduction",
    namespaces: set[int] | None = None,
    skip_redirects: bool = True,
    limit: int | None = None,
    output_format: str = "json",
    on_page: Callable[[dict[str, object]], None] | None = None,
) -> ConversionSummary:
    if pages_per_file < 1:
        raise ValueError("pages_per_file must be >= 1")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1 when provided")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    base_url = wiki_base_url or infer_wiki_base_url(source)
    language_code = language_code_for(base_url)

    chunk: list[dict[str, object]] = []
    pages_written = 0
    files_written = 0

    with open_dump_stream(source) as stream:
        for page in iter_dump_pages(
            stream,
            wiki_base_url=base_url,
            lead_title=lead_title,
            namespaces=namespaces,
            skip_redirects=skip_redirects,
            limit=limit,
        ):
            pages_written += 1
            if on_page is not None:
                on_page(page)

            if output_format == "ontology":
                write_page_to_ontology_layout(page, output_path, language_code=language_code)
                files_written += 1
                continue

            chunk.append(page)
            if len(chunk) >= pages_per_file:
                files_written += 1
                flush_chunk(output_path, files_written, chunk)
                chunk = []

    if output_format != "ontology" and chunk:
        files_written += 1
        flush_chunk(output_path, files_written, chunk)

    if output_format == "ontology":
        finalize_wiki_system(output_path)

    return ConversionSummary(
        pages_seen=pages_written,
        pages_written=pages_written,
        files_written=files_written,
        output_dir=output_path,
    )


def write_catalog_manifest(summary: CatalogConversionSummary) -> None:
    payload = {
        "catalog_url": summary.catalog_url,
        "archives_seen": summary.archives_seen,
        "archives_processed": summary.archives_processed,
        "pages_written": summary.pages_written,
        "files_written": summary.files_written,
        "archives": [
            {
                "source": archive.source,
                "output_dir": str(archive.output_dir),
                "pages_written": archive.pages_written,
                "files_written": archive.files_written,
            }
            for archive in summary.archives
        ],
    }
    (summary.output_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def convert_catalog(
    *,
    catalog_url: str,
    output_dir: str | Path,
    pages_per_file: int = 1000,
    wiki_base_url: str | None = None,
    lead_title: str = "Introduction",
    namespaces: set[int] | None = None,
    skip_redirects: bool = True,
    limit: int | None = None,
    archive_limit: int | None = None,
    output_format: str = "json",
    on_page: Callable[[dict[str, object]], None] | None = None,
    on_archive: Callable[[str, int, int], None] | None = None,
) -> CatalogConversionSummary:
    if archive_limit is not None and archive_limit < 1:
        raise ValueError("archive_limit must be >= 1 when provided")

    archives = list_catalog_archives(catalog_url)
    if not archives:
        raise ValueError(f"No .xml.bz2 archives found in catalog: {catalog_url}")

    selected_archives = archives[:archive_limit] if archive_limit is not None else archives
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    base_url = wiki_base_url or infer_wiki_base_url(catalog_url)

    archive_summaries: list[ArchiveConversionSummary] = []
    pages_written = 0
    files_written = 0

    for archive_index, archive_source in enumerate(selected_archives, start=1):
        if limit is not None and pages_written >= limit:
            break

        remaining_limit = None if limit is None else limit - pages_written
        if remaining_limit is not None and remaining_limit < 1:
            break

        if on_archive is not None:
            on_archive(archive_source, archive_index, len(selected_archives))

        archive_output_dir = output_path if output_format == "ontology" else output_path / archive_output_name(archive_source)
        summary = convert_dump(
            source=archive_source,
            output_dir=archive_output_dir,
            pages_per_file=pages_per_file,
            wiki_base_url=base_url,
            lead_title=lead_title,
            namespaces=namespaces,
            skip_redirects=skip_redirects,
            limit=remaining_limit,
            output_format=output_format,
            on_page=on_page,
        )
        archive_summaries.append(
            ArchiveConversionSummary(
                source=archive_source,
                output_dir=summary.output_dir,
                pages_written=summary.pages_written,
                files_written=summary.files_written,
            )
        )
        pages_written += summary.pages_written
        files_written += summary.files_written

    catalog_summary = CatalogConversionSummary(
        catalog_url=catalog_url,
        archives_seen=len(archives),
        archives_processed=len(archive_summaries),
        pages_written=pages_written,
        files_written=files_written,
        output_dir=output_path,
        archives=tuple(archive_summaries),
    )
    write_catalog_manifest(catalog_summary)
    return catalog_summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a MediaWiki XML dump (.xml/.xml.bz2, local file/URL) or a Wikimedia bzip2 catalog URL "
            "to chunked JSON files."
        ),
    )
    parser.add_argument("source", help="Local dump path, dump URL, or catalog URL ending with /")
    parser.add_argument("-o", "--output-dir", default="output/wiki_json", help="Directory for pages-*.json files")
    parser.add_argument("--pages-per-file", type=int, default=1000, help="Number of pages in each JSON file")
    parser.add_argument("--wiki-base-url", default=None, help="Example: https://en.wikipedia.org/wiki/")
    parser.add_argument("--lead-title", default="Introduction", help="Chapter name for text before the first heading")
    parser.add_argument("--namespaces", default="0", help="Comma-separated namespaces, or all. Default: 0")
    parser.add_argument("--include-redirects", action="store_true", help="Do not skip redirect pages")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N emitted pages; useful for smoke tests")
    parser.add_argument("--catalog", action="store_true", help="Treat source as a Wikimedia directory listing")
    parser.add_argument("--archive-limit", type=int, default=None, help="Process at most N archives from a catalog")
    parser.add_argument(
        "--output-format",
        choices=("json", "ontology"),
        default="json",
        help="json writes pages-*.json chunks; ontology writes S3-like core/* metadata layout",
    )
    parser.add_argument(
        "--storage",
        choices=("local", "s3", "both"),
        default="local",
        help="local keeps files on disk; s3/both upload the output directory after conversion",
    )
    parser.add_argument("--s3-bucket", default=None, help="Target S3 bucket for --storage s3/both")
    parser.add_argument("--s3-prefix", default="", help="Prefix inside the S3 bucket")
    parser.add_argument("--s3-endpoint-url", default=None, help="Custom S3 endpoint URL")
    parser.add_argument("--s3-region", default=None, help="S3 region name")
    parser.add_argument("--s3-cred-json", default=None, help="Credential JSON with bucket_name/url/reg/access_key/secret_key")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        if args.catalog or is_catalog_source(args.source):
            catalog_summary = convert_catalog(
                catalog_url=args.source,
                output_dir=args.output_dir,
                pages_per_file=args.pages_per_file,
                wiki_base_url=args.wiki_base_url,
                lead_title=args.lead_title,
                namespaces=parse_namespaces(args.namespaces),
                skip_redirects=not args.include_redirects,
                limit=args.limit,
                archive_limit=args.archive_limit,
                output_format=args.output_format,
            )
            if args.storage in {"s3", "both"}:
                s3_summary = upload_directory_to_s3(
                    catalog_summary.output_dir,
                    load_s3_config(
                        bucket=args.s3_bucket,
                        prefix=args.s3_prefix,
                        endpoint_url=args.s3_endpoint_url,
                        region_name=args.s3_region,
                        cred_json=args.s3_cred_json,
                    ),
                )
                print(
                    f"Uploaded {s3_summary.files_uploaded} file(s) to s3://{s3_summary.bucket}/{s3_summary.prefix}",
                    file=sys.stderr,
                )
            print(
                (
                    f"Wrote {catalog_summary.pages_written} pages from "
                    f"{catalog_summary.archives_processed}/{catalog_summary.archives_seen} archive(s) "
                    f"into {catalog_summary.files_written} JSON file(s): {catalog_summary.output_dir}"
                ),
                file=sys.stderr,
            )
            return 0

        summary = convert_dump(
            source=args.source,
            output_dir=args.output_dir,
            pages_per_file=args.pages_per_file,
            wiki_base_url=args.wiki_base_url,
            lead_title=args.lead_title,
            namespaces=parse_namespaces(args.namespaces),
            skip_redirects=not args.include_redirects,
            limit=args.limit,
            output_format=args.output_format,
        )
        if args.storage in {"s3", "both"}:
            s3_summary = upload_directory_to_s3(
                summary.output_dir,
                load_s3_config(
                    bucket=args.s3_bucket,
                    prefix=args.s3_prefix,
                    endpoint_url=args.s3_endpoint_url,
                    region_name=args.s3_region,
                    cred_json=args.s3_cred_json,
                ),
            )
            print(
                f"Uploaded {s3_summary.files_uploaded} file(s) to s3://{s3_summary.bucket}/{s3_summary.prefix}",
                file=sys.stderr,
            )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {summary.pages_written} pages into {summary.files_written} JSON file(s): {summary.output_dir}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
