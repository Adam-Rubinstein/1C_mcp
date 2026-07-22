"""Load platform API index from shcntx_ru.hbk."""

from __future__ import annotations

import io
import pickle
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from .hbk_reader import HbkContainerReader
from .toc import Page, parse_toc


@dataclass
class Definition:
    name: str
    name_en: str
    kind: str  # method | property | type
    html_path: str
    parent_type: str = ""
    snippet: str = ""

    def to_markdown(self) -> str:
        lines = [f"### {self.name}", f"- **type**: `{self.kind}`"]
        if self.name_en and self.name_en != self.name:
            lines.append(f"- **en**: `{self.name_en}`")
        if self.parent_type:
            lines.append(f"- **member of**: `{self.parent_type}`")
        if self.html_path:
            lines.append(f"- **help**: `{self.html_path}`")
        if self.snippet:
            lines.append("")
            lines.append(self.snippet[:4000])
        return "\n".join(lines)


@dataclass
class PlatformIndex:
    methods: list[Definition] = field(default_factory=list)
    properties: list[Definition] = field(default_factory=list)
    types: list[Definition] = field(default_factory=list)
    by_key: dict[str, Definition] = field(default_factory=dict)
    zip_path: Path | None = None
    _zip_bytes: bytes | None = field(default=None, repr=False)

    def all(self) -> list[Definition]:
        return self.methods + self.properties + self.types


def find_hbk(platform_path: Path) -> Path:
    for p in platform_path.rglob("shcntx_ru.hbk"):
        if p.is_file():
            return p
    raise FileNotFoundError(f"shcntx_ru.hbk not found under {platform_path}")


def _inflate_pack_block(data: bytes) -> bytes:
    """ZipInputStream-style: read first local entry (HBK PackBlock has no central directory)."""
    import struct
    import zlib

    if data[:2] != b"PK":
        raise ValueError("PackBlock is not a zip local header")
    # local file header
    (
        _sig,
        _ver,
        flags,
        method,
        _time,
        _date,
        _crc,
        comp_size,
        _uncomp,
        name_len,
        extra_len,
    ) = struct.unpack_from("<IHHHHHIIIHH", data, 0)
    offset = 30 + name_len + extra_len
    # if bit 3 set, sizes are in data descriptor — then read until next header or use streaming inflate
    payload = data[offset:]
    if method == 0:
        return payload[:comp_size] if comp_size else payload
    if method == 8:
        # try with known size, else decompress all remaining (wbits for raw deflate = -15)
        try:
            if comp_size and not (flags & 0x08):
                return zlib.decompress(payload[:comp_size], -15)
            return zlib.decompress(payload, -15)
        except zlib.error:
            return zlib.decompress(payload, 15 + 32)  # gzip/zlib autodetect
    # fallback: stdlib zipfile if archive is complete
    bio = io.BytesIO(data)
    with zipfile.ZipFile(bio) as zf:
        return zf.read(zf.namelist()[0])


def _classify(path: str) -> str | None:
    p = path.lower().replace("\\", "/")
    if not p or p.endswith("/"):
        return None
    if "/methods/" in p or p.endswith("/methods"):
        return "method"
    if "/properties/" in p or "/property/" in p:
        return "property"
    if "/ctors/" in p or "/constructors/" in p:
        return "constructor"
    # type-like pages
    if any(x in p for x in ("/objects/", "/catalogs/", "/enums/", "/types/", "/object/")):
        return "type"
    if p.endswith(".html"):
        return "type"
    return None


def _walk(pages: list[Page], parent_type: str = "") -> list[Definition]:
    out: list[Definition] = []
    for page in pages:
        kind = _classify(page.html_path)
        name = (page.title_ru or page.title_en or "").strip()
        name_en = (page.title_en or "").strip()
        cur_parent = parent_type
        if kind == "type" and name:
            cur_parent = name
        if kind and name and page.html_path:
            # skip section headers without useful names
            if name not in ("Свойства", "Методы", "Конструкторы", "Properties", "Methods", "Constructors"):
                out.append(
                    Definition(
                        name=name,
                        name_en=name_en,
                        kind="method" if kind == "constructor" else kind,
                        html_path=page.html_path.lstrip("/"),
                        parent_type=parent_type if kind in ("method", "property", "constructor") else "",
                    )
                )
        out.extend(_walk(page.children, cur_parent))
    return out


