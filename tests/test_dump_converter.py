import bz2
import json
from pathlib import Path

from wikiparser.dump_converter import (
    convert_catalog,
    convert_dump,
    extract_images,
    extract_archive_links,
    extract_year_candidates,
    infer_wiki_base_url,
    main,
    parse_namespaces,
    parse_page_element,
    split_chapters,
)
from wikiparser.ontology_writer import (
    finalize_wiki_system,
    language_code_for,
    write_page_to_ontology_layout,
)
from wikiparser.s3_io import load_s3_config, normalize_endpoint, s3_key


SAMPLE_PAGE_XML = """<page>
  <title>Example page (2012 film)</title>
  <ns>0</ns>
  <id>42</id>
  <revision>
    <id>1001</id>
    <timestamp>2026-05-01T12:00:00Z</timestamp>
    <contributor>
      <username>ExampleUser</username>
      <id>7</id>
    </contributor>
    <comment>Reverted edit by [[Special:Contribs/~2026-19227-93|~2026-19227-93]] ([[User talk:~2026-19227-93|talk]])</comment>
    <text xml:space="preserve">{{Infobox film
| released = {{Film date|2012|5|4}}
}}
Lead paragraph with [[File:Lead image.jpg|thumb|left|Caption]] and clean text.

== History ==
History text with [[Image:Map of place.svg|thumb]] and a [[regular link]].

=== Details ===
Nested details.

== History ==
Second history section.</text>
  </revision>
</page>"""


def sample_dump_xml(*pages: str) -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
  <siteinfo>
    <sitename>Wikipedia</sitename>
    <base>https://en.wikipedia.org/wiki/Main_Page</base>
  </siteinfo>
  {pages}
