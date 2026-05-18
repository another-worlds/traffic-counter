"""
Watcher service for the local folder (Yandex Disk) auto-import feature.

Watches WATCH_PATH for new video files and registers them with the API.
Runs two complementary detection strategies:
  1. inotify/watchdog events (IN_CLOSE_WRITE, file rename) — near-real-time
  2. Periodic full scan (SCAN_INTERVAL seconds) — catches anything inotify missed
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import httpx
from watchdog.events import FileClosedEvent, FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("watcher")

WATCH_PATH     = Path(os.getenv("WATCH_PATH", "/mnt/yandex-videos"))
API_URL        = os.getenv("API_URL", "http://api:8000")
AUTO_ANALYZE   = os.getenv("AUTO_ANALYZE", "false").lower() == "true"
SCAN_INTERVAL  = int(os.getenv("SCAN_INTERVAL", "60"))
STABILITY_WAIT = int(os.getenv("STABILITY_WAIT", "5"))   # seconds

VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".ts",
    ".m4v", ".wmv", ".mts", ".m2ts", ".webm",
}


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


# ── registrar ────────────────────────────────────────────────────────────────

class Registrar:
    def __init__(self) -> None:
        self._client = httpx.Client(base_url=API_URL, timeout=60)
        self._seen: set[str] = set()      # in-memory dedup for this run
        self._lock = threading.Lock()

    def register(self, path: Path) -> None:
        key = str(path)
        with self._lock:
            if key in self._seen:
                return
        if not is_video(path):
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
            if result.get("is_new"):
                log.info("registered %s → video %s (auto_analyze=%s)",
                         path.name, result["video_id"], AUTO_ANALYZE)
        except httpx.HTTPStatusError as exc:
            log.error("API error registering %s: %s %s",
                      path.name, exc.response.status_code, exc.response.text[:200])
        except Exception as exc:
            log.error("failed to register %s: %s", path.name, exc)

    def full_scan(self) -> None:
        """Walk the entire watch path and register any unindexed videos."""
        if not WATCH_PATH.exists():
            log.warning("watch path does not exist: %s", WATCH_PATH)
            return
        found = 0
        for p in WATCH_PATH.rglob("*"):
            if is_video(p):
                found += 1
                threading.Thread(target=self.register, args=(p,), daemon=True).start()
        log.info("scan complete — found %d video file(s) in %s", found, WATCH_PATH)


# ── watchdog event handler ────────────────────────────────────────────────────

class VideoHandler(FileSystemEventHandler):
    def __init__(self, registrar: Registrar) -> None:
        super().__init__()
        self._registrar = registrar

    def _handle(self, path_str: str) -> None:
        p = Path(path_str)
        if p.suffix.lower() in VIDEO_EXTS:
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
    log.info("starting — WATCH_PATH=%s  AUTO_ANALYZE=%s  SCAN_INTERVAL=%ds",
             WATCH_PATH, AUTO_ANALYZE, SCAN_INTERVAL)

    registrar = Registrar()

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

    # Start watchdog observer.
    observer = Observer()
    WATCH_PATH.mkdir(parents=True, exist_ok=True)
    observer.schedule(VideoHandler(registrar), str(WATCH_PATH), recursive=True)
    observer.start()
    log.info("watching %s", WATCH_PATH)

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
