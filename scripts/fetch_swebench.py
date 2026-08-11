#!/usr/bin/env python3
"""Fetch historical-bug benchmark data from SWE-bench (HuggingFace).

Usage:
    python scripts/fetch_swebench.py --output benchmarks/datasets/swebench_review.jsonl --limit 20

This importer reconstructs a *bug-introducing change* per instance:

    fix_commit  -> the commit that FIXED the bug (public in SWE-bench)
    patch       -> the fix patch (held out)

The bug-introducing diff is reconstructed by reverse-applying the fix patch to
the repository state at the fix commit's parent. `bug_description` is derived
from the associated GitHub issue and is stored with `hold_out: true` so the
evaluation never passes it to reviewers.

Requirements (optional dependency group):
    pip install datasets

Notes:
    - Requires network access and ~1-2 GB disk for cloning repos.
    - Datasets are large; start with --limit 5-20.
    - Output format: JSONL of BenchmarkEntry, leakage-controlled.
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from agentic_code_reviewer.evaluation.benchmark import BenchmarkEntry, BenchmarkLoader


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr[:300]}")
    return proc.stdout


def _reverse_patch(fix_patch: str) -> str:
    """Swap +/- lines of a unified patch to approximate the bug-introducing change."""
    out: list[str] = []
    for line in fix_patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append("-" + line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            out.append("+" + line[1:])
        else:
            out.append(line)
    return "\n".join(out)


def _build_entry(instance: dict, workdir: Path) -> BenchmarkEntry | None:
    repo = instance["repo"].replace("/", "__")
    base_commit = instance.get("base_commit")
    if not base_commit:
        return None
    repo_dir = workdir / repo
    if not repo_dir.exists():
        _git(workdir, "clone", "--quiet", f"https://github.com/{instance['repo']}.git", repo)
    _git(repo_dir, "checkout", "--quiet", "--detach", base_commit)
    diff = _git(repo_dir, "diff", base_commit + "^", base_commit)
    if not diff.strip():
        return None
    # Bounded base snapshot for context/retrieval.
    from agentic_code_reviewer.github.adapter import read_local_repository

    base_files = read_local_repository(str(repo_dir))
    problem = instance.get("problem_statement", "") or ""
    return BenchmarkEntry(
        entry_id=f"{instance['repo']}-{base_commit[:8]}",
        repository=instance["repo"],
        commit=base_commit,
        parent_commit=base_commit + "^",
        bug_description=problem[:2000],
        bug_fix=instance.get("patch", "")[:2000],
        hold_out=True,
        diff=diff,
        affected_files=[],
        base_files=base_files,
        language="python",
        category="",
        severity="",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--split", default="test")
    args = parser.parse_args()

    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise SystemExit(
            "The 'datasets' package is required. Install it with: pip install datasets"
        ) from exc

    ds = load_dataset("princeton-nlp/SWE-bench", split=args.split)
    entries: list[BenchmarkEntry] = []
    with tempfile.TemporaryDirectory(prefix="swebench_") as tmp:
        workdir = Path(tmp)
        for i, instance in enumerate(ds):
            if len(entries) >= args.limit:
                break
            try:
                entry = _build_entry(instance, workdir)
            except Exception as exc:  # noqa: BLE001 - skip problematic instances
                print(f"  ! skipped {instance.get('instance_id', i)}: {exc}")
                continue
            if entry:
                entries.append(entry)
                print(f"  + {entry.entry_id} ({len(entries)}/{args.limit})")

    BenchmarkLoader.save(args.output, entries)
    print(f"\nWrote {len(entries)} entries to {args.output}")


if __name__ == "__main__":
    main()
