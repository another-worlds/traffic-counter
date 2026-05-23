"""YAML-driven yadisk source-folder config.

The user mounts one or more Yandex.Disk sync folders on the host and
declares a workspace-per-subfolder mapping in `config/sources.yaml`.
On startup the API reads this file, auto-creates the corresponding
Project rows (idempotent — existing ones are adopted), and exposes a
prefix-match helper the local-folder router uses to route an inbound
absolute path to its owning workspace.

Degraded mode: when the file is missing or malformed the API still
boots — the auto-import feature simply does nothing and a warning is
logged once. The legacy single-WATCH_PATH watcher continues to work
unchanged when no YAML is present.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

import yaml
from sqlalchemy.orm import Session

log = logging.getLogger("api.sources")

DEFAULT_CONFIG_PATH = "/app/config/sources.yaml"


@dataclass(frozen=True)
class WorkspaceSource:
    name: str        # "tashkent-MP" — workspace name as shown in the UI
    subpath: str     # "tashkent-450" — relative to yadisk root
    abs_path: Path   # /mnt/disk1/yandexsync1/tashkent-450 (canonicalized)


@dataclass(frozen=True)
class SourcesConfig:
    yadisk: Path
    workspaces: List[WorkspaceSource] = field(default_factory=list)

    def ordered_for_prefix_match(self) -> List[WorkspaceSource]:
        """Longest abs_path first so the prefix scan picks the most specific."""
        return sorted(
            self.workspaces,
            key=lambda w: len(str(w.abs_path)),
            reverse=True,
        )


@dataclass
class SyncReport:
    adopted: int = 0
    created: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"sources: adopted={self.adopted} created={self.created} "
            f"skipped={self.skipped} errors={len(self.errors)}"
        )


def _config_path() -> Path:
    return Path(os.environ.get("SOURCES_CONFIG", DEFAULT_CONFIG_PATH))


def load_sources(path: Optional[Path] = None) -> Optional[SourcesConfig]:
    """Load and validate the YAML.

    Returns None when the file is missing — callers treat that as
    "auto-import disabled". Raises ValueError for structurally invalid
    YAML (callers log + degrade)."""
    p = path or _config_path()
    if not p.is_file():
        return None

    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"failed to parse {p}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{p}: expected a mapping at the top level")

    yadisk_raw = raw.get("yadisk")
    if not yadisk_raw or not isinstance(yadisk_raw, str):
        raise ValueError(f"{p}: 'yadisk' must be a non-empty string")
    yadisk = Path(yadisk_raw).resolve()

    workspaces_raw = raw.get("workspaces") or {}
    if not isinstance(workspaces_raw, dict):
        raise ValueError(f"{p}: 'workspaces' must be a mapping")

    seen_names: set[str] = set()
    workspaces: List[WorkspaceSource] = []
    for name, subpath in workspaces_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{p}: workspace key must be a non-empty string")
        if not isinstance(subpath, str) or not subpath.strip():
            raise ValueError(f"{p}: workspace '{name}': subpath must be a non-empty string")
        if name in seen_names:
            raise ValueError(f"{p}: duplicate workspace name '{name}'")
        sub = subpath.strip()
        if sub.startswith("/") or ".." in Path(sub).parts:
            raise ValueError(
                f"{p}: workspace '{name}': subpath must be relative to yadisk and "
                f"may not contain '..' (got {subpath!r})"
            )
        abs_path = (yadisk / sub).resolve()
        # Defence-in-depth: even after resolve(), make sure the path didn't
        # escape yadisk via symlinks.
        try:
            abs_path.relative_to(yadisk)
        except ValueError:
            raise ValueError(
                f"{p}: workspace '{name}': resolved path {abs_path} is outside yadisk {yadisk}"
            )
        seen_names.add(name)
        workspaces.append(WorkspaceSource(name=name, subpath=sub, abs_path=abs_path))

    return SourcesConfig(yadisk=yadisk, workspaces=workspaces)


def find_workspace_for_path(
    cfg: Optional[SourcesConfig], abs_path: str | os.PathLike[str]
) -> Optional[WorkspaceSource]:
    """Longest-prefix match of `abs_path` against the workspace roots.

    Returns None if cfg is missing or the path lives outside every
    configured workspace folder.
    """
    if cfg is None:
        return None
    try:
        target = Path(abs_path).resolve()
    except OSError:
        target = Path(abs_path)
    for ws in cfg.ordered_for_prefix_match():
        try:
            target.relative_to(ws.abs_path)
        except ValueError:
            continue
        return ws
    return None


def sync_workspaces(db: Session, cfg: Optional[SourcesConfig]) -> SyncReport:
    """Reconcile YAML workspaces with Project rows. Adopt-existing semantics.

    Never deletes; YAML removals leave existing Projects untouched so the
    user can clean up via the UI when they want to.
    """
    report = SyncReport()
    if cfg is None or not cfg.workspaces:
        return report

    # Local import keeps this module importable from non-API contexts
    # (e.g. the verification script) without pulling SQLAlchemy models.
    from ..models import Project

    for ws in cfg.workspaces:
        try:
            project = db.query(Project).filter(Project.name == ws.name).first()
            if project is None:
                project = Project(
                    name=ws.name,
                    description=f"Yandex Disk: {ws.subpath}",
                    local_source_root=str(ws.abs_path),
                )
                db.add(project)
                report.created += 1
            else:
                if project.local_source_root != str(ws.abs_path):
                    project.local_source_root = str(ws.abs_path)
                report.adopted += 1
        except Exception as exc:  # noqa: BLE001 — best-effort per-row
            report.errors.append(f"{ws.name}: {exc}")
            log.exception("sync_workspaces: failed for %s", ws.name)

    db.commit()
    return report


def workspace_names(cfg: Optional[SourcesConfig]) -> Iterable[str]:
    return [w.name for w in (cfg.workspaces if cfg else [])]
