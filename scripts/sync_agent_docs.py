#!/usr/bin/env python3
"""Keep CLAUDE.md and AGENTS.md project instructions in sync.

The script intentionally uses only the Python standard library and supports
Python 3.9+.  Run with --init once in a repository, then the installed
pre-commit hook invokes --apply automatically.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


START_MARKER = "<!-- sync:shared:start -->"
END_MARKER = "<!-- sync:shared:end -->"
STATE_NAME = ".agents-sync.json"
SCRIPT_RELATIVE_PATH = Path("scripts/sync_agent_docs.py")
HOOK_RELATIVE_PATH = Path(".githooks/pre-commit")
RULE_HEADING = "## Agent 文档同步"

HOOK_CONTENT = """#!/bin/sh
set -e

if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    echo "Agent 文档同步失败：需要 Python 3.9 或更高版本。" >&2
    exit 2
fi

exec "$PYTHON" scripts/sync_agent_docs.py --apply
"""

RULE_SECTION = """## Agent 文档同步

`CLAUDE.md` 与 `AGENTS.md` 由 `scripts/sync_agent_docs.py` 在 commit 时自动双向同步。
改任意一侧都可以，但不要在同一次 commit 前同时修改两侧，否则会触发冲突并阻断 commit。
工具专属内容应放在 `<!-- sync:shared:end -->` 之后。

新 clone 仓库后需执行一次：

```sh
git config core.hooksPath .githooks
```

若 `.agents-sync.json` 发生 merge 冲突，删除该文件后重新运行同步即可重建。

已知限制：

- 部分暂存：hook 基于工作区文件同步。若只 stage 了文档的部分改动，工作区完整内容会进入本次 commit。
- `core.hooksPath` 需在每个 clone 中手动启用；未启用时 hook 不会运行。
- 工具不做内容合并；冲突始终交由人工处理。
"""


class SyncError(Exception):
    """A structural or repository error that should exit with status 2."""


@dataclasses.dataclass(frozen=True)
class Document:
    path: Path
    text: str
    shared: str
    start_index: Optional[int]
    end_index: Optional[int]

    @property
    def has_markers(self) -> bool:
        return self.start_index is not None


@dataclasses.dataclass(frozen=True)
class Pair:
    directory: Path


@dataclasses.dataclass(frozen=True)
class FileChange:
    directory_key: str
    source: Path
    target: Path
    content: str
    reason: str


@dataclasses.dataclass
class SyncPlan:
    changes: List[FileChange]
    conflicts: List[str]
    messages: List[str]
    state: Dict[str, object]
    state_changed: bool

    @property
    def has_drift(self) -> bool:
        return bool(self.changes or self.state_changed)


def _to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_shared(text: str) -> str:
    """Normalize shared text for comparison without changing source files."""
    lines = _to_lf(text).split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def shared_hash(text: str) -> str:
    digest = hashlib.sha256(normalize_shared(text).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def decode_document(path: Path, data: bytes) -> str:
    try:
        return _to_lf(data.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise SyncError(
            "{} 不是有效的 UTF-8 文件（字节位置 {}）".format(path, exc.start)
        ) from exc


def parse_document(path: Path, text: str) -> Document:
    """Parse a document and locate its optional shared markers."""
    lines = text.splitlines(keepends=True)
    starts = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n").strip() == START_MARKER
    ]
    ends = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n").strip() == END_MARKER
    ]

    if not starts and not ends:
        return Document(path, text, text, None, None)

    if len(starts) != 1 or len(ends) != 1:
        locations = ["start:第 {} 行".format(item + 1) for item in starts]
        locations.extend("end:第 {} 行".format(item + 1) for item in ends)
        detail = "，".join(locations) if locations else "未找到标记"
        raise SyncError(
            "{} 的同步标记必须恰好配对一次（{}）".format(path, detail)
        )

    start, end = starts[0], ends[0]
    if start >= end:
        raise SyncError(
            "{} 的同步标记顺序错误：start 在第 {} 行，end 在第 {} 行".format(
                path, start + 1, end + 1
            )
        )

    shared = "".join(lines[start + 1 : end])
    return Document(path, text, shared, start, end)


def read_document(path: Path) -> Document:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SyncError("无法读取 {}：{}".format(path, exc)) from exc
    return parse_document(path, decode_document(path, data))


def render_with_shared(target: Document, shared: str) -> str:
    """Return target content with its shared region replaced."""
    if not target.has_markers:
        return _to_lf(shared)

    lines = target.text.splitlines(keepends=True)
    assert target.start_index is not None
    assert target.end_index is not None
    replacement = _to_lf(shared)
    if replacement and not replacement.endswith("\n"):
        replacement += "\n"
    return (
        "".join(lines[: target.start_index + 1])
        + replacement
        + "".join(lines[target.end_index :])
    )


def find_repo_root(start: Optional[Path] = None) -> Path:
    command = ["git", "rev-parse", "--show-toplevel"]
    try:
        result = subprocess.run(
            command,
            cwd=str(start or Path.cwd()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise SyncError("无法执行 git：{}".format(exc)) from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise SyncError("当前目录不在 git 仓库中{}".format("：" + message if message else ""))
    return Path(os.fsdecode(result.stdout).strip()).resolve()


def _git_list_files(root: Path) -> List[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise SyncError("无法执行 git ls-files：{}".format(exc)) from exc
    if result.returncode != 0:
        raise SyncError(
            "git ls-files 失败：{}".format(
                result.stderr.decode("utf-8", "replace").strip()
            )
        )
    return [Path(os.fsdecode(item)) for item in result.stdout.split(b"\0") if item]


def discover_pairs(root: Path) -> Tuple[List[Pair], List[str]]:
    """Discover canonical document pairs using Git's ignored-file rules."""
    grouped: Dict[Path, Dict[str, List[str]]] = {}
    for relative in _git_list_files(root):
        lowered = relative.name.lower()
        if lowered not in ("claude.md", "agents.md"):
            continue
        entry = grouped.setdefault(relative.parent, {"claude.md": [], "agents.md": []})
        entry[lowered].append(relative.name)

    pairs: List[Pair] = []
    warnings: List[str] = []
    for relative_dir in sorted(grouped, key=lambda item: item.as_posix()):
        names = grouped[relative_dir]
        noncanonical = [
            name
            for name in names["claude.md"] + names["agents.md"]
            if name not in ("CLAUDE.md", "AGENTS.md")
        ]
        duplicates = any(len(values) > 1 for values in names.values())
        if noncanonical or duplicates:
            display = ", ".join(names["claude.md"] + names["agents.md"])
            warnings.append(
                "警告：目录 {} 中发现大小写冲突或非标准文件名（{}），已跳过".format(
                    relative_dir.as_posix() or ".", display
                )
            )
            continue
        pairs.append(Pair((root / relative_dir).resolve()))
    return pairs, warnings


