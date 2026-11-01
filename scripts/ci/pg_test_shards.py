#!/usr/bin/env python3
"""Deterministic, complete PostgreSQL helper module partition for CI."""
import argparse
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parents[2] / "tests" / "pg"
SHARD = re.compile(r"^([1-9][0-9]*)/([1-9][0-9]*)$")


def discover_modules(root=TESTS):
    if not root.is_dir():
        raise ValueError("PostgreSQL tests/pg unavailable")
    paths = sorted(root.rglob("test_*.py"))
    if not paths:
        raise ValueError("No PostgreSQL helper test modules found")
    modules = []
    for path in paths:
        rel = path.relative_to(root).with_suffix("")
        if any(not name.isidentifier() for name in rel.parts):
            raise ValueError("Invalid PostgreSQL test module name")
        modules.append("tests.pg." + ".".join(rel.parts))
    if len(set(modules)) != len(modules):
        raise ValueError("Duplicate PostgreSQL test module")
    return modules


def shard_modules(modules, index, total):
    if not 1 <= total <= 12 or not 1 <= index <= total:
        raise ValueError("Invalid PostgreSQL shard index/total")
    if len(modules) < total or len(set(modules)) != len(modules):
        raise ValueError("Unshardable PostgreSQL module inventory")
    return [name for i, name in enumerate(modules) if i % total == index - 1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", required=True)
    args = parser.parse_args()
    match = SHARD.fullmatch(args.shard)
    if match is None:
        parser.error("Invalid PostgreSQL shard")
    try:
        result = shard_modules(discover_modules(), int(match[1]), int(match[2]))
    except ValueError as exc:
        parser.error(str(exc))
    if not result:
        parser.error("Empty PostgreSQL test shard")
    for name in result:
        print(name)


if __name__ == "__main__":
    main()