def load_index(platform_path: Path, cache_dir: Path | None = None) -> PlatformIndex:
    hbk = find_hbk(platform_path)
    cache_dir = cache_dir or Path.home() / "AppData" / "Local" / "1c-mcp"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"index-{hbk.stat().st_mtime_ns}.pkl"
    # drop old caches for this prefix
    for old in cache_dir.glob("index-*.pkl"):
        if old != cache_file and old.is_file():
            try:
                old.unlink()
            except OSError:
                pass
    if cache_file.is_file():
        try:
            return pickle.loads(cache_file.read_bytes())
        except Exception:
            pass

    entities = HbkContainerReader().read_entities(hbk)
    pack = entities.get("PackBlock")
    storage = entities.get("FileStorage")
    if not pack or not storage:
        raise RuntimeError("HBK missing PackBlock or FileStorage")
    toc_bytes = _inflate_pack_block(pack)
    pages = parse_toc(toc_bytes)
    defs = _walk(pages)
    idx = PlatformIndex(_zip_bytes=storage)
    for d in defs:
        key = f"{d.kind}:{d.name.casefold()}"
        idx.by_key[key] = d
        if d.name_en:
            idx.by_key[f"{d.kind}:{d.name_en.casefold()}"] = d
        if d.kind == "method":
            idx.methods.append(d)
        elif d.kind == "property":
            idx.properties.append(d)
        else:
            idx.types.append(d)
    try:
        cache_file.write_bytes(pickle.dumps(idx, protocol=pickle.HIGHEST_PROTOCOL))
    except Exception:
        pass
    return idx


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_help(index: PlatformIndex, html_path: str) -> str:
    if not index._zip_bytes:
        return ""
    path = html_path.lstrip("/")
    with ZipFile(io.BytesIO(index._zip_bytes)) as zf:
        # try variants
        for candidate in (path, "/" + path, path.replace("\\", "/")):
            try:
                raw = zf.read(candidate.lstrip("/"))
                return _html_to_text(raw.decode("utf-8", errors="replace"))
            except KeyError:
                continue
        # case-insensitive search
        low = path.lower()
        for name in zf.namelist():
            if name.lower().endswith(low) or name.lower() == low:
                raw = zf.read(name)
                return _html_to_text(raw.decode("utf-8", errors="replace"))
    return ""


def search(index: PlatformIndex, query: str, kind: str | None, limit: int) -> list[Definition]:
    q = query.strip().casefold()
    if not q:
        return []
    pool = index.all()
    if kind in ("method", "property", "type"):
        pool = [d for d in pool if d.kind == kind]
    scored: list[tuple[int, Definition]] = []
    for d in pool:
        names = [d.name.casefold(), d.name_en.casefold()]
        score = 0
        for n in names:
            if not n:
                continue
            if n == q:
                score = max(score, 100)
            elif n.startswith(q):
                score = max(score, 80)
            elif q in n:
                score = max(score, 60)
            elif all(tok in n for tok in q.split() if tok):
                score = max(score, 40)
        if score:
            scored.append((score, d))
    scored.sort(key=lambda x: (-x[0], x[1].name))
    # unique by name+kind
    seen: set[str] = set()
    out: list[Definition] = []
    for _, d in scored:
        k = f"{d.kind}:{d.name}"
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
        if len(out) >= limit:
            break
    return out


def find_one(index: PlatformIndex, name: str, kind: str) -> Definition | None:
    key = f"{kind}:{name.casefold()}"
    if key in index.by_key:
        return index.by_key[key]
    # fallback scan
    for d in index.all():
        if d.kind == kind and (d.name.casefold() == name.casefold() or d.name_en.casefold() == name.casefold()):
            return d
    return None


def type_members(index: PlatformIndex, type_name: str) -> list[Definition]:
    t = find_one(index, type_name, "type")
    if not t:
        return []
    # members with parent_type match or html path under type folder
    folder = t.html_path.rsplit("/", 1)[0].casefold() if "/" in t.html_path else ""
    out: list[Definition] = []
    for d in index.methods + index.properties:
        if d.parent_type.casefold() == type_name.casefold():
            out.append(d)
        elif folder and d.html_path.casefold().startswith(folder + "/"):
            out.append(d)
    return out
