#!/usr/bin/env python
"""Compliance gate for every repo in this portfolio.

Checks a repository for:
  1. banned words (company names, internal platform names)
  2. PII patterns (mobile numbers, national IDs)
  3. database connection strings
  4. data files living outside the allowed fixtures directory

Exit code 1 on any finding, so it can gate CI.

Usage:
    python scripts/compliance_check.py .            # scan a repo
    python scripts/compliance_check.py . --verbose
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

TEXT_SUFFIXES = {
    ".py", ".md", ".yml", ".yaml", ".json", ".toml", ".txt",
    ".csv", ".sql", ".html", ".js", ".css", ".cfg", ".ini",
}
SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "dist", "build", "runs",  # run artifacts
    "_trial", "_tools",  # local scratch dirs, git-ignored
}
ALLOWED_DATA_SUFFIXES = {".parquet", ".csv", ".json", ".md", ".py"}

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("mobile_number", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("national_id", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("db_uri", re.compile(r"\b(?:mysql|postgres(?:ql)?|mongodb(?:\+srv)?|redis|mssql|clickhouse)://", re.I)),
    ("jdbc_uri", re.compile(r"\bjdbc:[a-z]+://", re.I)),
    ("odbc_dsn", re.compile(r"Server\s*=\s*[^;\n]+;\s*(?:Database|Initial Catalog)\s*=", re.I)),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

WORDS_FILE = Path(__file__).with_name("compliance_words.txt")
# Real company / brand names must never be committed. They live in a local,
# git-ignored file or in the COMPLIANCE_WORDS environment variable.
LOCAL_WORDS_FILE = Path(__file__).with_name("compliance_words.local.txt")


def _read_words(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        w = line.strip()
        if w and not w.startswith("#"):
            out.append(w)
    return out


def load_words() -> list[str]:
    words = _read_words(WORDS_FILE) + _read_words(LOCAL_WORDS_FILE)
    for w in os.getenv("COMPLIANCE_WORDS", "").split(","):
        w = w.strip()
        if w:
            words.append(w)

    seen: set[str] = set()
    unique: list[str] = []
    for w in words:
        key = w.lower()
        if key not in seen:
            seen.add(key)
            unique.append(w)
    return unique


def iter_text_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield p


def scan(root: Path, verbose: bool = False) -> list[dict]:
    words = load_words()
    findings: list[dict] = []
    checked = 0

    for path in iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        if rel.endswith(
            ("compliance_check.py", "compliance_words.txt", "compliance_words.local.txt")
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        checked += 1
        for i, line in enumerate(text.splitlines(), 1):
            for w in words:
                if w.lower() in line.lower():
                    findings.append({"file": rel, "line": i, "kind": "banned_word", "detail": w})
            normalised = line.replace(" ", "").replace("-", "")
            for kind, pat in PATTERNS:
                target = normalised if kind == "mobile_number" else line
                if pat.search(target):
                    findings.append({"file": rel, "line": i, "kind": kind, "detail": pat.pattern})

    # Data files must live under data/ or fixtures/.
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in ALLOWED_DATA_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(root).as_posix()
        if p.suffix.lower() in {".parquet", ".csv"}:
            if not (rel.startswith("data/") or rel.startswith("fixtures/") or "/fixtures/" in rel):
                findings.append({"file": rel, "line": 0, "kind": "stray_data_file", "detail": "数据文件必须位于 data/ 或 fixtures/"})

    if verbose:
        print(f"scanned {checked} text files under {root}")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    findings = scan(root, args.verbose)

    if not findings:
        print(f"[PASS] {root.name}: 未发现禁用词、PII、连接串或越位数据文件")
        return 0

    print(f"[FAIL] {root.name}: {len(findings)} 处问题")
    for f in findings:
        loc = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
        print(f"  - {loc}  [{f['kind']}] {f['detail']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
