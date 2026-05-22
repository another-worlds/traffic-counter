#!/usr/bin/env python3
"""Verify that config/sources.yaml is consistent with what's on disk.

Reads the YAML, walks each configured workspace folder, probes every
video file's duration via ffprobe, and prints a human-readable summary
of what the app will see. Optional --db flag previews the workspace
sync (which Project rows would be adopted vs created).

Designed to be launched directly from bash:

    ./scripts/verify_sources.py
    ./scripts/verify_sources.py --config /etc/traffic-counter/sources.yaml
    ./scripts/verify_sources.py --db
    ./scripts/verify_sources.py --json

Exit codes:
    0   every listed workspace exists on disk
    2   at least one workspace folder is missing
    3   strict-duration mode and at least one duration couldn't be probed
    4   YAML parse / validation error
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "verify_sources: PyYAML is required.\n"
        "Install with `pip install pyyaml` or run inside the api container:\n"
        "  docker compose exec api python /app/scripts/verify_sources.py\n"
    )
    sys.exit(1)

# Standalone copy of the watcher's VIDEO_EXTS so the script has no
# dependency on the api package; if you change one, change both.
VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".ts",
    ".m4v", ".wmv", ".mts", ".m2ts", ".webm",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "??"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def fmt_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} B"
        f /= 1024
    return f"{n} B"


def fmt_mtime(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


# ── ffprobe with on-disk cache ───────────────────────────────────────────────

def probe_duration(path: Path, timeout: float = 10.0) -> Optional[float]:
    """Return seconds, or None on any failure (corrupt file, timeout)."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    raw = out.stdout.strip()
    if not raw:
        return None
    try:
        v = float(raw)
        return v if v > 0 else None
    except ValueError:
        return None


class DurationCache:
    """Cache ffprobe results keyed by (abs_path, mtime, size).

    The cache is a JSON file alongside the config so re-runs over the
    same files are near-instant.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: Dict[str, Dict[str, Any]] = {}
        if path.is_file():
            try:
                self.data = json.loads(path.read_text("utf-8")) or {}
            except (OSError, json.JSONDecodeError):
                self.data = {}

    def key(self, p: Path) -> Optional[Tuple[str, int, int]]:
        try:
            st = p.stat()
        except OSError:
            return None
        return (str(p), int(st.st_mtime), int(st.st_size))

    def get(self, p: Path) -> Tuple[Optional[float], bool]:
        """Return (duration_or_None, hit)."""
        k = self.key(p)
        if k is None:
            return (None, False)
        rec = self.data.get(k[0])
        if not rec:
            return (None, False)
        if int(rec.get("mtime", -1)) != k[1] or int(rec.get("size", -1)) != k[2]:
            return (None, False)
        return (rec.get("duration_s"), True)

    def set(self, p: Path, duration_s: Optional[float]) -> None:
        k = self.key(p)
        if k is None:
            return
        self.data[k[0]] = {"mtime": k[1], "size": k[2], "duration_s": duration_s}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"warning: failed to write duration cache: {exc}", file=sys.stderr)


# ── filesystem walk ──────────────────────────────────────────────────────────

@dataclass
class FileInfo:
    path: Path
    size: int
    mtime: float
    duration_s: Optional[float] = None


def find_videos(root: Path) -> List[FileInfo]:
    out: List[FileInfo] = []
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXTS:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append(FileInfo(path=p, size=st.st_size, mtime=st.st_mtime))
    return out


def annotate_durations(
    files: List[FileInfo],
    cache: DurationCache,
    *,
    use_cache: bool,
    workers: int = 8,
) -> Tuple[int, int]:
    """Fill in FileInfo.duration_s for every file. Returns (cache_hits, probed)."""
    hits = 0
    probed = 0
    to_probe: List[FileInfo] = []
    for f in files:
        if use_cache:
            cached, hit = cache.get(f.path)
            if hit:
                f.duration_s = cached
                hits += 1
                continue
        to_probe.append(f)
    if not to_probe:
        return hits, probed
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(probe_duration, f.path): f for f in to_probe}
        for fut in as_completed(futures):
            f = futures[fut]
            try:
                f.duration_s = fut.result()
            except Exception:
                f.duration_s = None
            cache.set(f.path, f.duration_s)
            probed += 1
    return hits, probed


# ── YAML loading ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkspaceEntry:
    name: str
    subpath: str
    abs_path: Path


@dataclass
class SourcesYaml:
    config_path: Path
    yadisk: Path
    workspaces: List[WorkspaceEntry] = field(default_factory=list)


def load_yaml(path: Path) -> SourcesYaml:
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a top-level mapping")
    yadisk_raw = raw.get("yadisk")
    if not isinstance(yadisk_raw, str) or not yadisk_raw.strip():
        raise ValueError(f"{path}: 'yadisk' must be a non-empty string")
    yadisk = Path(yadisk_raw).resolve()
    ws_raw = raw.get("workspaces") or {}
    if not isinstance(ws_raw, dict):
        raise ValueError(f"{path}: 'workspaces' must be a mapping")
    seen: set[str] = set()
    workspaces: List[WorkspaceEntry] = []
    for name, sub in ws_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}: workspace name must be a non-empty string")
        if not isinstance(sub, str) or not sub.strip():
            raise ValueError(f"{path}: workspace {name!r}: subpath must be a non-empty string")
        if name in seen:
            raise ValueError(f"{path}: duplicate workspace name {name!r}")
        if sub.startswith("/") or ".." in Path(sub).parts:
            raise ValueError(
                f"{path}: workspace {name!r}: subpath must be relative without '..'"
            )
        seen.add(name)
        workspaces.append(
            WorkspaceEntry(name=name, subpath=sub, abs_path=(yadisk / sub).resolve())
        )
    return SourcesYaml(config_path=path, yadisk=yadisk, workspaces=workspaces)


# ── strays ────────────────────────────────────────────────────────────────────

def find_strays(cfg: SourcesYaml) -> List[FileInfo]:
    """Videos under yadisk but outside every workspace folder."""
    out: List[FileInfo] = []
    if not cfg.yadisk.exists():
        return out
    roots = [w.abs_path for w in cfg.workspaces]
    for p in cfg.yadisk.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        inside = False
        for r in roots:
            try:
                resolved.relative_to(r)
                inside = True
                break
            except ValueError:
                pass
        if inside:
            continue
        try:
            st = p.stat()
            out.append(FileInfo(path=p, size=st.st_size, mtime=st.st_mtime))
        except OSError:
            pass
    return out


# ── DB preview ───────────────────────────────────────────────────────────────

def db_preview(workspaces: List[WorkspaceEntry]) -> Optional[List[Dict[str, Any]]]:
    """Return adopt/create plan from DATABASE_URL; None if unavailable."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    try:
        from sqlalchemy import create_engine, text  # type: ignore
    except ImportError:
        return None
    try:
        engine = create_engine(url, future=True)
    except Exception:
        return None
    plan: List[Dict[str, Any]] = []
    try:
        with engine.connect() as conn:
            for ws in workspaces:
                row = conn.execute(
                    text("SELECT id, local_source_root FROM projects WHERE name = :n"),
                    {"n": ws.name},
                ).first()
                if row is None:
                    plan.append({"workspace": ws.name, "action": "CREATE"})
                else:
                    plan.append({
                        "workspace": ws.name,
                        "action": "ADOPT",
                        "project_id": str(row[0]),
                        "current_root": row[1],
                        "yaml_root": str(ws.abs_path),
                    })
    except Exception as exc:
        return [{"error": str(exc)}]
    return plan


