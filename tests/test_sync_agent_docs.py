import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "sync_agent_docs.py"
START = "<!-- sync:shared:start -->"
END = "<!-- sync:shared:end -->"


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "tests@example.com")
    git(repo, "config", "user.name", "Sync Tests")
    return repo


def run_sync(repo, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def write(repo, relative, content, newline=None):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        with path.open("w", encoding="utf-8", newline=newline) as handle:
            handle.write(content)
    return path


def baseline(repo, content="# Shared\n"):
    write(repo, "CLAUDE.md", content)
    write(repo, "AGENTS.md", content)
    result = run_sync(repo, "--apply")
    assert result.returncode == 0, result.stderr
    return json.loads((repo / ".agents-sync.json").read_text())


def test_only_agents_creates_claude_and_state(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "AGENTS.md", "# Rules\n")

    result = run_sync(repo, "--apply")

    assert result.returncode == 0
    assert (repo / "CLAUDE.md").read_text() == "# Rules\n"
    state = json.loads((repo / ".agents-sync.json").read_text())
    assert list(state["pairs"]) == ["."]
    staged = git(repo, "diff", "--cached", "--name-only").stdout.splitlines()
    assert staged == [".agents-sync.json", "CLAUDE.md"]


def test_equal_pair_dry_run_does_not_touch_files(tmp_path):
    repo = make_repo(tmp_path)
    claude = write(repo, "CLAUDE.md", "same\n")
    agents = write(repo, "AGENTS.md", "same\n")
    before = (claude.stat().st_mtime_ns, agents.stat().st_mtime_ns)

    result = run_sync(repo)

    assert result.returncode == 0
    assert "OK" in result.stdout
    assert (claude.stat().st_mtime_ns, agents.stat().st_mtime_ns) == before
    assert not (repo / ".agents-sync.json").exists()


@pytest.mark.parametrize(
    ("edited", "other"),
    [("CLAUDE.md", "AGENTS.md"), ("AGENTS.md", "CLAUDE.md")],
)
def test_one_sided_edit_syncs_in_the_correct_direction(tmp_path, edited, other):
    repo = make_repo(tmp_path)
    baseline(repo)
    write(repo, edited, "changed from {}\n".format(edited))

    result = run_sync(repo, "--apply")

    assert result.returncode == 0
    assert (repo / other).read_text() == "changed from {}\n".format(edited)


def test_two_sided_edit_conflicts_without_writing_any_pair(tmp_path):
    repo = make_repo(tmp_path)
    baseline(repo)
    write(repo, "CLAUDE.md", "claude edit\n")
    write(repo, "AGENTS.md", "agents edit\n")
    before_state = (repo / ".agents-sync.json").read_bytes()

    result = run_sync(repo, "--apply")

    assert result.returncode == 1
    assert "--- ./CLAUDE.md" in result.stderr
    assert (repo / "CLAUDE.md").read_text() == "claude edit\n"
    assert (repo / "AGENTS.md").read_text() == "agents edit\n"
    assert (repo / ".agents-sync.json").read_bytes() == before_state


def test_first_conflict_can_be_resolved_with_preference(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "CLAUDE.md", "claude wins\n")
    write(repo, "AGENTS.md", "agents loses\n")

    conflict = run_sync(repo, "--apply")
    resolved = run_sync(repo, "--apply", "--prefer", "claude")

    assert conflict.returncode == 1
    assert resolved.returncode == 0
    assert (repo / "AGENTS.md").read_text() == "claude wins\n"


def test_marked_target_preserves_private_content(tmp_path):
    repo = make_repo(tmp_path)
    claude_text = "{}\nold\n{}\n@private.md\n".format(START, END)
    write(repo, "CLAUDE.md", claude_text)
    write(repo, "AGENTS.md", "old\n")
    assert run_sync(repo, "--apply").returncode == 0
    write(repo, "AGENTS.md", "new shared text")

    result = run_sync(repo, "--apply")

    assert result.returncode == 0
    assert (repo / "CLAUDE.md").read_text() == (
        "{}\nnew shared text\n{}\n@private.md\n".format(START, END)
    )


def test_nested_pairs_are_independent_and_state_is_sorted(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "z/deep/AGENTS.md", "z\n")
    write(repo, "a/AGENTS.md", "a\n")
    write(repo, "AGENTS.md", "root\n")

    result = run_sync(repo, "--apply")

    assert result.returncode == 0
    state = json.loads((repo / ".agents-sync.json").read_text())
    assert list(state["pairs"]) == [".", "a", "z/deep"]
    assert (repo / "z/deep/CLAUDE.md").read_text() == "z\n"


def test_gitignored_documents_are_not_discovered(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, ".gitignore", "node_modules/\n")
    write(repo, "node_modules/CLAUDE.md", "ignored\n")

    result = run_sync(repo, "--apply")

    assert result.returncode == 0
    assert "未发现" in result.stdout
    assert not (repo / "node_modules/AGENTS.md").exists()
    assert not (repo / ".agents-sync.json").exists()


def test_normalization_avoids_crlf_and_trailing_space_false_conflicts(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "CLAUDE.md").write_bytes(b"\r\nvalue  \r\n")
    (repo / "AGENTS.md").write_bytes(b"value\n\n")

    result = run_sync(repo, "--apply")

    assert result.returncode == 0
    assert (repo / "CLAUDE.md").read_bytes() == b"\r\nvalue  \r\n"
    assert (repo / "AGENTS.md").read_bytes() == b"value\n\n"


def test_marker_errors_and_non_utf8_exit_two(tmp_path):
    marker_repo = make_repo(tmp_path / "marker")
    write(marker_repo, "CLAUDE.md", "intro\n{}\ntext\n".format(START))
    marker_result = run_sync(marker_repo, "--apply")
    assert marker_result.returncode == 2
    assert "第 2 行" in marker_result.stderr

    encoding_repo = make_repo(tmp_path / "encoding")
    write(encoding_repo, "AGENTS.md", b"\xff\xfe")
    encoding_result = run_sync(encoding_repo, "--apply")
    assert encoding_result.returncode == 2
    assert "UTF-8" in encoding_result.stderr


def test_damaged_state_behaves_like_missing_state(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "CLAUDE.md", "one\n")
    write(repo, "AGENTS.md", "two\n")
    write(repo, ".agents-sync.json", "not json")

    result = run_sync(repo, "--apply")

    assert result.returncode == 1
    assert "按无 state 处理" in result.stderr
    assert (repo / ".agents-sync.json").read_text() == "not json"


def test_check_reports_drift_without_writing(tmp_path):
    repo = make_repo(tmp_path)
    baseline(repo)
    write(repo, "CLAUDE.md", "pending\n")

    result = run_sync(repo, "--check")

    assert result.returncode == 1
    assert (repo / "AGENTS.md").read_text() == "# Shared\n"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlink_to_other_side_is_skipped(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "CLAUDE.md", "one file\n")
    try:
        os.symlink("CLAUDE.md", repo / "AGENTS.md")
    except OSError:
        pytest.skip("symlink creation unavailable")

    result = run_sync(repo, "--apply")

    assert result.returncode == 0
    assert "同一个文件" in result.stdout
    assert not (repo / ".agents-sync.json").exists()


def test_noncanonical_case_is_warned_and_skipped(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "claude.md", "lowercase\n")

    result = run_sync(repo, "--apply")

    assert result.returncode == 0
    assert "非标准文件名" in result.stderr
    assert not (repo / "AGENTS.md").exists()


def test_init_is_idempotent_and_installs_executable_hook(tmp_path):
    repo = make_repo(tmp_path)
    write(repo, "README.md", "hello\n")

    first = run_sync(repo, "--init")
    second = subprocess.run(
        [sys.executable, "scripts/sync_agent_docs.py", "--init"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert first.returncode == second.returncode == 0
    assert (repo / "scripts/sync_agent_docs.py").read_bytes() == SCRIPT.read_bytes()
    hook = repo / ".githooks/pre-commit"
    assert os.access(hook, os.X_OK)
    assert "python3" in hook.read_text() and "python" in hook.read_text()
    assert git(repo, "config", "--get", "core.hooksPath").stdout.strip() == ".githooks"
    for name in ("CLAUDE.md", "AGENTS.md"):
        assert (repo / name).read_text().count("## Agent 文档同步") == 1


@pytest.mark.parametrize("existing", ["CLAUDE.md", "AGENTS.md"])
def test_init_with_one_root_document_does_not_create_a_conflict(tmp_path, existing):
    repo = make_repo(tmp_path)
    write(repo, existing, "# Existing project rules\n")

    initialized = run_sync(repo, "--init")
    checked = subprocess.run(
        [sys.executable, "scripts/sync_agent_docs.py", "--check"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert initialized.returncode == 0
    assert checked.returncode == 1  # Only the not-yet-written state is drift.
    assert "内容冲突" not in checked.stderr
    assert (repo / "CLAUDE.md").read_text() == (repo / "AGENTS.md").read_text()
    assert "# Existing project rules" in (repo / "CLAUDE.md").read_text()


def test_hook_syncs_and_stages_generated_files_during_commit(tmp_path):
    repo = make_repo(tmp_path)
    assert run_sync(repo, "--init").returncode == 0
    git(repo, "add", ".")

    commit = git(repo, "commit", "-m", "install sync", check=False)

    assert commit.returncode == 0, commit.stderr
    committed = git(repo, "show", "--pretty=format:", "--name-only", "HEAD").stdout
    assert ".agents-sync.json" in committed.splitlines()


def test_hook_blocks_conflict_and_keeps_worktree_unchanged(tmp_path):
    repo = make_repo(tmp_path)
    assert run_sync(repo, "--init").returncode == 0
    git(repo, "add", ".")
    assert git(repo, "commit", "-m", "baseline", check=False).returncode == 0
    write(repo, "CLAUDE.md", "claude conflict\n")
    write(repo, "AGENTS.md", "agents conflict\n")
    git(repo, "add", "CLAUDE.md", "AGENTS.md")

    commit = git(repo, "commit", "-m", "must fail", check=False)

    assert commit.returncode != 0
    assert (repo / "CLAUDE.md").read_text() == "claude conflict\n"
    assert (repo / "AGENTS.md").read_text() == "agents conflict\n"
