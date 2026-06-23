from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


STRUCTURE_VERSION = "v0.0.1"
WIKIPEDIA_DESCRIPTION = "Wikipedia page converted from a MediaWiki XML dump."
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


def page_output_filename(page: dict) -> str:
    title = str(page.get("title") or f"page_{page.get('page_id') or 'unknown'}")
    return f"{slugify(title)}_wikipedia.json"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_source_metadata(source_dir: Path, filename: str) -> None:
    metadata_path = source_dir / "metadata.json"
    metadata = read_json(metadata_path)
    order = list(metadata.get("order") or [])
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


def page_payload(page: dict, *, matched: bool, match_reason: str, movie_folder: str | None) -> dict:
    payload = dict(page)
    payload["ontology_match"] = {
        "match_status": "matched" if matched else "unmatched",
        "match_reason": match_reason,
        "movie_folder": movie_folder,
    }
    return payload


def write_page_to_ontology_layout(page: dict, root: str | Path) -> OntologyWriteResult:
    root_path = Path(root)
    candidate, match_reason = find_movie_match(page, root_path)
    filename = page_output_filename(page)

    if candidate is not None:
        source_dir = candidate.path / "raw_data" / "sources" / "wikipedia"
        target_path = source_dir / filename
        write_json(target_path, page_payload(page, matched=True, match_reason=match_reason, movie_folder=candidate.folder))
        write_source_metadata(source_dir, filename)
        return OntologyWriteResult(
            matched=True,
            movie_folder=candidate.folder,
            relative_path=str(target_path.relative_to(root_path)),
            match_reason=match_reason,
        )

    title = safe_component(str(page.get("title") or f"page_{page.get('page_id') or 'unknown'}")) or "unknown"
    page_root = root_path / "core" / "wiki_pages" / "__unsorted__" / title
    source_dir = page_root / "raw_data" / "sources" / "wikipedia"
    target_path = source_dir / filename
    write_json(page_root / "metadata.json", root_metadata(page, matched=False, match_reason=match_reason, movie_folder=None))
    write_json(page_root / "raw_data" / "metadata.json", {})
    write_json(target_path, page_payload(page, matched=False, match_reason=match_reason, movie_folder=None))
    write_source_metadata(source_dir, filename)
    return OntologyWriteResult(
        matched=False,
        movie_folder=None,
        relative_path=str(target_path.relative_to(root_path)),
        match_reason=match_reason,
    )