</mediawiki>
""".format(pages="\n".join(pages))


def test_extract_images_builds_special_filepath_urls() -> None:
    images = extract_images(
        "[[File:Lead image.jpg|thumb]] [[Image:Map of place.svg|caption]]",
        "https://en.wikipedia.org/wiki/",
    )

    assert images == [
        "https://en.wikipedia.org/wiki/Special:FilePath/Lead_image.jpg",
        "https://en.wikipedia.org/wiki/Special:FilePath/Map_of_place.svg",
    ]


def test_extract_images_and_strip_text_support_ru_file_links() -> None:
    text = "[[\u0424\u0430\u0439\u043b:\u0422\u0435\u0441\u0442.jpg|\u043c\u0438\u043d\u0438|\u041f\u043e\u0434\u043f\u0438\u0441\u044c]] \u0427\u0438\u0441\u0442\u044b\u0439 \u0442\u0435\u043a\u0441\u0442"

    assert extract_images(text, "https://ru.wikipedia.org/wiki/") == [
        "https://ru.wikipedia.org/wiki/Special:FilePath/%D0%A2%D0%B5%D1%81%D1%82.jpg"
    ]
    assert split_chapters(text, wiki_base_url="https://ru.wikipedia.org/wiki/")["Introduction"]["text"] == "\u0427\u0438\u0441\u0442\u044b\u0439 \u0442\u0435\u043a\u0441\u0442"


def test_split_chapters_preserves_required_shape() -> None:
    chapters = split_chapters(
        "Lead paragraph.\n\n== History ==\nHistory text.\n=== Details ===\nMore.",
        wiki_base_url="https://en.wikipedia.org/wiki/",
        lead_title="Introduction",
    )

    assert list(chapters) == ["Introduction", "History", "Details"]
    assert chapters["Introduction"] == {"text": "Lead paragraph.", "images": []}
    assert chapters["History"]["text"] == "History text."


def test_parse_page_element_extracts_target_fields() -> None:
    import xml.etree.ElementTree as ET

    element = ET.fromstring(SAMPLE_PAGE_XML)

    page = parse_page_element(
        element,
        wiki_base_url="https://en.wikipedia.org/wiki/",
        lead_title="Introduction",
    )

    assert page["title"] == "Example page (2012 film)"
    assert page["page_id"] == 42
    assert page["namespace"] == 0
    assert page["revision_id"] == 1001
    assert page["timestamp"] == "2026-05-01T12:00:00Z"
    assert page["comment"] == "Reverted edit by ~2026-19227-93"
    assert page["contributor"] == "ExampleUser"
    assert page["publication_year"] == 2012
    assert page["year_candidates"] == [2012]
    assert set(page["chapters"]) == {"Introduction", "History", "Details", "History (2)"}
    assert "thumb|left" not in page["chapters"]["Introduction"]["text"]
    assert "Caption" not in page["chapters"]["Introduction"]["text"]
    assert page["chapters"]["Introduction"]["images"] == [
        "https://en.wikipedia.org/wiki/Special:FilePath/Lead_image.jpg"
    ]


def test_convert_dump_writes_chunked_json_arrays(tmp_path: Path) -> None:
    source = tmp_path / "sample.xml.bz2"
    source.write_bytes(bz2.compress(sample_dump_xml(SAMPLE_PAGE_XML, SAMPLE_PAGE_XML).encode("utf-8")))
    output_dir = tmp_path / "out"

    summary = convert_dump(
        source=str(source),
        output_dir=output_dir,
        pages_per_file=1,
        wiki_base_url="https://en.wikipedia.org/wiki/",
        lead_title="Introduction",
    )

    assert summary.pages_written == 2
    assert summary.files_written == 2
    first_file = output_dir / "pages-00001.json"
    second_file = output_dir / "pages-00002.json"
    assert first_file.exists()
    assert second_file.exists()
    assert json.loads(first_file.read_text(encoding="utf-8"))[0]["page_id"] == 42


def test_extract_year_candidates_uses_title_and_infobox() -> None:
    candidates = extract_year_candidates(
        "The Avengers (2012 film)",
        "{{Infobox film| released = {{Film date|2012|4|11}} }}",
    )

    assert candidates == [2012]


def test_extract_year_candidates_ignores_non_movie_dates() -> None:
    candidates = extract_year_candidates(
        "Mebbin National Park",
        "This park was added to the National Heritage List in 2007 and updated in 2025.",
    )

    assert candidates == []


def test_extract_archive_links_filters_wikimedia_index() -> None:
    html = """<html><body><pre>
    <a href="../">../</a>
    <a href="SHA256SUMS">SHA256SUMS</a>
    <a href="_SUCCESS">_SUCCESS</a>
    <a href="enwiki-2026-05-01-p10p1134785.xml.bz2">first</a>
    <a href="https://dumps.wikimedia.org/other/mediawiki_content_current/enwiki/file.xml.bz2">absolute</a>
    <a href="enwiki-2026-05-01-pages-meta-history.xml.bz2">history</a>
    </pre></body></html>"""

    links = extract_archive_links(
        "https://dumps.wikimedia.org/other/mediawiki_content_current/enwiki/2026-05-01/xml/bzip2/",
        html,
    )

    assert links == [
        "https://dumps.wikimedia.org/other/mediawiki_content_current/enwiki/2026-05-01/xml/bzip2/enwiki-2026-05-01-p10p1134785.xml.bz2",
        "https://dumps.wikimedia.org/other/mediawiki_content_current/enwiki/file.xml.bz2",
        "https://dumps.wikimedia.org/other/mediawiki_content_current/enwiki/2026-05-01/xml/bzip2/enwiki-2026-05-01-pages-meta-history.xml.bz2",
    ]


def test_convert_catalog_writes_each_archive_to_own_directory(tmp_path: Path, monkeypatch) -> None:
    first_source = tmp_path / "enwiki-2026-05-01-p1p2.xml.bz2"
    second_source = tmp_path / "enwiki-2026-05-01-p3p4.xml.bz2"
    first_source.write_bytes(bz2.compress(sample_dump_xml(SAMPLE_PAGE_XML, SAMPLE_PAGE_XML).encode("utf-8")))
    second_source.write_bytes(bz2.compress(sample_dump_xml(SAMPLE_PAGE_XML, SAMPLE_PAGE_XML).encode("utf-8")))
    output_dir = tmp_path / "catalog-out"

    monkeypatch.setattr(
        "wikiparser.dump_converter.list_catalog_archives",
        lambda _catalog_url: [str(first_source), str(second_source)],
    )

    summary = convert_catalog(
        catalog_url="https://dumps.wikimedia.org/other/mediawiki_content_current/enwiki/2026-05-01/xml/bzip2/",
        output_dir=output_dir,
        pages_per_file=1,
        wiki_base_url="https://en.wikipedia.org/wiki/",
        lead_title="Introduction",
        namespaces={0},
        limit=3,
    )

    first_output = output_dir / "enwiki-2026-05-01-p1p2" / "pages-00001.json"
    second_output = output_dir / "enwiki-2026-05-01-p3p4" / "pages-00001.json"
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))

    assert summary.archives_processed == 2
    assert summary.pages_written == 3
    assert first_output.exists()
    assert second_output.exists()
    assert len(json.loads(first_output.read_text(encoding="utf-8"))) == 1
    assert manifest["pages_written"] == 3
    assert [item["pages_written"] for item in manifest["archives"]] == [2, 1]


def test_convert_dump_filters_redirects_and_namespaces(tmp_path: Path) -> None:
    redirect_page = SAMPLE_PAGE_XML.replace(
        "<title>Example page (2012 film)</title>", "<title>Redirected</title>"
    ).replace(
        "<revision>",
        '<redirect title="Target" />\n  <revision>',
        1,
    )
    talk_page = SAMPLE_PAGE_XML.replace(
        "<title>Example page (2012 film)</title>", "<title>Talk page</title>"
    ).replace(
        "<ns>0</ns>",
        "<ns>1</ns>",
        1,
    )
    source = tmp_path / "sample.xml"
    source.write_text(sample_dump_xml(SAMPLE_PAGE_XML, redirect_page, talk_page), encoding="utf-8")
    output_dir = tmp_path / "filtered"

    summary = convert_dump(
        source=str(source),
        output_dir=output_dir,
        pages_per_file=10,
        wiki_base_url="https://en.wikipedia.org/wiki/",
        lead_title="Introduction",
        namespaces={0},
    )

    output = json.loads((output_dir / "pages-00001.json").read_text(encoding="utf-8"))
    assert summary.pages_written == 1
    assert output[0]["title"] == "Example page (2012 film)"


def test_parse_page_element_uses_ip_and_handles_empty_text() -> None:
    import xml.etree.ElementTree as ET

    page_xml = """<page>
      <title>IP page</title>
      <ns>0</ns>
      <id>5</id>
      <revision>
        <id>6</id>
        <timestamp>2026-05-01T00:00:00Z</timestamp>
        <contributor><ip>192.0.2.1</ip></contributor>
      </revision>
    </page>"""

    page = parse_page_element(
        ET.fromstring(page_xml),
        wiki_base_url="https://en.wikipedia.org/wiki/",
        lead_title="Introduction",
    )

    assert page["contributor"] == "192.0.2.1"
    assert page["chapters"] == {"Empty": {"text": "", "images": []}}


def test_helpers_validate_inputs_and_infer_wiki_urls(tmp_path: Path) -> None:
    assert infer_wiki_base_url("https://dumps.wikimedia.org/enwiki/2026/file.xml.bz2") == (
        "https://en.wikipedia.org/wiki/"
    )
    assert infer_wiki_base_url(
        "https://dumps.wikimedia.org/other/mediawiki_content_current/ruwiki/2026-06-01/xml/bzip2/"
    ) == "https://ru.wikipedia.org/wiki/"
    assert parse_namespaces("0, 10") == {0, 10}
    assert parse_namespaces("all") is None

    try:
        convert_dump(source=str(tmp_path / "missing.xml"), output_dir=tmp_path / "out")
    except FileNotFoundError as exc:
        assert "missing.xml" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")

    try:
        convert_dump(source=str(tmp_path / "missing.xml"), output_dir=tmp_path / "out", pages_per_file=0)
    except ValueError as exc:
        assert "pages_per_file" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_cli_returns_success_and_error_codes(tmp_path: Path, capsys) -> None:
    source = tmp_path / "sample.xml.bz2"
    source.write_bytes(bz2.compress(sample_dump_xml(SAMPLE_PAGE_XML).encode("utf-8")))
    output_dir = tmp_path / "cli-out"

    success_code = main(
        [
            str(source),
            "--output-dir",
            str(output_dir),
            "--pages-per-file",
            "1",
            "--limit",
            "1",
        ]
    )
    success_err = capsys.readouterr().err

    error_code = main([str(tmp_path / "missing.xml"), "--output-dir", str(tmp_path / "bad")])
    error_err = capsys.readouterr().err

    assert success_code == 0
    assert "Wrote 1 pages" in success_err
    assert error_code == 1
    assert "error:" in error_err


def test_write_page_to_ontology_layout_matches_movie_by_title_and_year(tmp_path: Path) -> None:
    movie_root = tmp_path / "core" / "movies" / "__unsorted__" / "Example page"
    info_dir = movie_root / "raw_data" / "sources" / "info"
    info_dir.mkdir(parents=True)
    (movie_root / "metadata.json").write_text(
        json.dumps({"source_name": "Example page", "version": "v0.0.1"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (info_dir / "Example_page_премьера.html").write_text("<p>Premiere: 2012</p>", encoding="utf-8")

    page = {
        "title": "Example page (2012 film)",
        "page_id": 42,
        "namespace": 0,
        "revision_id": 1001,
        "timestamp": "2026-05-01T12:00:00Z",
        "comment": "",
        "contributor": "ExampleUser",
        "publication_year": 2012,
        "year_candidates": [2012],
        "chapters": {"Introduction": {"text": "Clean text", "images": []}},
    }

    result = write_page_to_ontology_layout(page, tmp_path)

    wikipedia_dir = movie_root / "raw_data" / "sources" / "wikipedia"
    target_file = wikipedia_dir / "Example_page_2012_film_Introduction.html"
    source_meta = wikipedia_dir / "metadata.json"
    html = target_file.read_text(encoding="utf-8")
    metadata = json.loads(source_meta.read_text(encoding="utf-8"))

    assert result.matched
    assert result.movie_folder == "Example page"
    assert html.startswith("<!DOCTYPE html>")
    assert "<h1>Example page (2012 film)</h1>" in html
    assert "<p>\n            Clean text\n        </p>" in html
    assert metadata["is_composite"] is False
    assert metadata["order"] == ["Example_page_2012_film_Introduction.html"]
    # Matched-фильм принадлежит кластеру movies: existing_entity для него не пишется.
    assert not (tmp_path / "core" / "wiki_pages" / "__system__" / "raw_data" / "existing_entities").exists()


def test_language_code_for_resolves_wiki_url() -> None:
    assert language_code_for("https://en.wikipedia.org/wiki/") == 2
    assert language_code_for("https://ru.wikipedia.org/wiki/") == 1
    assert language_code_for("https://example.com/") == 0
    assert language_code_for(None) == 0


def test_unmatched_page_writes_existing_entity_and_system(tmp_path: Path) -> None:
    page = {
        "title": "Mebbin National Park",
        "page_id": 101064,
        "namespace": 0,
        "revision_id": 1341482661,
        "timestamp": "2026-03-03T12:31:49Z",
        "comment": "",
        "contributor": "Entranced98",
        "publication_year": None,
        "year_candidates": [],
        "chapters": {
            "Introduction": {"text": "Lead paragraph.", "images": []},
            "History": {"text": "Founded later.", "images": []},
        },
    }

    result = write_page_to_ontology_layout(page, tmp_path, language_code=2)
    count = finalize_wiki_system(tmp_path)

    system_root = tmp_path / "core" / "wiki_pages" / "__system__"
    entity_file = system_root / "raw_data" / "existing_entities" / "Mebbin National Park.json"
    entity = json.loads(entity_file.read_text(encoding="utf-8"))
    system_meta = json.loads((system_root / "raw_data" / "metadata.json").read_text(encoding="utf-8"))
    wikipedia_meta = json.loads(
        (
            tmp_path / "core" / "wiki_pages" / "__unsorted__" / "Mebbin National Park"
            / "raw_data" / "sources" / "wikipedia" / "metadata.json"
        ).read_text(encoding="utf-8")
    )

    assert not result.matched
    assert entity == [
        {
            "entity_name": "Mebbin National Park",
            "parent_name": None,
            "grandparent_name": None,
            "representations": [
                {
                    "text": "Mebbin National Park",
                    "representation_type": "1",
                    "representation_language": 2,
                    "representation_weight": 1,
                }
            ],
        }
    ]
    assert count == 1
    assert system_meta == {"count_raw_entities": 1}
    # Порядок глав сохраняется в order.
    assert wikipedia_meta["order"] == [
        "Mebbin_National_Park_Introduction.html",
        "Mebbin_National_Park_History.html",
    ]


def test_s3_config_supports_parse_mongo_credential_shape(tmp_path: Path) -> None:
    cred_path = tmp_path / "s3_cred.json"
    cred_path.write_text(
        json.dumps(
            {
                "url": "s3.example.test",
                "port": 9443,
                "reg": "ru-1",
                "bucket_name": "bucket",
                "access_key": "access",
                "secret_key": "secret",
            }
        ),
        encoding="utf-8",
    )

    config = load_s3_config(prefix="core/wiki", cred_json=cred_path)

    assert config.bucket == "bucket"
    assert config.prefix == "core/wiki"
    assert config.endpoint_url == "https://s3.example.test:9443"
    assert config.region_name == "ru-1"
    assert normalize_endpoint("https://s3.example.test") == "https://s3.example.test"
    assert s3_key("core/wiki", Path("pages-00001.json")) == "core/wiki/pages-00001.json"
