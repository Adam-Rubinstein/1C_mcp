"""TOC tokenize/parse (port of Tokenizer + TocParser)."""

from __future__ import annotations

from dataclasses import dataclass, field


BOM = "\ufeff"


def tokenize(content: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    in_string = False
    i = 0
    while i < len(content):
        ch = content[i]
        if ch == BOM:
            pass
        elif ch == '"':
            if in_string:
                if i + 1 < len(content) and content[i + 1] == '"':
                    current.append('"')
                    i += 1
                else:
                    current.append(ch)
                    tokens.append("".join(current))
                    current = []
                    in_string = False
            else:
                if current:
                    tokens.append("".join(current).strip())
                    current = []
                current.append(ch)
                in_string = True
        elif in_string:
            current.append(ch)
        elif ch.isspace():
            if current:
                tokens.append("".join(current).strip())
                current = []
        elif ch in "{}":
            if current:
                tokens.append("".join(current).strip())
                current = []
            tokens.append(ch)
        elif ch == ",":
            if current:
                tokens.append("".join(current).strip())
                current = []
        else:
            current.append(ch)
        i += 1
    if current:
        tokens.append("".join(current).strip())
    return [t for t in tokens if t and t != ","]


@dataclass
class Page:
    title_en: str
    title_ru: str
    html_path: str
    children: list[Page] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.title_ru or self.title_en


class _It:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.i = 0

    def has(self) -> bool:
        return self.i < len(self.tokens)

    def peek(self) -> str:
        return self.tokens[self.i]

    def next(self) -> str:
        t = self.tokens[self.i]
        self.i += 1
        return t


def parse_toc(pack_block_text: bytes) -> list[Page]:
    content = pack_block_text.decode("utf-8", errors="replace")
    it = _It(tokenize(content))
    if not it.has() or it.next() != "{":
        raise ValueError("TOC: expected '{'")
    # chunk count
    it.next()
    pages_by_id: dict[int, Page] = {0: Page("TOC", "TOC", "")}
    while it.has() and it.peek() != "}":
        chunk = _parse_chunk(it)
        title_en, title_ru = chunk["titles"]
        page = Page(title_en, title_ru, chunk["html_path"])
        pages_by_id[chunk["id"]] = page
        parent = pages_by_id.get(chunk["parent_id"])
        if parent is not None:
            parent.children.append(page)
    return pages_by_id[0].children


def _parse_chunk(it: _It) -> dict:
    if it.next() != "{":
        raise ValueError("Chunk: expected '{'")
    cid = int(it.next())
    parent_id = int(it.next())
    child_count = int(it.next())
    for _ in range(child_count):
        it.next()
    # properties
    if it.next() != "{":
        raise ValueError("Props: expected '{'")
    it.next()  # n1
    it.next()  # n2
    titles = _parse_names(it)
    html_path = _parse_string(it).replace('"', "")
    if it.next() != "}":
        raise ValueError("Props: expected '}'")
    if it.next() != "}":
        raise ValueError("Chunk: expected '}'")
    return {"id": cid, "parent_id": parent_id, "titles": titles, "html_path": html_path}


def _parse_names(it: _It) -> tuple[str, str]:
    if it.next() != "{":
        raise ValueError("NameContainer: '{'")
    it.next()
    it.next()
    names: list[str] = []
    while it.has() and it.peek() != "}":
        if it.peek() == "{":
            names.append(_parse_name_object(it))
        else:
            break
    if it.next() != "}":
        raise ValueError("NameContainer: '}'")
    if not names:
        return "", ""
    if len(names) == 1:
        return names[0], ""
    # Kotlin: names[0]=ru, names[1]=en → DoubleLanguageString(en, ru)
    return names[1], names[0]


def _parse_name_object(it: _It) -> str:
    if it.next() != "{":
        raise ValueError("NameObject")
    _lang = _parse_string(it)
    name = _parse_string(it).replace('"', "")
    if it.next() != "}":
        raise ValueError("NameObject end")
    return name


def _parse_string(it: _It) -> str:
    t = it.next()
    if t.startswith('"') and t.endswith('"'):
        return t[1:-1]
    return t
