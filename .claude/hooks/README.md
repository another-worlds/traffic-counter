# Guardrails — `.claude/hooks/`

Enforcement for the AGENTS.md **golden rules**, so the conventions hold across sessions (and across
agents/tools/humans) instead of relying on everyone reading the docs. Defense in depth:

| Layer | File | Binds | What it does |
|---|---|---|---|
| Permissions | `.claude/settings.json` → `permissions.deny` | Claude Code sessions | hard-denies `Edit`/`Write`/`NotebookEdit` into `macromodel/**` |
| PreToolUse hook | `.claude/hooks/guard.py` | this session's tool calls | blocks the rules below; **authoritative** in-session enforcement |
| CI | `.github/workflows/platform-ci.yml` | every push/PR | contract tests + frozen-diff + model-id scan — binds *everything* |
| SessionStart | `.claude/hooks/session-start.sh` | (unwired) | optional web-sandbox dev bring-up |

## What `guard.py` blocks (exit 2 = blocked)

- **Frozen / core edits** — writes to `macromodel/` (frozen legacy) or the counter core
  (`api/ worker/ watcher/ frontend/ config/ infra/ scripts/`). Active work is in `platform/`.
  Covers the `Edit`/`Write`/`NotebookEdit` tools *and* shell writes (`>`, `tee`, `sed -i`, `rm`, …).
- **Commits/pushes to `main`/`master`** — `git commit|merge|rebase|push|…` while on a protected
  branch, or any `git push` that names `main`/`master` as a target.
- **Secrets / model identifiers** in written content — Claude model ids (`claude-opus-…`,
  `anthropic.claude-…`), private-key blocks, AWS keys, tokens. (Infra dirs `.claude/`, `.github/`
  are exempt — they legitimately contain these patterns.)

The guard **fails open**: any internal error allows the call (a guard bug must never brick work).

## Deliberate overrides

The golden rule says "don't touch the core *unless explicitly asked*." When you *were* asked:

```bash
GUARD_ALLOW=macromodel   # allow edits under macromodel/
GUARD_ALLOW=core         # allow edits under the counter core
GUARD_ALLOW=all          # allow any protected area
GUARD_DISABLE=1          # disable every check (last resort)
```

Set the variable in the environment the hook runs in. The `macromodel/**` *permission* deny in
`settings.json` is independent — to edit the frozen app you must also relax that rule (rare by
design; ADR-0002 keeps `macromodel/` frozen until the rewrite reaches parity).

In **CI**, bypass the frozen-diff check by putting `[allow-legacy]` in a commit message.

## Enabling the SessionStart bring-up (opt-in)

`session-start.sh` is shipped but **not wired** — it auto-runs code each session, so enable it
deliberately. Add to `.claude/settings.json`:

```json
"SessionStart": [
  { "hooks": [ { "type": "command",
      "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh\"" } ] }
]
```

then export `PLATFORM_DEV_AUTOSTART=1`. Until then it no-ops (prints one reminder line).

## Testing the guard

```bash
# blocked (exit 2):
echo '{"tool_name":"Edit","tool_input":{"file_path":"macromodel/x.py","new_string":"y"}}' \
  | python3 .claude/hooks/guard.py; echo "exit=$?"
# allowed (exit 0):
echo '{"tool_name":"Edit","tool_input":{"file_path":"platform/core-api/app/x.py","new_string":"y"}}' \
  | python3 .claude/hooks/guard.py; echo "exit=$?"
```
