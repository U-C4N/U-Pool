"""Download a newer release and put it in place of this one.

Three things make this awkward on Windows, and each one shapes the design:

*Progress* - pywebview has no reliable way to push to the page from a worker
thread, so the work happens in a thread that only ever mutates a lock-guarded
:class:`Progress` snapshot. The UI polls :meth:`Updater.snapshot` through the
same envelope as every other endpoint.

*A running exe cannot be replaced* - so nothing is overwritten. The new version
is extracted to a sibling of the install folder and a detached ``cmd`` script
waits for this process to exit, renames the old folder aside, renames the new one
into place, and starts it. Two renames on one volume, with the first one undone if
the second fails.

*No code signature* - the archive is checked by length, by SHA-256 when GitHub
published a digest, and member by member for CRC and path traversal. When there
is no digest to check against, the UI is told so rather than implying more.

Anything that cannot be done safely is refused with a sentence explaining why -
running from source, or an install under Program Files - and the UI falls back to
opening the release page.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__, atomicio, paths, release, settings
from .models import UPoolError

PHASE_IDLE = "idle"
PHASE_CHECKING = "checking"
PHASE_UP_TO_DATE = "up_to_date"
PHASE_AVAILABLE = "available"
PHASE_DOWNLOADING = "downloading"
PHASE_VERIFYING = "verifying"
PHASE_STAGING = "staging"
PHASE_RELAUNCHING = "relaunching"
PHASE_ERROR = "error"

# Phases where the UI should keep polling.
BUSY_PHASES = (PHASE_CHECKING, PHASE_DOWNLOADING, PHASE_VERIFYING, PHASE_STAGING)

KIND_FROZEN = "frozen"
KIND_SOURCE = "source"

CHECK_INTERVAL = 6 * 60 * 60  # seconds; GitHub allows 60 unauthenticated req/hr
CHUNK = 256 * 1024
DOWNLOAD_TIMEOUT = 60.0
# A bundle smaller than this is not a real one, whatever the archive claims.
MIN_EXE_BYTES = 1024 * 1024

EXE_NAME = "U-Pool.exe"
INTERNAL_DIR = "_internal"
LATEST_CACHE = "latest.json"
SWAP_RESULT = "last-swap.txt"
OLD_PREFIX = ".U-Pool.old-"
NEW_PREFIX = ".U-Pool.new-"

SOURCE_BLOCKER = (
    "This copy runs from source, so there is nothing to replace. Update it with "
    "git pull, then pip install -e . and npm run build in ui/."
)
NOT_WINDOWS_BLOCKER = "Installing an update in place is wired up for Windows only."
NOT_A_BUNDLE_BLOCKER = "This copy does not look like the packaged bundle, so it is left alone."


# --------------------------------------------------------------------- progress


@dataclass
class Progress:
    """Everything the Updates section of the UI draws, in one snapshot."""

    phase: str = PHASE_IDLE
    percent: int | None = None
    detail: str = ""
    release: dict[str, Any] | None = None
    error: str = ""
    kind: str = KIND_FROZEN
    can_install: bool = False
    blocker: str = ""
    verified: str = ""
    skipped_version: str = ""
    last_check: int = 0
    current_version: str = __version__
    # Set once, on the launch after a swap, so the UI can say the update landed.
    installed_from: str = ""
    install_failed: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"busy": self.phase in BUSY_PHASES}


# ------------------------------------------------------------------- locations


def install_dir() -> Path | None:
    """The folder that would be replaced, or ``None`` if this is not a bundle."""
    if not getattr(sys, "frozen", False):
        return None
    root = Path(sys.executable).resolve().parent
    if (root / EXE_NAME).exists() and (root / INTERNAL_DIR).is_dir():
        return root
    return None


def can_self_update() -> tuple[bool, str]:
    """Whether an in-place install is possible, and why not when it is not."""
    if sys.platform != "win32":
        return False, NOT_WINDOWS_BLOCKER
    if not getattr(sys, "frozen", False):
        return False, SOURCE_BLOCKER
    root = install_dir()
    if root is None:
        return False, NOT_A_BUNDLE_BLOCKER
    try:
        # The swap renames folders *inside the parent*, so that is the directory
        # whose permissions decide whether this can work at all.
        probe = tempfile.mkdtemp(dir=str(root.parent), prefix=".upool-probe-")
        os.rmdir(probe)
    except OSError:
        return False, (
            f"U-Pool is installed in {root.parent}, where Windows will not let it replace "
            "itself without administrator rights. Move the U-Pool folder somewhere you own "
            "(your user folder, for instance), or download the new version yourself."
        )
    return True, ""


# -------------------------------------------------------------------- download


def _sidecar(archive: Path) -> Path:
    return archive.with_suffix(archive.suffix + ".json")


def _cached(rel: release.Release) -> Path | None:
    """A previous download of exactly this asset, or ``None``.

    Makes a retry after a failed swap free, and means reopening Settings never
    re-downloads what is already sitting there.
    """
    archive = paths.update_cache_dir() / rel.asset_name
    if not archive.exists():
        return None
    try:
        meta = atomicio.read_json(_sidecar(archive), None)
    except ValueError:
        return None
    if not isinstance(meta, dict) or not meta.get("complete"):
        return None
    if int(meta.get("size") or 0) != archive.stat().st_size:
        return None
    if rel.asset_size and archive.stat().st_size != rel.asset_size:
        return None
    if rel.asset_sha256 and str(meta.get("sha256") or "") != rel.asset_sha256:
        return None
    return archive


def _open_download(url: str, offset: int) -> Any:
    request = urllib.request.Request(url, method="GET")
    request.add_header("user-agent", release.USER_AGENT)
    request.add_header("accept", "application/octet-stream")
    if offset:
        request.add_header("range", f"bytes={offset}-")
    return urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT)


def download(rel: release.Release, on_bytes: Callable[[int, int], None] | None = None) -> Path:
    """Fetch the release archive into the cache and return its path.

    Resumes a half-finished ``.part`` with a range request, so a dropped
    connection does not mean starting over.
    """
    if not rel.has_asset:
        raise UPoolError("This release has no Windows download attached to it.")
    if not release.is_allowed_url(rel.asset_url):
        raise UPoolError("The download URL is not on GitHub, so it was not followed.")

    cache = paths.update_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / rel.asset_name
    existing = _cached(rel)
    if existing is not None:
        if on_bytes is not None:
            on_bytes(existing.stat().st_size, existing.stat().st_size)
        return existing

    part = archive.with_suffix(archive.suffix + ".part")
    offset = part.stat().st_size if part.exists() else 0
    digest = hashlib.sha256()
    try:
        with _open_download(rel.asset_url, offset) as response:
            if not release.is_allowed_url(response.url):
                raise UPoolError("The download redirected off GitHub, so it was abandoned.")
            resumed = response.status == 206 and offset > 0
            if resumed:
                with part.open("rb") as handle:
                    for block in iter(lambda: handle.read(CHUNK), b""):
                        digest.update(block)
            else:
                offset = 0
            mode = "ab" if resumed else "wb"
            expected = rel.asset_size or (offset + int(response.headers.get("content-length") or 0))
            written = offset
            with part.open(mode) as handle:
                for block in iter(lambda: response.read(CHUNK), b""):
                    handle.write(block)
                    digest.update(block)
                    written += len(block)
                    if on_bytes is not None:
                        on_bytes(written, expected)
    except urllib.error.HTTPError as exc:
        raise UPoolError(f"The download failed with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise UPoolError(f"The download could not be completed: {exc.reason}") from exc
    except OSError as exc:
        raise UPoolError(f"The download could not be written to {cache}: {exc}") from exc

    if rel.asset_size and written != rel.asset_size:
        part.unlink(missing_ok=True)
        raise UPoolError(
            f"The download is {written} bytes but the release says {rel.asset_size}; it was discarded."
        )
    checksum = digest.hexdigest()
    if rel.asset_sha256 and checksum != rel.asset_sha256:
        part.unlink(missing_ok=True)
        raise UPoolError("The download does not match the checksum GitHub published; it was discarded.")

    os.replace(part, archive)
    atomicio.write_json(
        _sidecar(archive),
        {"url": rel.asset_url, "size": archive.stat().st_size, "sha256": checksum, "complete": True},
    )
    for stale in cache.iterdir():
        if stale.name not in (archive.name, _sidecar(archive).name):
            stale.unlink(missing_ok=True)
    return archive


# ---------------------------------------------------------------------- verify


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Members that are plain files/dirs inside the archive, or an error.

    ``extractall`` trusts the names in the archive; a release zip is downloaded
    from the internet, so every name is checked before anything is written.
    """
    members = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        unsafe = (
            name.startswith("/")
            or ".." in name.split("/")
            or Path(name).is_absolute()
            # A drive letter, which ``is_absolute`` misses on POSIX.
            or (len(name) > 1 and name[1] == ":")
        )
        if unsafe:
            raise UPoolError(
                f"The archive contains an unsafe path ({info.filename}); it was rejected."
            )
        members.append(info)
    return members


