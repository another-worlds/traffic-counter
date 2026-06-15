#!/usr/bin/env python3
"""PreToolUse guard for the traffic-counter repo.

Turns the AGENTS.md golden rules into hard checks (not just advice):

  1. No edits to the frozen legacy ``macromodel/`` or the ``traffic-counter`` core
     (root ``api/ worker/ watcher/ frontend/ config/ infra/ scripts/``) — active work is in ``platform/``.
  2. No commits/pushes to ``main``/``master``.
  3. No secrets or Claude model identifiers baked into written content.

Wired via ``.claude/settings.json`` (PreToolUse, matcher ``Edit|Write|MultiEdit|NotebookEdit|Bash``).
Contract: a JSON event arrives on stdin; **exit 0 = allow, exit 2 = block** (stderr is shown to
Claude). Fails *open* on any internal error so a guard bug can never brick the workflow.

Deliberate overrides (documented in ``.claude/hooks/README.md``):
  GUARD_ALLOW=macromodel|core|all   permit edits in that protected area (when explicitly asked)
  GUARD_DISABLE=1                    disable every check
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Protected areas, relative to repo root. Active work lives in platform/ (never protected).
FROZEN = {
    "macromodel": ("macromodel/",),
    "core": ("api/", "worker/", "watcher/", "frontend/", "config/", "infra/", "scripts/"),
}
# Infra dirs may legitimately contain model-id regexes etc. — exempt from the content scan.
INFRA_EXEMPT = (".claude/", ".github/")

SECRET_PATTERNS = [
    ("Claude model identifier", re.compile(r"claude-(?:opus|sonnet|haiku|fable)-\d", re.I)),
    ("Claude model identifier", re.compile(r"anthropic\.claude-", re.I)),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
]

_FROZEN_DIR_RE = r"(?:\./)?(macromodel|api|worker|watcher|frontend|config|infra|scripts)/"
# Shell write-operators whose *target* is the matched path (read-only uses like `cat`/`grep` are fine).
WRITE_OP_RES = [
    re.compile(r">>?\s*['\"]?" + _FROZEN_DIR_RE),
    re.compile(r"\btee\s+(?:-a\s+)?['\"]?" + _FROZEN_DIR_RE),
    re.compile(r"\bsed\s+-i\S*\s+[^|;&]*?" + _FROZEN_DIR_RE),
    re.compile(r"\b(?:rm|truncate)\s+[^|;&]*?" + _FROZEN_DIR_RE),
    re.compile(r"\bdd\b[^|;&]*of=['\"]?" + _FROZEN_DIR_RE),
]


def deny(msg: str):
    sys.stderr.write("⛔ guard: " + msg + "\n")
    raise SystemExit(2)


def project_dir(ev) -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or ev.get("cwd") or os.getcwd()


def rel_path(fp, proj):
    if not fp:
        return None
    p = fp if os.path.isabs(fp) else os.path.join(proj, fp)
    return os.path.relpath(os.path.normpath(p), os.path.normpath(proj)).replace(os.sep, "/")


def frozen_group(rel):
    if not rel or rel.startswith(".."):
        return None
    for group, prefixes in FROZEN.items():
        for pre in prefixes:
            if rel == pre.rstrip("/") or rel.startswith(pre):
                return group
    return None


def written_text(ti) -> str:
    out = []
    for key in ("content", "new_string", "new_source"):
        val = ti.get(key)
        if isinstance(val, str):
            out.append(val)
    for edit in ti.get("edits") or []:
        if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
            out.append(edit["new_string"])
    return "\n".join(out)


def git_branch(proj) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", proj, "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return ""


def check_write(tool, ti, proj, allow):
    rel = rel_path(ti.get("file_path") or ti.get("notebook_path"), proj)
    group = frozen_group(rel)
    if group and group not in allow and "all" not in allow:
        area = "the frozen legacy macromodel/" if group == "macromodel" else "the traffic-counter core"
        deny(f"{rel} is in {area} — AGENTS.md: active work is in platform/, don't modify this area "
             f"unless explicitly asked.\n"
             f"   If you were explicitly asked to, set environment GUARD_ALLOW={group} and retry.")
    if not (rel and rel.startswith(INFRA_EXEMPT)):
        blob = written_text(ti)
        for label, rx in SECRET_PATTERNS:
            m = rx.search(blob)
            if m:
                deny(f"writing {rel} would introduce a {label} ('{m.group(0)[:40]}') — AGENTS.md: "
                     f"don't bake secrets or model identifiers into committed files.")


def check_bash(ti, proj, allow):
    cmd = ti.get("command", "") or ""
    if re.search(r"\bgit\b", cmd):
        branch = git_branch(proj)
        if branch in ("main", "master") and re.search(
                r"\bgit\s+(?:commit|merge|rebase|cherry-pick|revert|push)\b", cmd):
            deny(f"on protected branch '{branch}' — AGENTS.md: never commit to main. "
                 f"Create/switch to a feature branch first.")
        push = re.search(r"\bgit\s+push\b[^|;&\n]*", cmd)
        if push and re.search(r"(?<![\w/-])(?:main|master)(?![\w/-])", push.group(0)):
            deny("refusing 'git push' that names main/master as a target. Push to your feature branch.")
    if "all" in allow:
        return
    for rx in WRITE_OP_RES:
        m = rx.search(cmd)
        if m:
            d = m.group(1)
            group = "macromodel" if d == "macromodel" else "core"
            if group not in allow:
                deny(f"shell command writes to protected '{d}/' ({group}). Work in platform/, "
                     f"or set GUARD_ALLOW={group} if explicitly asked.")


def main():
    ev = json.loads(sys.stdin.read() or "{}")
    if os.environ.get("GUARD_DISABLE") == "1":
        return
    allow = {x.strip() for x in os.environ.get("GUARD_ALLOW", "").split(",") if x.strip()}
    tool = ev.get("tool_name", "")
    ti = ev.get("tool_input") or {}
    proj = project_dir(ev)
    if tool in WRITE_TOOLS:
        check_write(tool, ti, proj, allow)
    elif tool == "Bash":
        check_bash(ti, proj, allow)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail OPEN — a guard bug must never block legitimate work
        sys.stderr.write(f"guard: internal error, allowing ({exc})\n")
        sys.exit(0)
