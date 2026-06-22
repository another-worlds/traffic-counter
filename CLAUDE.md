@AGENTS.md

## Claude Code specifics

- The canonical guide is `AGENTS.md` (imported above) so other agents/tools share it.
  Put only Claude-Code-specific notes here; keep durable conventions in `AGENTS.md`.
- Active work is in **`platform/`**, which has its own `README.md` and
  `docs/data-contract.md` — consult them (and `platform/ROADMAP.md`) when editing there.
- In Claude Code on the web, the Docker daemon isn't auto-started and the egress proxy does TLS
  interception — follow the "Running in Claude Code on the web" steps in `AGENTS.md` to bring up
  the `platform/` dev environment before running `docker compose` / the tests.
