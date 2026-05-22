"""
Watcher service for the local folder (Yandex Disk) auto-import feature.

Reads `config/sources.yaml` (SOURCES_CONFIG env var) to learn which
yadisk root to watch and which subfolders map to workspaces. Only video
files living inside one of those configured subfolders are forwarded to
the API — strays elsewhere under yadisk are silently skipped (logged
once each).

Falls back to the legacy WATCH_PATH env var when no YAML is present, so
existing dev environments keep working.

Two complementary detection strategies:
  1. inotify/watchdog events (IN_CLOSE_WRITE, file rename) — near-real-time
  2. Periodic full scan (SCAN_INTERVAL seconds) — catches anything inotify missed
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import cv2
import httpx
import yaml
from watchdog.events import FileClosedEvent, FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from video_exts import VIDEO_EXTS, is_video_filename

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("watcher")

API_URL        = os.getenv("API_URL", "http://api:8000")
AUTO_ANALYZE   = os.getenv("AUTO_ANALYZE", "false").lower() == "true"
SCAN_INTERVAL  = int(os.getenv("SCAN_INTERVAL", "60"))
STABILITY_WAIT = int(os.getenv("STABILITY_WAIT", "5"))   # seconds
SOURCES_CONFIG = os.getenv("SOURCES_CONFIG", "/app/config/sources.yaml")
LEGACY_WATCH_PATH = os.getenv("WATCH_PATH", "/mnt/yandex-videos")


# ── config loading ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WatchedRoot:
    """One subfolder under yadisk that the watcher will accept videos from."""
    workspace: str
    abs_path: Path


@dataclass(frozen=True)
class WatcherConfig:
    yadisk: Path
    roots: List[WatchedRoot]


def load_config() -> WatcherConfig:
    """Parse SOURCES_CONFIG; fall back to legacy WATCH_PATH (one anonymous
    root) when the YAML is missing so older dev setups still work."""
    p = Path(SOURCES_CONFIG)
    if not p.is_file():
        log.warning(
            "sources config %s missing — falling back to legacy WATCH_PATH=%s",
            p, LEGACY_WATCH_PATH,
        )
        root = Path(LEGACY_WATCH_PATH).resolve()
        return WatcherConfig(yadisk=root, roots=[WatchedRoot(workspace="(legacy)", abs_path=root)])

    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    yadisk = Path(str(raw.get("yadisk") or "")).resolve()
    if str(yadisk) in ("", "."):
        raise SystemExit(f"{p}: 'yadisk' must be set")

    workspaces_raw = raw.get("workspaces") or {}
    if not isinstance(workspaces_raw, dict):
        raise SystemExit(f"{p}: 'workspaces' must be a mapping")

    roots: List[WatchedRoot] = []
    for name, sub in workspaces_raw.items():
        if not isinstance(name, str) or not isinstance(sub, str):
            raise SystemExit(f"{p}: workspace entries must be string → string")
        abs_path = (yadisk / sub).resolve()
        roots.append(WatchedRoot(workspace=name, abs_path=abs_path))

    return WatcherConfig(yadisk=yadisk, roots=roots)


def workspace_for(cfg: WatcherConfig, path: Path) -> Optional[WatchedRoot]:
    """Longest-prefix match: which configured root owns `path`?"""
    try:
        target = path.resolve()
    except OSError:
        target = path
    # Iterate longest-first so a nested subfolder beats a parent.
    for root in sorted(cfg.roots, key=lambda r: len(str(r.abs_path)), reverse=True):
        try:
            target.relative_to(root.abs_path)
        except ValueError:
            continue
        return root
    return None


# ── helpers ──────────────────────────────────────────────────────────────────

def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS


def is_stable(path: Path) -> bool:
    """Return True once the file size stops changing (write complete)."""
    try:
        size_before = path.stat().st_size
        time.sleep(STABILITY_WAIT)
        return path.stat().st_size == size_before and size_before > 0
    except OSError:
        return False


def extract_video_metadata(path: Path) -> dict | None:
    """Fast metadata extraction outside the worker pipeline."""
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        num_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration_s = (num_frames / fps) if fps > 0 else None
        return {
            "fps": fps if fps > 0 else None,
            "duration_s": float(duration_s) if duration_s is not None else None,
            "width": width or None,
            "height": height or None,
            "num_frames": num_frames or None,
        }
    finally:
        cap.release()


# ── registrar ────────────────────────────────────────────────────────────────

class Registrar:
    def __init__(self, cfg: WatcherConfig) -> None:
        self._cfg = cfg
        self._client = httpx.Client(base_url=API_URL, timeout=60)
        self._seen: set[str] = set()        # in-memory dedup for this run
        self._skipped_logged: set[str] = set()
        self._lock = threading.Lock()

    def register(self, path: Path) -> None:
        key = str(path)
        with self._lock:
            if key in self._seen:
                return
        if not is_video(path):
            return

        # Reject anything outside the configured workspace folders before we
        # waste an API call on it.
        ws = workspace_for(self._cfg, path)
        if ws is None:
            with self._lock:
                if key not in self._skipped_logged:
                    self._skipped_logged.add(key)
                    log.info("skip: outside any workspace folder: %s", key)
            return

        if not is_stable(path):
            log.warning("skipping unstable file: %s", path.name)
            return
        try:
            r = self._client.post(
                "/local-folder/register",
                json={"path": key, "auto_analyze": AUTO_ANALYZE},
            )
            r.raise_for_status()
            result = r.json()
            with self._lock:
                self._seen.add(key)
            metadata = extract_video_metadata(path)
            if metadata:
                try:
                    self._client.post(
                        "/local-folder/update-metadata",
                        json={"path": key, **metadata},
                    ).raise_for_status()
                except Exception as exc:
                    log.warning("metadata update failed for %s: %s", path.name, exc)
            if result.get("is_new"):
                log.info(
                    "registered %s → workspace=%s video=%s auto_analyze=%s",
                    path.name, ws.workspace, result["video_id"], AUTO_ANALYZE,
                )
        except httpx.HTTPStatusError as exc:
            log.error("API error registering %s: %s %s",
                      path.name, exc.response.status_code, exc.response.text[:200])
        except Exception as exc:
            log.error("failed to register %s: %s", path.name, exc)

    def full_scan(self) -> None:
        """Walk every configured workspace root and register fresh videos."""
        scanned = 0
        for root in self._cfg.roots:
            if not root.abs_path.exists():
                log.warning("workspace root missing on disk: %s (workspace=%s)",
                            root.abs_path, root.workspace)
                continue
            for p in root.abs_path.rglob("*"):
                if is_video(p):
                    scanned += 1
                    threading.Thread(target=self.register, args=(p,), daemon=True).start()
        log.info("scan complete — found %d video file(s) across %d workspace(s)",
                 scanned, len(self._cfg.roots))


# ── watchdog event handler ────────────────────────────────────────────────────

class VideoHandler(FileSystemEventHandler):
    def __init__(self, registrar: Registrar) -> None:
        super().__init__()
        self._registrar = registrar

    def _handle(self, path_str: str) -> None:
        if not is_video_filename(path_str):
            return
        p = Path(path_str)
        threading.Thread(target=self._registrar.register, args=(p,), daemon=True).start()

    def on_closed(self, event: FileClosedEvent) -> None:
        # Fired on IN_CLOSE_WRITE — file fully written to disk.
        if not event.is_directory:
            self._handle(event.src_path)

    def on_created(self, event: FileCreatedEvent) -> None:
        # Fallback for systems where on_closed isn't fired; stability check compensates.
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        # Yandex Disk CLI often downloads to a .tmp file then renames to final name.
        if not event.is_directory:
            self._handle(event.dest_path)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = load_config()
    log.info(
        "starting — yadisk=%s workspaces=%d AUTO_ANALYZE=%s SCAN_INTERVAL=%ds",
        cfg.yadisk, len(cfg.roots), AUTO_ANALYZE, SCAN_INTERVAL,
    )
    for root in cfg.roots:
        exists = root.abs_path.exists()
        log.info("  workspace=%s root=%s exists=%s",
                 root.workspace, root.abs_path, "yes" if exists else "no")

    registrar = Registrar(cfg)

    # Wait for API to become available (important during docker compose startup).
    for attempt in range(1, 13):
        try:
            httpx.get(f"{API_URL}/healthz", timeout=5).raise_for_status()
            log.info("API is up")
            break
        except Exception:
            log.info("waiting for API... (%d/12)", attempt)
            time.sleep(5)

    # Initial full scan.
    registrar.full_scan()

    # Start watchdog observer at the yadisk root (recursive). Watching the
    # root once is cheaper than schedule-per-subfolder and handles new
    # workspace folders appearing inside yadisk between restarts.
    observer = Observer()
    if not cfg.yadisk.exists():
        log.warning("yadisk root %s does not exist yet — creating", cfg.yadisk)
        cfg.yadisk.mkdir(parents=True, exist_ok=True)
    observer.schedule(VideoHandler(registrar), str(cfg.yadisk), recursive=True)
    observer.start()
    log.info("watching %s (recursive)", cfg.yadisk)

    # Periodic rescan to catch anything inotify may have missed.
    try:
        while True:
            time.sleep(SCAN_INTERVAL)
            registrar.full_scan()
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