def extract(archive: Path, target: Path) -> None:
    """Unpack ``archive`` into an empty ``target``, checking as we go."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            members = _safe_members(zf)
            broken = zf.testzip()
            if broken is not None:
                raise UPoolError(f"The archive is damaged around {broken}; it was discarded.")
            root = target.resolve()
            for info in members:
                destination = (root / info.filename.replace("\\", "/")).resolve()
                if root not in destination.parents and destination != root:
                    raise UPoolError("The archive tried to write outside the staging folder.")
                zf.extract(info, root)
    except zipfile.BadZipFile as exc:
        raise UPoolError("The download is not a readable zip archive.") from exc

    _flatten(target)
    exe = target / EXE_NAME
    if not exe.exists() or exe.stat().st_size < MIN_EXE_BYTES:
        raise UPoolError(f"The archive does not contain a usable {EXE_NAME}.")
    if not (target / INTERNAL_DIR).is_dir():
        raise UPoolError(f"The archive is missing its {INTERNAL_DIR} folder.")


def _flatten(target: Path) -> None:
    """Lift a single wrapping folder, so ``U-Pool/U-Pool.exe`` becomes the root."""
    entries = list(target.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        return
    inner = entries[0]
    for item in list(inner.iterdir()):
        shutil.move(str(item), str(target / item.name))
    inner.rmdir()


# ------------------------------------------------------------------------ swap

# Sleeps with ping because ``timeout /t`` needs a real console on stdin, and this
# script is started detached. Absolute System32 paths so PATH cannot be hijacked.
SWAP_SCRIPT = r"""@echo off
setlocal
set "SYS=%SystemRoot%\System32"
set "PID={pid}"
set "CUR={cur}"
set "OLD={old}"
set "NEW={new}"
set "RESULT={result}"
set /a TRIES=0
:wait
"%SYS%\tasklist.exe" /FI "PID eq %PID%" /NH 2>nul | "%SYS%\find.exe" /I "%PID%" >nul || goto gone
set /a TRIES+=1
if %TRIES% GEQ 60 ( >"%RESULT%" echo timeout & goto relaunch )
"%SYS%\ping.exe" -n 2 127.0.0.1 >nul
goto wait
:gone
"%SYS%\ping.exe" -n 2 127.0.0.1 >nul
move "%CUR%" "%OLD%" >nul 2>&1 || ( >"%RESULT%" echo move_out_failed & goto relaunch )
move "%NEW%" "%CUR%" >nul 2>&1 || (
  move "%OLD%" "%CUR%" >nul 2>&1 && ( >"%RESULT%" echo rolled_back ) || ( >"%RESULT%" echo broken )
  goto relaunch
)
>"%RESULT%" echo ok {version}
:relaunch
if exist "%CUR%\{exe}" start "" "%CUR%\{exe}"
rmdir /s /q "%OLD%" 2>nul
(goto) 2>nul & del "%~f0"
"""


def write_swap_script(current: Path, staged: Path, retired: Path, version: str) -> Path:
    directory = paths.update_dir()
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / f"swap-{version}.cmd"
    atomicio.write_text(
        script,
        SWAP_SCRIPT.format(
            pid=os.getpid(),
            cur=current,
            old=retired,
            new=staged,
            result=directory / SWAP_RESULT,
            version=version,
            exe=EXE_NAME,
        ),
    )
    return script


def spawn_swap(script: Path) -> None:
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, name, 0)
    subprocess.Popen(  # noqa: S603 - a script this module just wrote
        [str(script)],
        # Never the install folder: cmd.exe holds a handle on its own working
        # directory, which would block the very rename it is there to perform.
        cwd=str(paths.app_home()),
        close_fds=True,
        creationflags=flags,
    )


def reconcile() -> dict[str, str]:
    """Tidy up after a swap and report how the last one went.

    Called on every launch. Never raises: housekeeping is not a reason to keep a
    window from appearing.
    """
    outcome: dict[str, str] = {}
    directory = paths.update_dir()
    marker = directory / SWAP_RESULT
    try:
        if marker.exists():
            words = marker.read_text(encoding="utf-8").strip().split()
            marker.unlink(missing_ok=True)
            status = words[0] if words else ""
            if status == "ok":
                outcome["installed_from"] = words[1] if len(words) > 1 else ""
            elif status:
                outcome["install_failed"] = status
    except OSError:
        pass

    root = install_dir()
    if root is not None:
        for prefix in (OLD_PREFIX, NEW_PREFIX):
            for stale in root.parent.glob(prefix + "*"):
                shutil.rmtree(stale, ignore_errors=True)
    for script in directory.glob("swap-*.cmd"):
        try:
            script.unlink(missing_ok=True)
        except OSError:
            pass
    return outcome


# --------------------------------------------------------------------- updater


@dataclass
class _Thread:
    handle: threading.Thread | None = None


class Updater:
    """Owns the update state and the two background jobs that change it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._worker = _Thread()
        can_install, blocker = can_self_update()
        stored = settings.load()
        self._p = Progress(
            kind=KIND_FROZEN if getattr(sys, "frozen", False) else KIND_SOURCE,
            can_install=can_install,
            blocker=blocker,
            skipped_version=str(stored.get("update_skipped_version") or ""),
            last_check=int(stored.get("update_last_check") or 0),
            release=self._remembered(),
        )
        outcome = reconcile()
        self._p.installed_from = outcome.get("installed_from", "")
        self._p.install_failed = outcome.get("install_failed", "")
        if self._p.release and release.is_newer(
            str(self._p.release.get("version") or ""), __version__
        ):
            self._p.phase = PHASE_AVAILABLE

    # ------------------------------------------------------------------ state

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._p.to_dict()

    def _set(self, **fields: Any) -> None:
        with self._lock:
            for name, value in fields.items():
                setattr(self._p, name, value)

    def _busy(self) -> bool:
        handle = self._worker.handle
        return handle is not None and handle.is_alive()

    def _start(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        self._worker.handle = thread
        thread.start()

    @staticmethod
    def _remembered() -> dict[str, Any] | None:
        """The last release we heard about, so an offline launch still knows."""
        try:
            data = atomicio.read_json(paths.update_dir() / LATEST_CACHE, None)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------ check

    def check_async(self, *, force: bool = False) -> dict[str, Any]:
        if self._busy():
            return self.snapshot()
        stored = settings.load()
        if not force and not stored.get("update_check_enabled"):
            return self.snapshot()
        age = time.time() - int(stored.get("update_last_check") or 0)
        if not force and age < CHECK_INTERVAL:
            return self.snapshot()
        self._set(phase=PHASE_CHECKING, detail="Asking GitHub…", error="", percent=None)
        self._start(self._check, "upool-update-check")
        return self.snapshot()

    def _check(self) -> None:
        try:
            latest = release.fetch_latest()
        except UPoolError as exc:
            # A failed check is not an error state to sit in: keep whatever we
            # last knew and show the reason next to it.
            with self._lock:
                known = self._p.release
                newer = bool(known) and release.is_newer(
                    str(known.get("version") or ""), __version__
                )
                self._p.phase = PHASE_AVAILABLE if newer else PHASE_IDLE
                self._p.error = str(exc)
                self._p.detail = ""
            return

        payload = latest.to_dict()
        try:
            atomicio.write_json(paths.update_dir() / LATEST_CACHE, payload)
        except OSError:
            pass
        now = int(time.time())
        try:
            settings.update(
                {"update_last_check": now, "update_last_seen_version": latest.version}
            )
        except OSError:
            pass

        newer = release.is_newer(latest.version, __version__)
        self._set(
            release=payload,
            error="",
            last_check=now,
            phase=PHASE_AVAILABLE if newer else PHASE_UP_TO_DATE,
            detail="" if newer else f"U-Pool {__version__} is the latest version.",
        )

    def skip(self, version: str) -> dict[str, Any]:
        wanted = str(version).strip()
        try:
            settings.update({"update_skipped_version": wanted})
        except OSError as exc:
            raise UPoolError(f"That choice could not be saved: {exc}") from exc
        self._set(skipped_version=wanted)
        return self.snapshot()

    def set_checks_enabled(self, enabled: bool) -> dict[str, Any]:
        try:
            settings.update({"update_check_enabled": bool(enabled)})
        except OSError as exc:
            raise UPoolError(f"That choice could not be saved: {exc}") from exc
        return self.snapshot()

    # ---------------------------------------------------------------- install

    def install_async(self) -> dict[str, Any]:
        if self._busy():
            return self.snapshot()
        can_install, blocker = can_self_update()
        self._set(can_install=can_install, blocker=blocker)
        if not can_install:
            raise UPoolError(blocker)
        with self._lock:
            payload = self._p.release
        if not payload or not payload.get("has_asset"):
            raise UPoolError("There is no download attached to the latest release.")
        if not release.is_newer(str(payload.get("version") or ""), __version__):
            raise UPoolError(f"U-Pool {__version__} is already the latest version.")
        self._set(phase=PHASE_DOWNLOADING, percent=0, detail="Starting download…", error="")
        self._start(self._install, "upool-update-install")
        return self.snapshot()

    def _install(self) -> None:
        with self._lock:
            payload = dict(self._p.release or {})
        rel = release.Release(
            tag=str(payload.get("tag") or ""),
            version=str(payload.get("version") or ""),
            notes="",
            html_url=str(payload.get("html_url") or ""),
            published_at="",
            asset_name=str(payload.get("asset_name") or ""),
            asset_url=str(payload.get("asset_url") or ""),
            asset_size=int(payload.get("asset_size") or 0),
            asset_sha256=str(payload.get("asset_sha256") or ""),
        )
        try:
            archive = download(rel, self._on_bytes)
            self._set(
                phase=PHASE_VERIFYING,
                percent=84,
                detail="Checking the download…",
                verified=(
                    "checksum published by GitHub"
                    if rel.asset_sha256
                    else "length and archive CRC only - GitHub published no checksum"
                ),
            )
            root = install_dir()
            if root is None:  # pragma: no cover - can_self_update already refused
                raise UPoolError(NOT_A_BUNDLE_BLOCKER)
            staged = root.parent / f"{NEW_PREFIX}{rel.version}"
            self._set(phase=PHASE_STAGING, percent=90, detail="Unpacking…")
            extract(archive, staged)

            retired = root.parent / f"{OLD_PREFIX}{__version__}"
            shutil.rmtree(retired, ignore_errors=True)
            script = write_swap_script(root, staged, retired, rel.version)
            spawn_swap(script)
        except UPoolError as exc:
            self._set(phase=PHASE_ERROR, percent=None, detail="", error=str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - never leave the UI mid-install
            self._set(
                phase=PHASE_ERROR,
                percent=None,
                detail="",
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        self._set(
            phase=PHASE_RELAUNCHING,
            percent=100,
            detail=f"Restarting into {rel.version}…",
        )
        # The UI closes the window from the webview thread. If that hangs, the
        # swap script would spin for a minute and give up, so force the exit.
        threading.Thread(target=self._force_exit, name="upool-update-exit", daemon=True).start()

    def _on_bytes(self, written: int, expected: int) -> None:
        percent = int(written / expected * 80) if expected else None
        detail = f"{written // 1024 // 1024} MB of {expected // 1024 // 1024} MB" if expected else ""
        self._set(phase=PHASE_DOWNLOADING, percent=percent, detail=detail)

    @staticmethod
    def _force_exit(delay: float = 6.0) -> None:  # pragma: no cover - process exit
        time.sleep(delay)
        os._exit(0)
