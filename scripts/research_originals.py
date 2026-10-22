"""Manifest and tidiness check for research originals (screenshots, PDFs, videos) kept out of git.

    python -I scripts/research_originals.py            # rebuild ORIGINALS.md and originals.sha256
    python -I scripts/research_originals.py --check    # exit 1 and list problems if docs/research is untidy
    python -I scripts/research_originals.py --hook     # Claude Code Stop hook: block until tidy

Originals live in docs/research/<official|unofficial>/evidence/<topic>/.  A topic
folder records where it came from in source.json (or, for downloaded videos, in
the yt-dlp *.info.json).  Two committed files let the team see what exists even
without the binaries:

* docs/research/ORIGINALS.md     - one row per original: source, date, size, notes citing it
* docs/research/originals.sha256 - checksums; verify a copy with
  ``cd docs/research && sha256sum -c originals.sha256``

The hook also catches images pasted into the current Claude Code session that
were never archived.  An image that is not research material (a bug
screenshot, say) is acknowledged by adding its sha256 and a reason to
docs/research/.ignored-pastes (local, not committed).
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1] / "docs" / "research"
LEVELS = ("official", "unofficial")
GENERATED = ("ORIGINALS.md", "originals.sha256")
IGNORED_PASTES = RESEARCH / ".ignored-pastes"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


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


def notes() -> list[Path]:
    """Research notes: markdown outside evidence/, other than the index and the generated manifest."""
    return [path for path in sorted(RESEARCH.rglob("*.md"))
            if "evidence" not in path.relative_to(RESEARCH).parts and path.name not in ("README.md", "ORIGINALS.md")]


def citing_notes(folder: Path) -> list[str]:
    relative = folder.relative_to(RESEARCH / folder.relative_to(RESEARCH).parts[0]).as_posix()
    cited = []
    for note in notes():
        text = note.read_text(encoding="utf-8")
        if relative in text or folder.name in text:   # notes cite videos by id
            cited.append(note.relative_to(RESEARCH).as_posix())
    return cited


def size(total: int) -> str:
    return f"{total / 1048576:.1f} MB" if total >= 1048576 else f"{total / 1024:.0f} KB"


def build(with_hashes: bool = True) -> tuple[str, list[str], list[str]]:
    """Manifest text, checksum lines (or bare paths without hashes) and folders missing a source."""
    rows, sums, missing = [], [], []
    total_files = total_bytes = 0
    for level in LEVELS:
        for folder, meta, files in entries(level):
            path = folder.relative_to(RESEARCH).as_posix()
            nbytes = sum(item.stat().st_size for item in files)
            total_files, total_bytes = total_files + len(files), total_bytes + nbytes
            sums += [f"{sha256(item)}  {item.relative_to(RESEARCH).as_posix()}" if with_hashes else item.relative_to(RESEARCH).as_posix() for item in files]
            if meta is None:
                missing.append(path)
                meta = {}
            url = meta.get("url")
            source = f"[{meta.get('provider') or '链接'}]({url})" if url else (meta.get("provider") or "—")
            date = " / ".join(part for part in (f"发布 {meta['published']}" if meta.get("published") else "", f"获取 {meta['obtained']}" if meta.get("obtained") else "") if part) or "—"
            notes_cell = " ".join(f"[{name.split('/')[-1]}]({name})" for name in (meta.get("notes") and [f"{level}/{n}" for n in meta["notes"]] or citing_notes(folder))) or "**未被任何笔记引用**"
            title = (meta.get("title") or "**缺少来源记录**").replace("|", "/")
            rows.append(f"| `{path}/` | {title} | {meta.get('level', '—')} | {source} | {date} | {len(files)} 个 · {size(nbytes)} | {notes_cell} |")
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
    return "\n".join(lines) + "\n", sums, missing


def problems(full: bool) -> list[str]:
    """What makes docs/research untidy.  ``full`` re-hashes every original; otherwise only paths are compared."""
    found = []
    for path in sorted(RESEARCH.rglob("*")):
        relative = path.relative_to(RESEARCH)
        if path.is_file() and path.suffix != ".md" and "evidence" not in relative.parts and relative.as_posix() not in GENERATED \
                and path != IGNORED_PASTES:
            found.append(f"原件不在 evidence/ 下：docs/research/{relative.as_posix()}（移到 <official|unofficial>/evidence/<主题-日期>/ 并写 source.json）")
    manifest, sums, missing = build(with_hashes=full)
    found += [f"原件缺少来源记录：docs/research/{path}/（补 source.json）" for path in missing]
    on_disk = (RESEARCH / "originals.sha256").read_text(encoding="utf-8") if (RESEARCH / "originals.sha256").exists() else ""
    stale = manifest != ((RESEARCH / "ORIGINALS.md").read_text(encoding="utf-8") if (RESEARCH / "ORIGINALS.md").exists() else "")
    stale |= (on_disk != "\n".join(sums) + "\n") if full else ({line.split("  ", 1)[-1] for line in on_disk.splitlines()} != set(sums))
    if stale:
        found.append("ORIGINALS.md / originals.sha256 已过期：运行 python -I scripts/research_originals.py")
    index = (RESEARCH / "README.md").read_text(encoding="utf-8")
    for note in notes():
        if note.relative_to(RESEARCH).as_posix() not in index:
            found.append(f"笔记没有登记到索引：docs/research/{note.relative_to(RESEARCH).as_posix()}（在 docs/research/README.md 索引表加一行）")
    return found


def unarchived_pastes(session_id: str) -> list[str]:
    """Images pasted into this session whose content is neither an archived original nor acknowledged as non-research."""
    if not session_id:
        return []
    known = set()
    for source in (RESEARCH / "originals.sha256", IGNORED_PASTES):
        if source.exists():
            known |= {line.split()[0] for line in source.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    found = []
    for image in sorted((Path(tempfile.gettempdir()) / "claude").glob(f"*/{session_id}/images/*")):
        if image.suffix.lower() in IMAGE_SUFFIXES and sha256(image) not in known:
            found.append(f"本会话粘贴的图片还没收纳：{image}（是外部资料就按流程归档；不是的话把 `<sha256>  <原因>` 写进 docs/research/.ignored-pastes）")
    return found


def hook() -> int:
    """Claude Code Stop hook: block the stop (once) and tell Claude what to fix."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    found = problems(full=False) + unarchived_pastes(str(payload.get("session_id") or ""))
    if not found:
        return 0
    text = "资料目录不整齐，按 CLAUDE.md《外部资料处理流程》处理：\n- " + "\n- ".join(found)
    if payload.get("stop_hook_active"):
        # Already blocked once this turn: let the turn end rather than loop, but tell the user.
        print(json.dumps({"systemMessage": text}, ensure_ascii=False))
    else:
        print(json.dumps({"decision": "block", "reason": text}, ensure_ascii=False))
    return 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")   # -I ignores PYTHONIOENCODING; Windows consoles default to cp1252
    if "--hook" in sys.argv:
        sys.exit(hook())
    if "--check" in sys.argv:
        found = problems(full=True)
        print("\n".join(found) if found else "docs/research is tidy")
        sys.exit(1 if found else 0)
    manifest, sums, missing = build()
    # LF endings so `sha256sum -c` works on every platform.
    (RESEARCH / "ORIGINALS.md").write_text(manifest, encoding="utf-8", newline="\n")
    (RESEARCH / "originals.sha256").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(sums)} files; missing source: {missing}")


if __name__ == "__main__":
    main()