def directory_key(root: Path, directory: Path) -> str:
    relative = directory.relative_to(root)
    return "." if relative == Path(".") else relative.as_posix()


def load_state(root: Path) -> Tuple[Dict[str, object], Optional[str]]:
    path = root / STATE_NAME
    if not path.exists():
        return {"version": 1, "pairs": {}}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if data.get("version") != 1 or not isinstance(data.get("pairs"), dict):
            raise ValueError("未知版本或 pairs 格式错误")
        for value in data["pairs"].values():
            if not isinstance(value, dict) or not isinstance(value.get("hash"), str):
                raise ValueError("pair 条目格式错误")
        return data, None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"version": 1, "pairs": {}}, "警告：{} 损坏或版本不受支持，将按无 state 处理（{}）".format(path, exc)


def _utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _state_entry(state_pairs: Dict[str, object], key: str) -> Optional[Dict[str, str]]:
    value = state_pairs.get(key)
    if isinstance(value, dict) and isinstance(value.get("hash"), str):
        return value  # type: ignore[return-value]
    return None


def _conflict_diff(key: str, claude: Document, agents: Document) -> str:
    diff = difflib.unified_diff(
        normalize_shared(claude.shared).splitlines(),
        normalize_shared(agents.shared).splitlines(),
        fromfile="{}/CLAUDE.md (shared)".format(key),
        tofile="{}/AGENTS.md (shared)".format(key),
        lineterm="",
    )
    rendered = "\n".join(diff)
    return "目录 {} 的两侧内容冲突：\n{}".format(key, rendered or "（归一化内容不同）")


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and os.path.samefile(str(left), str(right))
    except OSError:
        return False


