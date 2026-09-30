#!/usr/bin/env python3
"""Validate the Styx skill: frontmatter parses + required keys, every
references/ path in SKILL.md resolves, every file in references/ is mapped in
the Support File Map, and the test suite passes.

Usage: python3 tests/validate_skill.py [--help]
Exit 0 = all clean. Exit 1 = one or more problems (each printed to stdout).
"""
import os
import re
import subprocess
import sys

_HELP_ARGS = {"--help", "-h"}
if set(sys.argv[1:]) & _HELP_ARGS:
    print((__doc__ or "").strip())
    sys.exit(0)

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")

problems = []


def check_frontmatter():
    import yaml
    text = open(SKILL_MD, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        problems.append("no YAML frontmatter block")
        return {}
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception as e:
        problems.append(f"frontmatter does not parse: {e}")
        return {}
    for key in ("name", "description", "license", "includes", "triggers", "source"):
        if not fm.get(key):
            problems.append(f"frontmatter missing '{key}'")
    hermes = (fm.get("metadata") or {}).get("hermes") or {}
    for key in ("category", "tags"):
        if not hermes.get(key):
            problems.append(f"frontmatter missing 'metadata.hermes.{key}'")
    if fm.get("name") != os.path.basename(SKILL_DIR):
        problems.append(f"name {fm.get('name')!r} != directory {os.path.basename(SKILL_DIR)!r}")
    return fm


def check_references():
    text = open(SKILL_MD, encoding="utf-8").read()
    refs = sorted(set(re.findall(
        r"(?:references|scripts|tests)/[A-Za-z0-9._\-/]+\.[A-Za-z0-9]{1,5}", text)))
    for r in refs:
        if os.path.exists(os.path.join(SKILL_DIR, r)):
            continue
        # The bare suffix also matches a fully-qualified pointer elsewhere, e.g.
        # `~/.hermes/scripts/rr_styx_enrich.sh` is captured as
        # `scripts/rr_styx_enrich.sh`. If the full path on that line exists, the
        # reference is live and only the prefix is relative.
        if any(os.path.exists(os.path.expanduser(os.path.expandvars(m.group(1))))
               for line in text.splitlines() if r in line
               for m in re.finditer(r"([~$][A-Za-z0-9_./\-]*/[^\s`'\"),;]+)", line)
               if m.group(1).endswith(r)):
            continue
        problems.append(f"dead reference: {r}")
    return refs


def check_map_coverage():
    text = open(SKILL_MD, encoding="utf-8").read()
    m = re.search(r"## Support File Map(.*?)(\n## |\Z)", text, re.S)
    if not m:
        problems.append("no Support File Map section")
        return
    block = m.group(1)
    refdir = os.path.join(SKILL_DIR, "references")
    for f in sorted(os.listdir(refdir)):
        if not f.endswith(".md"):
            continue
        if f not in block:
            problems.append(f"unmapped reference: references/{f}")
    # Conditional language, per the rubric: a static topic list is a D8-3.
    if "When to read" not in block:
        problems.append("Support File Map has no 'When to read' column")


def check_tests():
    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=SKILL_DIR, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["?"])[-1]
        problems.append(f"test suite FAILS: {tail}")
    return r.returncode == 0


if __name__ == "__main__":
    check_frontmatter()
    refs = check_references()
    check_map_coverage()
    tests_ok = check_tests()
    print(f"references cited in SKILL.md: {len(refs)}")
    print(f"test suite: {'OK' if tests_ok else 'FAIL'}")
    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\nAll checks passed.")
