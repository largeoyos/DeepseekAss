from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


DEFAULT_EXTENSIONS = {
    ".py",
    ".pyw",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".scss",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
}

DEFAULT_EXCLUDES = {
    ".claude",
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "plans",
    "venv",
    ".venv",
    "env",
    ".env",
}

DEFAULT_EXCLUDE_FILES = {
    "CLAUDE.md",
}


@dataclass(frozen=True)
class FileStats:
    path: Path
    total: int
    code: int
    blank: int
    comment: int


def parse_extensions(value: str | None) -> set[str]:
    if not value:
        return set(DEFAULT_EXTENSIONS)
    return {
        item if item.startswith(".") else f".{item}"
        for item in (part.strip().lower() for part in value.split(","))
        if item
    }


def should_skip(path: Path, root: Path, excludes: set[str], exclude_files: set[str]) -> bool:
    relative_parts = path.relative_to(root).parts
    return path.name in exclude_files or any(part in excludes for part in relative_parts)


def is_comment_line(line: str, suffix: str) -> bool:
    stripped = line.lstrip()
    if suffix in {".py", ".pyw"}:
        return stripped.startswith("#")
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".css", ".scss"}:
        return stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*")
    if suffix in {".html", ".md"}:
        return stripped.startswith("<!--")
    if suffix in {".yaml", ".yml"}:
        return stripped.startswith("#")
    return False


def count_file(path: Path, root: Path) -> FileStats:
    total = code = blank = comment = 0
    suffix = path.suffix.lower()

    with path.open("r", encoding="utf-8", errors="ignore") as file:
        for line in file:
            total += 1
            if not line.strip():
                blank += 1
            elif is_comment_line(line, suffix):
                comment += 1
            else:
                code += 1

    return FileStats(path=path.relative_to(root), total=total, code=code, blank=blank, comment=comment)


def collect_stats(
    root: Path,
    extensions: set[str],
    excludes: set[str],
    exclude_files: set[str],
) -> list[FileStats]:
    stats: list[FileStats] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if should_skip(path, root, excludes, exclude_files):
            continue
        if path.suffix.lower() not in extensions:
            continue
        stats.append(count_file(path, root))
    return sorted(stats, key=lambda item: str(item.path).lower())


def print_table(stats: list[FileStats]) -> None:
    path_width = max((len(str(item.path)) for item in stats), default=4)
    print(f"{'file':<{path_width}}  {'total':>7}  {'code':>7}  {'blank':>7}  {'comment':>7}")
    print(f"{'-' * path_width}  {'-' * 7}  {'-' * 7}  {'-' * 7}  {'-' * 7}")
    for item in stats:
        print(f"{str(item.path):<{path_width}}  {item.total:>7}  {item.code:>7}  {item.blank:>7}  {item.comment:>7}")

    total_lines = sum(item.total for item in stats)
    code_lines = sum(item.code for item in stats)
    blank_lines = sum(item.blank for item in stats)
    comment_lines = sum(item.comment for item in stats)
    print(f"{'-' * path_width}  {'-' * 7}  {'-' * 7}  {'-' * 7}  {'-' * 7}")
    print(f"{'TOTAL':<{path_width}}  {total_lines:>7}  {code_lines:>7}  {blank_lines:>7}  {comment_lines:>7}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Count project lines by file.")
    parser.add_argument("root", nargs="?", default=".", help="Project root to scan. Defaults to current directory.")
    parser.add_argument(
        "--ext",
        help="Comma-separated extensions to include, for example: py,md,html. Defaults to common code files.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Directory name to exclude. Can be passed multiple times.",
    )
    parser.add_argument(
        "--exclude-file",
        action="append",
        default=[],
        help="File name to exclude. Can be passed multiple times.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        parser.error(f"root does not exist: {root}")
    if not root.is_dir():
        parser.error(f"root is not a directory: {root}")

    extensions = parse_extensions(args.ext)
    excludes = DEFAULT_EXCLUDES | set(args.exclude)
    exclude_files = DEFAULT_EXCLUDE_FILES | set(args.exclude_file)
    stats = collect_stats(root, extensions, excludes, exclude_files)
    print_table(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