# ── output ───────────────────────────────────────────────────────────────────

def human_output(
    cfg: SourcesYaml,
    per_ws: Dict[str, List[FileInfo]],
    strays: List[FileInfo],
    db_plan: Optional[List[Dict[str, Any]]],
) -> Tuple[str, int, int]:
    lines: List[str] = []
    yadisk_exists = cfg.yadisk.is_dir()
    lines.append(f"yadisk:  {cfg.yadisk}")
    lines.append(f"status:  {'✓ exists, mounted, readable' if yadisk_exists else '✗ MISSING'}")
    lines.append("")
    lines.append(f"workspaces ({len(cfg.workspaces)}):")

    total_videos = 0
    total_bytes = 0
    total_dur = 0.0
    total_unprobed = 0
    missing_ws = 0

    for ws in cfg.workspaces:
        files = per_ws.get(ws.name, [])
        exists = ws.abs_path.is_dir()
        lines.append(f"  {ws.name:<18} → {ws.abs_path}")
        if not exists:
            lines.append("    ✗ missing folder")
            missing_ws += 1
            continue
        count = len(files)
        size = sum(f.size for f in files)
        durs = [f.duration_s for f in files if f.duration_s is not None]
        unprobed = sum(1 for f in files if f.duration_s is None)
        total_videos += count
        total_bytes += size
        total_dur += sum(durs)
        total_unprobed += unprobed
        ws_dur = sum(durs)
        latest = max((f.mtime for f in files), default=None)
        head = (
            f"    ✓ exists · {count} file(s) · {fmt_bytes(size)} · "
            f"{fmt_duration(ws_dur)} total"
        )
        if latest is not None:
            head += f" · latest {fmt_mtime(latest)}"
        if unprobed:
            head += f" · {unprobed} undetermined"
        lines.append(head)
        if files:
            sample = sorted(files, key=lambda f: f.mtime, reverse=True)[:2]
            lines.append("    sample:")
            for f in sample:
                lines.append(
                    f"      {f.path.name:<40}  {fmt_duration(f.duration_s):>9}  {fmt_bytes(f.size):>9}"
                )
            if count > len(sample):
                lines.append(f"      … {count - len(sample)} more")

    lines.append("")
    if strays:
        lines.append(
            f"ignored: {len(strays)} video file(s) inside yadisk but outside any listed workspace"
        )
        for f in strays[:8]:
            rel = f.path.relative_to(cfg.yadisk) if cfg.yadisk in f.path.parents or f.path == cfg.yadisk else f.path
            lines.append(f"  /{rel}   {fmt_duration(f.duration_s)}")
        if len(strays) > 8:
            lines.append(f"  … {len(strays) - 8} more")
    else:
        lines.append("ignored: 0 stray video file(s)")

    if db_plan is not None:
        lines.append("")
        lines.append("DB sync preview:")
        for entry in db_plan:
            if "error" in entry:
                lines.append(f"  error: {entry['error']}")
                continue
            if entry["action"] == "CREATE":
                lines.append(f"  {entry['workspace']:<18} → would CREATE new project")
            else:
                shift = ""
                if entry.get("current_root") and entry["current_root"] != entry["yaml_root"]:
                    shift = f" (root: {entry['current_root']} → {entry['yaml_root']})"
                lines.append(
                    f"  {entry['workspace']:<18} → would ADOPT existing project "
                    f"(id {entry['project_id'][:8]}…){shift}"
                )

    lines.append("")
    have = sum(1 for w in cfg.workspaces if w.abs_path.is_dir())
    lines.append(
        f"Summary: {have}/{len(cfg.workspaces)} workspace paths exist · "
        f"{total_videos} videos · {fmt_duration(total_dur)} · {fmt_bytes(total_bytes)} · "
        f"{len(strays)} ignored"
    )

    return "\n".join(lines) + "\n", missing_ws, total_unprobed


