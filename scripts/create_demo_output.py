from __future__ import annotations

import json
import shutil
from pathlib import Path

from wikiparser.ontology_writer import finalize_wiki_system, write_page_to_ontology_layout


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = PROJECT_ROOT / "demo_output" / "ontology_preview"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def create_movie_seed(root: Path) -> None:
    movie_root = root / "core" / "movies" / "__unsorted__" / "Example page"
    info_dir = movie_root / "raw_data" / "sources" / "info"

    write_json(
        movie_root / "metadata.json",
        {
            "source_name": "Example page",
            "hash_id": "demo-movie-hash",
            "version": "v0.0.1",
            "source_url": "https://www.kinopoisk.ru/film/demo/",
        },
    )
    write_json(movie_root / "raw_data" / "metadata.json", {})
    write_text(info_dir / "Example_page_premiere_year.html", "<p>Premiere: 2012</p>")
    write_json(
        info_dir / "metadata.json",
        {
            "is_composite": False,
            "order": ["Example_page_premiere_year.html"],
            "description": "Demo movie metadata used to validate Wikipedia title plus year matching.",
        },
    )
    write_json(root / "core" / "movies" / "__system__" / "metadata.json", {"version": "v0.0.1"})
    write_json(root / "core" / "movies" / "__system__" / "raw_data" / "metadata.json", {"count_raw_entities": 1})


def create_wikipedia_pages(root: Path) -> None:
    matched_page = {
        "title": "Example page (2012 film)",
        "page_id": 42,
        "namespace": 0,
        "revision_id": 1001,
        "timestamp": "2026-05-01T12:00:00Z",
        "comment": "Reverted edit by ExampleUser",
        "contributor": "ExampleUser",
        "publication_year": 2012,
        "year_candidates": [2012],
        "chapters": {
            "Introduction": {
                "text": "A clean demo film article. File/thumb markup has already been removed from text.",
                "images": ["https://en.wikipedia.org/wiki/Special:FilePath/Demo.jpg"],
            }
        },
    }
    unmatched_page = {
        "title": "Mebbin National Park",
        "page_id": 101064,
        "namespace": 0,
        "revision_id": 1341482661,
        "timestamp": "2026-03-03T12:31:49Z",
        "comment": "Adding local short description",
        "contributor": "Entranced98",
        "publication_year": None,
        "year_candidates": [],
        "chapters": {
            "Introduction": {
                "text": "Mebbin is a national park located in New South Wales, Australia.",
                "images": [],
            }
        },
    }

    write_page_to_ontology_layout(matched_page, root, language_code=2)
    write_page_to_ontology_layout(unmatched_page, root, language_code=2)
    finalize_wiki_system(root)


def main() -> int:
    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)
    create_movie_seed(DEMO_ROOT)
    create_wikipedia_pages(DEMO_ROOT)
    print(f"Demo output written to: {DEMO_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
