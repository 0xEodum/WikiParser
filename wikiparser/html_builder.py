"""Рендер главы Wikipedia в простую HTML-страницу (один <h1> и один или несколько <p>).

Формат полностью повторяет mongo_to_s3/html_builder.py, чтобы вывод парсера
Wikipedia был структурно идентичен эталонному хранилищу: <!DOCTYPE html>,
<head> с charset и <title>, <body> с <h1> и блоками <p>.
"""
from __future__ import annotations

import re
from html import escape

PAGE_LANG = "en"

_PAGE = """<!DOCTYPE html>
<html lang="{lang}">

    <head>
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">

        <title>{title}</title>
    </head>

    <body>
        <h1>{title}</h1>

{body}
    </body>

</html>
"""

_PARAGRAPH = """        <p>
            {content}
        </p>
"""

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def split_paragraphs(text: str) -> list[str]:
    """Делит нормализованный текст главы на абзацы по пустым строкам.

    Внутри абзаца переносы строк схлопываются в пробелы, пустые блоки отбрасываются.
    """
    paragraphs: list[str] = []
    for block in _PARAGRAPH_SPLIT_RE.split(text or ""):
        collapsed = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if collapsed:
            paragraphs.append(collapsed)
    return paragraphs


def render_chapter(
    *,
    title: str,
    text: str,
    images: list[str] | None = None,
    lang: str = PAGE_LANG,
) -> str:
    """Строит HTML для одной главы: текст как набор <p>, картинки как <p><img></p>."""
    blocks = [_PARAGRAPH.format(content=escape(paragraph)) for paragraph in split_paragraphs(text)]
    for url in images or []:
        if not url:
            continue
        img = f'<img src="{escape(url, quote=True)}" alt="">'
        blocks.append(_PARAGRAPH.format(content=img))
    body = "\n".join(blocks)
    return _PAGE.format(lang=escape(lang, quote=True), title=escape(title), body=body)