def build_plan(
    root: Path,
    pairs: Iterable[Pair],
    state: Dict[str, object],
    prefer: Optional[str] = None,
) -> SyncPlan:
    """Analyze every pair without writing anything."""
    old_pairs = state.get("pairs", {})
    assert isinstance(old_pairs, dict)
    new_pairs: Dict[str, object] = dict(old_pairs)
    changes: List[FileChange] = []
    conflicts: List[str] = []
    messages: List[str] = []
    timestamp = _utc_timestamp()

    for pair in pairs:
        key = directory_key(root, pair.directory)
        claude_path = pair.directory / "CLAUDE.md"
        agents_path = pair.directory / "AGENTS.md"
        claude_exists = claude_path.exists()
        agents_exists = agents_path.exists()

        if not claude_exists and not agents_exists:
            continue
        if _same_file(claude_path, agents_path):
            messages.append("SKIP {}：两侧解析为同一个文件".format(key))
            continue

        old_entry = _state_entry(old_pairs, key)
        old_hash = old_entry.get("hash") if old_entry else None

        if claude_exists and not agents_exists:
            claude = read_document(claude_path)
            digest = shared_hash(claude.shared)
            changes.append(
                FileChange(key, claude_path, agents_path, claude.text, "仅 CLAUDE.md 存在")
            )
            messages.append("CREATE {}/AGENTS.md ← CLAUDE.md".format(key))
            new_pairs[key] = {"hash": digest, "synced_at": timestamp}
            continue

        if agents_exists and not claude_exists:
            agents = read_document(agents_path)
            digest = shared_hash(agents.shared)
            changes.append(
                FileChange(key, agents_path, claude_path, agents.text, "仅 AGENTS.md 存在")
            )
            messages.append("CREATE {}/CLAUDE.md ← AGENTS.md".format(key))
            new_pairs[key] = {"hash": digest, "synced_at": timestamp}
            continue

        claude = read_document(claude_path)
        agents = read_document(agents_path)
        claude_hash = shared_hash(claude.shared)
        agents_hash = shared_hash(agents.shared)

        if claude_hash == agents_hash:
            messages.append("OK {}：shared 内容一致".format(key))
            if old_hash != claude_hash:
                new_pairs[key] = {"hash": claude_hash, "synced_at": timestamp}
            continue

        source: Optional[Document] = None
        target: Optional[Document] = None
        reason = ""
        if old_hash is not None and claude_hash == old_hash and agents_hash != old_hash:
            source, target = agents, claude
            reason = "AGENTS.md 自上次同步后发生修改"
        elif old_hash is not None and agents_hash == old_hash and claude_hash != old_hash:
            source, target = claude, agents
            reason = "CLAUDE.md 自上次同步后发生修改"
        elif prefer == "claude":
            source, target = claude, agents
            reason = "按 --prefer claude 解决冲突"
        elif prefer == "agents":
            source, target = agents, claude
            reason = "按 --prefer agents 解决冲突"
        else:
            conflicts.append(_conflict_diff(key, claude, agents))
            continue

        assert source is not None and target is not None
        content = render_with_shared(target, source.shared)
        changes.append(FileChange(key, source.path, target.path, content, reason))
        messages.append(
            "SYNC {}：{} → {}（{}）".format(
                key, source.path.name, target.path.name, reason
            )
        )
        new_pairs[key] = {"hash": shared_hash(source.shared), "synced_at": timestamp}

    new_state: Dict[str, object] = {"version": 1, "pairs": new_pairs}
    return SyncPlan(
        changes=changes,
        conflicts=conflicts,
        messages=messages,
        state=new_state,
        state_changed=new_state != state,
    )


def serialize_state(state: Dict[str, object]) -> str:
    pairs = state.get("pairs", {})
    assert isinstance(pairs, dict)
    ordered = {key: pairs[key] for key in sorted(pairs)}
    return json.dumps(
        {"version": 1, "pairs": ordered},
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_to_lf(content).encode("utf-8"))


def apply_plan(root: Path, plan: SyncPlan) -> List[Path]:
    if plan.conflicts:
        raise SyncError("内部错误：不能应用包含冲突的同步计划")
    changed: List[Path] = []
    for change in plan.changes:
        _write_text(change.target, change.content)
        changed.append(change.target)
    if plan.state_changed:
        state_path = root / STATE_NAME
        _write_text(state_path, serialize_state(plan.state))
        changed.append(state_path)
    return changed


