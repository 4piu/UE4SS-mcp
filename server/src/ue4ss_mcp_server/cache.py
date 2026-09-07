"""Fetch/cache layer for versioned RE-UE4SS repo content.

Shallow-clones a single git ref, extracts only the subtrees we need
(docs/, assets/CustomGameConfigs/), and caches the result on disk keyed
by ref name. Numbered tags are immutable and cached forever; moving refs
(experimental, experimental-latest) are re-fetched once per process
lifetime rather than trusted from a prior run. See dev-notes/spec.md §5.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/UE4SS-RE/RE-UE4SS.git"

# Subtrees copied out of the shallow clone into the cache.
_SUBTREES = ("docs", "assets/CustomGameConfigs")

# Refs that move over time and must not be trusted across process runs.
_MOVING_REFS = {"experimental", "experimental-latest"}

# Refs refreshed at least once this process (moving refs only).
_refreshed_this_run: set[str] = set()


class RefFetchError(RuntimeError):
    """Raised when a ref can't be shallow-cloned (bad ref, network, etc.)."""


def cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "ue4ss-mcp" / "cache"


def _ref_dir(ref: str) -> Path:
    # Ref names can contain characters that aren't safe as directory
    # names on Windows in theory (they can't in practice for git refs
    # we care about here), so no extra sanitization needed for the refs
    # this project actually uses (tags/branch names from RE-UE4SS).
    return cache_root() / ref


def _is_populated(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def ensure_ref_cached(ref: str, *, force_refresh: bool = False) -> Path:
    """Ensure `ref`'s docs/compat subtrees are on disk; return the cache dir.

    Raises RefFetchError if the ref doesn't exist or the clone fails.
    """
    target = _ref_dir(ref)
    is_moving = ref in _MOVING_REFS
    already_refreshed = ref in _refreshed_this_run

    if not force_refresh and _is_populated(target):
        if not is_moving or already_refreshed:
            return target

    _clone_ref(ref, target)
    if is_moving:
        _refreshed_this_run.add(ref)
    return target


def _clone_ref(ref: str, target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="ue4ss-mcp-clone-") as tmp:
        tmp_path = Path(tmp)
        result = subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                ref,
                "--single-branch",
                REPO_URL,
                str(tmp_path / "repo"),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RefFetchError(
                f"failed to clone ref {ref!r}: {result.stderr.strip()}"
            )

        repo_dir = tmp_path / "repo"
        staging = tmp_path / "staging"
        staging.mkdir()
        for subtree in _SUBTREES:
            src = repo_dir / subtree
            if not src.exists():
                continue
            dst = staging / subtree
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)

        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(target))
