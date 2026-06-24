from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .html_builder import render_chapter


STRUCTURE_VERSION = "v0.0.1"
WIKIPEDIA_DESCRIPTION = "Wikipedia page converted from a MediaWiki XML dump."
LEAD_CHAPTER_TITLE = "Introduction"

# Коды языков из lang_codes.json эталонного хранилища (0 = не определён).
LANG_NOT_DETERMINED = 0
REPRESENTATION_TYPE_PRIMARY = "1"
WIKI_LANGUAGE_CODES = {
    "en": 2, "ru": 1, "be": 5, "bg": 6, "zh": 7, "hr": 9, "cs": 10,
    "nl": 11, "et": 12, "fi": 13, "fr": 14, "de": 15, "he": 16, "id": 17,
    "it": 18, "ja": 19, "la": 20, "lv": 21, "no": 22, "pt": 24, "sk": 25,
    "sv": 26, "uk": 27,
}
WIKI_LANG_RE = re.compile(r"https?://([a-z-]+)\.wikipedia\.org", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(18[89]\d|19\d{2}|20\d{2})\b")


@dataclass(frozen=True)
class MovieCandidate:
    folder: str
    path: Path
    source_name: str
    years: tuple[int, ...]


@dataclass(frozen=True)
class OntologyWriteResult:
    matched: bool
    movie_folder: str | None
    relative_path: str
    match_reason: str


def safe_component(text: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join(" " if (ch in forbidden or ord(ch) < 32) else ch for ch in text)
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned.strip(" .")


def slugify(text: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in forbidden or ch.isspace() or ch in "()" else ch for ch in text)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "page"


def normalize_title(text: str) -> str:
    text = re.sub(r"\([^)]*\b(?:film|movie|фильм|мультфильм|телефильм)\b[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\([^)]*\b(?:18[89]\d|19\d{2}|20\d{2})\b[^)]*\)", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def extract_years(text: str) -> tuple[int, ...]:
    years: list[int] = []
    seen: set[int] = set()
    for match in YEAR_RE.finditer(text):
        year = int(match.group(1))
        if year in seen:
            continue
        seen.add(year)
        years.append(year)
    return tuple(years)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def candidate_years(movie_path: Path, metadata: dict) -> tuple[int, ...]:
    years: set[int] = set(extract_years(json.dumps(metadata, ensure_ascii=False)))
    info_dir = movie_path / "raw_data" / "sources" / "info"
    if info_dir.exists():
        for html_path in info_dir.glob("*.html"):
            if "прем" not in html_path.name.casefold() and "year" not in html_path.name.casefold():
                continue
            try:
                years.update(extract_years(html_path.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
    return tuple(sorted(years))


def normalize_title(text: str) -> str:
    text = re.sub(
        r"\([^)]*\b(?:film|movie|\u0444\u0438\u043b\u044c\u043c|\u043c\u0443\u043b\u044c\u0442\u0444\u0438\u043b\u044c\u043c|\u0442\u0435\u043b\u0435\u0444\u0438\u043b\u044c\u043c)\b[^)]*\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\([^)]*\b(?:18[89]\d|19\d{2}|20\d{2})\b[^)]*\)", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def candidate_years(movie_path: Path, metadata: dict) -> tuple[int, ...]:
    years: set[int] = set(extract_years(json.dumps(metadata, ensure_ascii=False)))
    info_dir = movie_path / "raw_data" / "sources" / "info"
    if info_dir.exists():
        for html_path in info_dir.glob("*.html"):
            name = html_path.name.casefold()
            if "\u043f\u0440\u0435\u043c" not in name and "year" not in name:
                continue
            try:
                years.update(extract_years(html_path.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
    return tuple(sorted(years))


def iter_movie_candidates(root: Path) -> Iterable[MovieCandidate]:
    movies_root = root / "core" / "movies" / "__unsorted__"
    if not movies_root.exists():
        return

    for movie_path in movies_root.iterdir():
        if not movie_path.is_dir():
            continue
        metadata = read_json(movie_path / "metadata.json")
        source_name = metadata.get("source_name") or movie_path.name
        yield MovieCandidate(
            folder=movie_path.name,
            path=movie_path,
            source_name=source_name,
            years=candidate_years(movie_path, metadata),
        )


def find_movie_match(page: dict, root: Path) -> tuple[MovieCandidate | None, str]:
    title = str(page.get("title") or "")
    page_years = set(page.get("year_candidates") or [])
    normalized_title = normalize_title(title)

    for candidate in iter_movie_candidates(root):
        if normalize_title(candidate.source_name) != normalized_title:
            continue
        if page_years and candidate.years and page_years.intersection(candidate.years):
            return candidate, "title_and_year"
    return None, "no_title_year_match"


def page_slug(page: dict) -> str:
    title = str(page.get("title") or f"page_{page.get('page_id') or 'unknown'}")
    return slugify(title)


def chapter_filename(prefix: str, chapter_title: str, used: set[str]) -> str:
    base = f"{prefix}_{slugify(chapter_title)}"
    filename = f"{base}.html"
    index = 2
    while filename in used:
        filename = f"{base}_{index}.html"
        index += 1
    used.add(filename)
    return filename


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_chapter_files(page: dict, source_dir: Path) -> list[str]:
    """Пишет по одному HTML-файлу на главу страницы и возвращает порядок файлов.

    Для вводной главы (lead) заголовок страницы используется как есть; для
    остальных глав — "<страница> — <глава>", чтобы сохранить контекст для NLP.
    """
    title = str(page.get("title") or f"page_{page.get('page_id') or 'unknown'}")
    prefix = page_slug(page)
    chapters = page.get("chapters") or {}

    used: set[str] = set()
    order: list[str] = []
    for chapter_title, chapter in chapters.items():
        data = chapter or {}
        heading = title if chapter_title == LEAD_CHAPTER_TITLE else f"{title} — {chapter_title}"
        filename = chapter_filename(prefix, chapter_title, used)
        write_text(
            source_dir / filename,
            render_chapter(
                title=heading,
                text=str(data.get("text") or ""),
                images=list(data.get("images") or []),
            ),
        )
        order.append(filename)

    if not order:
        filename = f"{prefix}.html"
        write_text(source_dir / filename, render_chapter(title=title, text=""))
        order.append(filename)

    return order


def write_source_metadata(source_dir: Path, filenames: list[str]) -> None:
    metadata_path = source_dir / "metadata.json"
    metadata = read_json(metadata_path)
    order = list(metadata.get("order") or [])
    for filename in filenames:
        if filename not in order:
            order.append(filename)
    write_json(
        metadata_path,
        {
            "is_composite": False,
            "order": order,
            "description": metadata.get("description") or WIKIPEDIA_DESCRIPTION,
        },
    )


def language_code_for(wiki_base_url: str | None) -> int:
    """Числовой код языка статьи по URL вики (enwiki -> 2, ruwiki -> 1, ...)."""
    if not wiki_base_url:
        return LANG_NOT_DETERMINED
    match = WIKI_LANG_RE.search(wiki_base_url)
    if not match:
        return LANG_NOT_DETERMINED
    return WIKI_LANGUAGE_CODES.get(match.group(1).lower(), LANG_NOT_DETERMINED)


def build_wiki_entity(page: dict, *, language_code: int = LANG_NOT_DETERMINED) -> dict:
    """Готовая к графу сущность по образцу mongo_to_s3/entity_builder.

    Тип статьи заранее неизвестен, поэтому иерархия (parent/grandparent) — null.
    representations содержит заголовок страницы с кодом языка вики.
    """
    title = page.get("title")
    return {
        "entity_name": title,
        "parent_name": None,
        "grandparent_name": None,
        "representations": [
            {
                "text": title,
                "representation_type": REPRESENTATION_TYPE_PRIMARY,
                "representation_language": language_code,
                "representation_weight": 1,
            }
        ],
    }


def finalize_wiki_system(root: str | Path) -> int:
    """Пишет core/wiki_pages/__system__ после обработки всех страниц.

    count_raw_entities считается по числу файлов в existing_entities, поэтому
    вызов идемпотентен и корректно накапливается при обходе нескольких архивов.
    Возвращает итоговый count_raw_entities.
    """
    root_path = Path(root)
    system_root = root_path / "core" / "wiki_pages" / "__system__"
    existing_dir = system_root / "raw_data" / "existing_entities"
    count = len(list(existing_dir.glob("*.json"))) if existing_dir.exists() else 0
    write_json(system_root / "metadata.json", {"version": STRUCTURE_VERSION})
    write_json(system_root / "raw_data" / "metadata.json", {"count_raw_entities": count})
    return count


def root_metadata(page: dict, *, matched: bool, match_reason: str, movie_folder: str | None) -> dict:
    return {
        "source_name": page.get("title"),
        "hash_id": str(uuid.uuid4()),
        "version": STRUCTURE_VERSION,
        "source_url": page.get("source_url"),
        "wikipedia_page_id": page.get("page_id"),
        "publication_year": page.get("publication_year"),
        "ontology_match": {
            "match_status": "matched" if matched else "unmatched",
            "match_reason": match_reason,
            "movie_folder": movie_folder,
        },
    }


def write_page_to_ontology_layout(
    page: dict, root: str | Path, *, language_code: int = LANG_NOT_DETERMINED
) -> OntologyWriteResult:
    root_path = Path(root)
    candidate, match_reason = find_movie_match(page, root_path)

    if candidate is not None:
        source_dir = candidate.path / "raw_data" / "sources" / "wikipedia"
        order = write_chapter_files(page, source_dir)
        write_source_metadata(source_dir, order)
        return OntologyWriteResult(
            matched=True,
            movie_folder=candidate.folder,
            relative_path=str(source_dir.relative_to(root_path)),
            match_reason=match_reason,
        )

    title = safe_component(str(page.get("title") or f"page_{page.get('page_id') or 'unknown'}")) or "unknown"
    page_root = root_path / "core" / "wiki_pages" / "__unsorted__" / title
    source_dir = page_root / "raw_data" / "sources" / "wikipedia"
    write_json(page_root / "metadata.json", root_metadata(page, matched=False, match_reason=match_reason, movie_folder=None))
    write_json(page_root / "raw_data" / "metadata.json", {})
    order = write_chapter_files(page, source_dir)
    write_source_metadata(source_dir, order)

    entity_path = (
        root_path / "core" / "wiki_pages" / "__system__" / "raw_data" / "existing_entities" / f"{title}.json"
    )
    write_json(entity_path, [build_wiki_entity(page, language_code=language_code)])

    return OntologyWriteResult(
        matched=False,
        movie_folder=None,
        relative_path=str(source_dir.relative_to(root_path)),
        match_reason=match_reason,
    )