def git_add(root: Path, paths: Sequence[Path]) -> None:
    if not paths:
        return
    relative = [str(path.relative_to(root)) for path in paths]
    result = subprocess.run(
        ["git", "add", "--"] + relative,
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise SyncError(
            "git add 失败：{}".format(result.stderr.decode("utf-8", "replace").strip())
        )


def run_sync(
    root: Path,
    apply: bool = False,
    check: bool = False,
    prefer: Optional[str] = None,
) -> int:
    pairs, warnings = discover_pairs(root)
    for warning in warnings:
        print(warning, file=sys.stderr)
    if not pairs:
        print("未发现需同步的文件")
        return 0

    state, state_warning = load_state(root)
    if state_warning:
        print(state_warning, file=sys.stderr)
    plan = build_plan(root, pairs, state, prefer)
    for message in plan.messages:
        print(message)

    if plan.conflicts:
        for conflict in plan.conflicts:
            print(conflict, file=sys.stderr)
        print(
            "请手工合并后重跑，或使用 --apply --prefer claude|agents 强制单侧覆盖。",
            file=sys.stderr,
        )
        return 1

    if check:
        if plan.has_drift:
            print("检测到 Agent 文档或 state 偏差", file=sys.stderr)
            return 1
        print("检查通过，无偏差")
        return 0

    if not apply:
        if plan.has_drift:
            print("DRY-RUN：以上变更尚未写入；使用 --apply 执行。")
        else:
            print("DRY-RUN：无需变更。")
        return 0

    changed = apply_plan(root, plan)
    git_add(root, changed)
    if changed:
        print("已同步并暂存 {} 个文件。".format(len(changed)))
    else:
        print("无需变更。")
    return 0


def append_rule_section(path: Path) -> bool:
    if path.exists():
        document = read_document(path)
        if any(line.strip() == RULE_HEADING for line in document.text.splitlines()):
            return False
        content = document.text
        if content and not content.endswith("\n"):
            content += "\n"
        if content and not content.endswith("\n\n"):
            content += "\n"
        content += RULE_SECTION
    else:
        content = RULE_SECTION
    _write_text(path, content)
    return True


def install_root_rule_sections(root: Path) -> None:
    claude = root / "CLAUDE.md"
    agents = root / "AGENTS.md"

    # A missing counterpart has an unambiguous direction.  Copying the existing
    # document here avoids creating an artificial first-run conflict merely by
    # installing the rule section.  Existing, divergent files remain untouched
    # apart from receiving the same rule section independently.
    if claude.exists() != agents.exists():
        existing = claude if claude.exists() else agents
        missing = agents if claude.exists() else claude
        changed = append_rule_section(existing)
        _write_text(missing, read_document(existing).text)
        if changed:
            print("已向 {} 注入同步规则".format(existing.name))
        else:
            print("{} 已包含同步规则，跳过".format(existing.name))
        print("已从 {} 创建并注入 {}".format(existing.name, missing.name))
        return

    for path in (claude, agents):
        if append_rule_section(path):
            print("已向 {} 注入同步规则".format(path.name))
        else:
            print("{} 已包含同步规则，跳过".format(path.name))


def _scan_initial_pairs(root: Path) -> Tuple[int, int]:
    pairs, warnings = discover_pairs(root)
    for warning in warnings:
        print(warning, file=sys.stderr)
    mismatches = 0
    for pair in pairs:
        claude_path = pair.directory / "CLAUDE.md"
        agents_path = pair.directory / "AGENTS.md"
        if claude_path.exists() and agents_path.exists():
            if _same_file(claude_path, agents_path):
                continue
            claude = read_document(claude_path)
            agents = read_document(agents_path)
            if shared_hash(claude.shared) != shared_hash(agents.shared):
                mismatches += 1
    return len(pairs), mismatches


def initialize(root: Path) -> int:
    source = Path(__file__).resolve()
    destination = root / SCRIPT_RELATIVE_PATH
    source_bytes = source.read_bytes()
    if source != destination.resolve():
        if destination.exists() and destination.read_bytes() != source_bytes:
            print("警告：将覆盖内容不同的 {}".format(destination), file=sys.stderr)
        if not destination.exists() or destination.read_bytes() != source_bytes:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source_bytes)
            print("已安装 {}".format(SCRIPT_RELATIVE_PATH.as_posix()))
        else:
            print("脚本已是最新版本")
    else:
        print("脚本已位于 {}".format(SCRIPT_RELATIVE_PATH.as_posix()))

    hook_path = root / HOOK_RELATIVE_PATH
    _write_text(hook_path, HOOK_CONTENT)
    hook_path.chmod(hook_path.stat().st_mode | 0o111)
    print("已写入并启用 {}".format(HOOK_RELATIVE_PATH.as_posix()))

    result = subprocess.run(
        ["git", "config", "core.hooksPath", ".githooks"],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise SyncError(
            "git config core.hooksPath 失败：{}".format(
                result.stderr.decode("utf-8", "replace").strip()
            )
        )

    pair_count, mismatch_count = _scan_initial_pairs(root)
    print("发现 {} 个 pair，其中 {} 个两侧内容不一致。".format(pair_count, mismatch_count))

    install_root_rule_sections(root)

    if mismatch_count:
        print("发现两侧内容不一致，请先决定以哪侧为准：")
    else:
        print("下一步先查看同步计划：")
    print("  python scripts/sync_agent_docs.py")
    print("  python scripts/sync_agent_docs.py --apply --prefer claude")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="双向同步仓库中的 CLAUDE.md 与 AGENTS.md"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help="执行同步并 git add")
    modes.add_argument("--check", action="store_true", help="只检测偏差，不写盘")
    modes.add_argument("--init", action="store_true", help="在当前 git 仓库中自安装")
    parser.add_argument(
        "--prefer",
        choices=("claude", "agents"),
        help="冲突时强制选择一侧（可用于 dry-run 预览）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.init and args.prefer:
        parser.error("--init 不能与 --prefer 同时使用")
    try:
        root = find_repo_root()
        if args.init:
            return initialize(root)
        return run_sync(root, apply=args.apply, check=args.check, prefer=args.prefer)
    except SyncError as exc:
        print("错误：{}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