def json_output(
    cfg: SourcesYaml,
    per_ws: Dict[str, List[FileInfo]],
    strays: List[FileInfo],
    db_plan: Optional[List[Dict[str, Any]]],
) -> str:
    def file_dict(f: FileInfo) -> Dict[str, Any]:
        return {
            "path": str(f.path),
            "size_bytes": f.size,
            "mtime_iso": _dt.datetime.fromtimestamp(f.mtime).isoformat(timespec="seconds"),
            "duration_s": f.duration_s,
        }

    doc: Dict[str, Any] = {
        "yadisk": str(cfg.yadisk),
        "yadisk_exists": cfg.yadisk.is_dir(),
        "workspaces": [
            {
                "name": ws.name,
                "subpath": ws.subpath,
                "abs_path": str(ws.abs_path),
                "exists": ws.abs_path.is_dir(),
                "videos": [file_dict(f) for f in per_ws.get(ws.name, [])],
            }
            for ws in cfg.workspaces
        ],
        "ignored": [file_dict(f) for f in strays],
    }
    if db_plan is not None:
        doc["db_preview"] = db_plan
    return json.dumps(doc, indent=2) + "\n"


# ── main ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Verify config/sources.yaml against disk.")
    default_cfg = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"
    ap.add_argument("--config", type=Path, default=default_cfg, help="path to sources.yaml")
    ap.add_argument("--db", action="store_true", help="show DB workspace-sync preview")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-cache", action="store_true", help="bypass the ffprobe duration cache")
    ap.add_argument("--workers", type=int, default=8, help="parallel ffprobe workers (default 8)")
    ap.add_argument(
        "--strict-duration",
        action="store_true",
        help="exit non-zero if any duration could not be probed",
    )
    args = ap.parse_args(argv)

    cfg_path: Path = args.config
    if not cfg_path.is_file():
        sys.stderr.write(f"verify_sources: config not found: {cfg_path}\n")
        return 4

    try:
        cfg = load_yaml(cfg_path)
    except (yaml.YAMLError, ValueError) as exc:
        sys.stderr.write(f"verify_sources: {exc}\n")
        return 4

    if not shutil.which("ffprobe"):
        sys.stderr.write(
            "warning: ffprobe not found on PATH — durations will be reported as ??\n"
            "         install ffmpeg locally or run via "
            "`docker compose exec api python /app/scripts/verify_sources.py`\n"
        )

    cache_path = cfg_path.with_suffix(cfg_path.suffix + ".durations.json")
    cache = DurationCache(cache_path)

    per_ws: Dict[str, List[FileInfo]] = {}
    for ws in cfg.workspaces:
        per_ws[ws.name] = find_videos(ws.abs_path)

    # Probe durations across all listed workspaces in one batch.
    all_files = [f for files in per_ws.values() for f in files]
    annotate_durations(
        all_files, cache, use_cache=not args.no_cache, workers=max(1, args.workers)
    )

    strays = find_strays(cfg)
    annotate_durations(
        strays, cache, use_cache=not args.no_cache, workers=max(1, args.workers)
    )

    if not args.no_cache:
        cache.save()

    db_plan = db_preview(cfg.workspaces) if args.db else None

    if args.json:
        sys.stdout.write(json_output(cfg, per_ws, strays, db_plan))
        missing_ws = sum(1 for w in cfg.workspaces if not w.abs_path.is_dir())
        total_unprobed = sum(
            1 for f in all_files + strays if f.duration_s is None
        )
    else:
        text, missing_ws, total_unprobed = human_output(cfg, per_ws, strays, db_plan)
        sys.stdout.write(text)

    if missing_ws:
        return 2
    if args.strict_duration and total_unprobed:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
