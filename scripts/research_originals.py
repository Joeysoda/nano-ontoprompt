"""Rebuild the manifest of research originals (screenshots, PDFs, videos) kept out of git.

    python -I scripts/research_originals.py

Originals live in docs/research/<official|unofficial>/evidence/<topic>/.  A topic
folder records where it came from in source.json (or, for downloaded videos, in
the yt-dlp *.info.json).  This writes two committed files so the team knows what
exists even without the binaries:

* docs/research/ORIGINALS.md   - one row per original: source, date, size, notes citing it
* docs/research/originals.sha256 - checksums; verify a copy with
  ``cd docs/research && sha256sum -c originals.sha256``
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1] / "docs" / "research"
LEVELS = ("official", "unofficial")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def describe(folder: Path) -> dict | None:
    """Source metadata of a topic folder, or None when it records none."""
    if (folder / "source.json").exists():
        return json.loads((folder / "source.json").read_text(encoding="utf-8"))
    for info in sorted(folder.glob("*.info.json")):
        data = json.loads(info.read_text(encoding="utf-8"))
        date = data.get("upload_date") or ""
        return {"title": data.get("title", folder.name), "url": data.get("webpage_url"), "level": "官方视频" if "palantir" in str(data.get("channel", "")).lower() else "视频",
                "obtained": "", "published": f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else "", "provider": data.get("channel", "")}
    return None


def entries(level: str) -> list[tuple[Path, dict | None, list[Path]]]:
    """Topic folders under evidence/; a folder without metadata whose subfolders have it is a collection."""
    root = RESEARCH / level / "evidence"
    found = []

    def walk(folder: Path):
        meta = describe(folder)
        children = [path for path in sorted(folder.iterdir()) if path.is_dir()]
        if meta is None and any(describe(child) for child in children):
            for child in children:
                walk(child)
            loose = [path for path in sorted(folder.iterdir()) if path.is_file()]
            if loose:
                found.append((folder, None, loose))
            return
        files = sorted(path for path in folder.rglob("*") if path.is_file() and path.name != "source.json")
        found.append((folder, meta, files))

    if root.exists():
        for folder in sorted(path for path in root.iterdir() if path.is_dir()):
            walk(folder)
    return found


def citing_notes(folder: Path) -> list[str]:
    relative = folder.relative_to(RESEARCH / folder.relative_to(RESEARCH).parts[0]).as_posix()
    notes = []
    for note in sorted(RESEARCH.rglob("*.md")):
        if "evidence" in note.relative_to(RESEARCH).parts or note.name in ("README.md", "ORIGINALS.md"):
            continue
        text = note.read_text(encoding="utf-8")
        if relative in text or folder.name in text:   # notes cite videos by id
            notes.append(note.relative_to(RESEARCH).as_posix())
    return notes


def size(total: int) -> str:
    return f"{total / 1048576:.1f} MB" if total >= 1048576 else f"{total / 1024:.0f} KB"


def main():
    rows, sums, missing = [], [], []
    total_files = total_bytes = 0
    for level in LEVELS:
        for folder, meta, files in entries(level):
            path = folder.relative_to(RESEARCH).as_posix()
            nbytes = sum(item.stat().st_size for item in files)
            total_files, total_bytes = total_files + len(files), total_bytes + nbytes
            sums += [f"{sha256(item)}  {item.relative_to(RESEARCH).as_posix()}" for item in files]
            if meta is None:
                missing.append(path)
                meta = {}
            url = meta.get("url")
            source = f"[{meta.get('provider') or '链接'}]({url})" if url else (meta.get("provider") or "—")
            date = " / ".join(part for part in (f"发布 {meta['published']}" if meta.get("published") else "", f"获取 {meta['obtained']}" if meta.get("obtained") else "") if part) or "—"
            notes = " ".join(f"[{name.split('/')[-1]}]({name})" for name in (meta.get("notes") and [f"{level}/{n}" for n in meta["notes"]] or citing_notes(folder))) or "**未被任何笔记引用**"
            title = (meta.get("title") or "**缺少来源记录**").replace("|", "/")
            rows.append(f"| `{path}/` | {title} | {meta.get('level', '—')} | {source} | {date} | {len(files)} 个 · {size(nbytes)} | {notes} |")
    lines = [
        "# 调研原件清单",
        "",
        "> 由 `python -I scripts/research_originals.py` 生成，不要手改。原件本身（图片、PDF、视频）只在本地，不进 git；",
        "> 本清单和 [`originals.sha256`](originals.sha256) 进 git，让组员知道有哪些原件、来源在哪、被哪些笔记引用。",
        "> 拿到原件后核对完整性：`cd docs/research && sha256sum -c originals.sha256`。",
        "",
        f"共 {len(rows)} 组、{total_files} 个文件、{size(total_bytes)}。",
        "",
        "| 位置 | 内容 | 等级 | 来源 | 日期 | 文件 | 引用它的笔记 |",
        "|---|---|---|---|---|---|---|",
        *rows,
    ]
    if missing:
        lines += ["", "**缺少来源记录（请补 `source.json`）**：" + "、".join(f"`{path}/`" for path in missing)]
    # LF endings so `sha256sum -c` works on every platform.
    (RESEARCH / "ORIGINALS.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (RESEARCH / "originals.sha256").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(rows)} groups, {total_files} files, {size(total_bytes)}; missing source: {missing}")


if __name__ == "__main__":
    main()
