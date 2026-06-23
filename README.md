# Wiki Dump Converter

Convert Wikimedia XML/BZ2 dumps into chunked JSON files or an S3-like ontology layout.

Install from GitHub in Colab:

```python
%pip install "git+https://github.com/<OWNER>/<REPO>.git"
```

With S3 upload support:

```python
%pip install "wiki-dump-converter[s3] @ git+https://github.com/<OWNER>/<REPO>.git"
```

Single dump:

```bash
wiki-dump-converter enwiki-2026-04-01-p101063p101729.xml.bz2 --output-dir output/enwiki
```

Wikimedia catalog:

```bash
wiki-dump-converter "https://dumps.wikimedia.org/other/mediawiki_content_current/enwiki/2026-05-01/xml/bzip2/" --output-dir output/enwiki --pages-per-file 1000
```

Ontology layout for an existing S3-like movie tree:

```bash
wiki-dump-converter dump.xml.bz2 --output-dir D:/test/parse_mongo_to_s3/output --output-format ontology
```

Upload output after conversion:

```bash
wiki-dump-converter dump.xml.bz2 --output-dir output/enwiki --storage both --s3-cred-json D:/test/parse_mongo_to_s3/s3_cred.json --s3-prefix core/wiki
```
