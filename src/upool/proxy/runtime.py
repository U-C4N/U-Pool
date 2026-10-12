"""Pinned runtime installation and ownership of per-account child processes.

Nothing starts or downloads until an explicit install/login/request action.
Only paths created beneath U-Pool's redirected state directory are managed.
"""

from __future__ import annotations

import ctypes
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from dataclasses import dataclass, field

from .. import paths
from ..atomicio import write_json, write_text
from .models import ProxyError, UpstreamResponse


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _managed_path(path: Path, base: Path) -> Path:
    if not path.resolve().is_relative_to(base.resolve()) or path.resolve() == base.resolve():
        raise ProxyError("The managed runtime path is outside U-Pool state.", 400, "invalid_path")
    return path


_MANAGED_FS_RETRY_SECONDS = 2.0


def _retry_managed_fs(operation):
    """Let Windows release owned process handles without altering permissions."""
    deadline = time.monotonic() + _MANAGED_FS_RETRY_SECONDS
    while True:
        try:
            return operation()
        except OSError as error:
            if (sys.platform != "win32" or getattr(error, "winerror", None) not in (5, 32)
                    or time.monotonic() >= deadline):
                raise
            time.sleep(0.05)


def extract_zip(archive: Path, destination: Path) -> None:
    """Reject traversal, symlinks and excessive expansion before writing files."""
    root = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        if sum(item.file_size for item in package.infolist()) > 3 * 1024**3:
            raise ProxyError("Runtime archive exceeds the installation size limit.", code="invalid_archive")
        for item in package.infolist():
            target = (root / item.filename.replace("\\", "/")).resolve()
            if not target.is_relative_to(root) or re.match(r"^[A-Za-z]:", item.filename):
                raise ProxyError("Runtime archive contains an unsafe path.", code="invalid_archive")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ProxyError("Runtime archive contains a symbolic link.", code="invalid_archive")
        package.extractall(root)


class RuntimeInstaller:
    def __init__(self, manifest: Path | None = None) -> None:
        self._manifest_path = manifest
        self._lock = threading.RLock()
        self._status: dict[str, tuple[str, str | None]] = {}

    def manifest(self) -> dict:
        candidates = [self._manifest_path] if self._manifest_path else [
            paths.bundle_root() / "proxy-runtimes" / "runtime-manifest.json",
            paths.bundle_root() / "build" / "proxy-runtimes" / "runtime-manifest.json",
            paths.bundle_root() / "runtime-manifest.json",
        ]
        for candidate in candidates:
            if candidate and candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                data["_directory"] = str(candidate.parent)
                return data
        raise ProxyError("The pinned runtime manifest is missing.", code="runtime_missing")

    def components(self) -> list[dict]:
        data = self.manifest()
        worker = dict(data["worker"])
        return [*data["components"], worker]

    def directory(self, component: dict) -> Path:
        return _managed_path(paths.runtimes_dir() / component["id"] / component["version"], paths.runtimes_dir())

    def executable(self, identifier: str) -> Path:
        component = next(c for c in self.components() if c["id"] == identifier)
        executable = self.directory(component) / component["executable"]
        if not self.installed(component):
            raise ProxyError(f"Install the {identifier} runtime from the API tab first.", 503, "runtime_missing")
        return executable

    def installed(self, component: dict) -> bool:
        directory = self.directory(component)
        try:
            marker = json.loads((directory / "installed.json").read_text(encoding="utf-8"))
            return bool(component.get("sha256") and marker["sha256"] == component["sha256"]
                        and (directory / component["executable"]).is_file())
        except (OSError, ValueError, KeyError):
            return False

    def snapshot(self) -> list[dict]:
        result = []
        for component in self.components():
            installed = self.installed(component)
            status, error = self._status.get(component["id"], ("installed" if installed else "missing", None))
            if component["id"] == "chatgpt-worker" and not component.get("sha256"):
                error = "The release worker has not been built. Run scripts/prepare_proxy_runtimes.py."
            result.append({"id": component["id"], "version": component["version"],
                           "installed": installed, "status": status, "error": error})
        return result

    def install(self) -> list[dict]:
        with self._lock:
            if sys.platform != "win32":
                raise ProxyError("Managed API runtimes require Windows x64.", 400, "unsupported_platform")
            # Refuse incomplete release packages before downloading large files.
            components = self.components()
            if any(not c.get("sha256") for c in components):
                raise ProxyError("The packaged ChatGPT worker is missing. Build the proxy runtimes before packaging U-Pool.", 503, "runtime_missing")
            for component in components:
                if self.installed(component):
                    continue
                identifier = component["id"]
                self._status[identifier] = ("installing", None)
                try:
                    self._install_component(component)
                    self._status[identifier] = ("installed", None)
                except Exception:
                    self._status[identifier] = ("error", "Runtime installation failed; verify network access and retry.")
                    raise ProxyError(f"Could not install {identifier}; the existing installation was preserved.", 503, "runtime_install_failed") from None
            return self.snapshot()

    def _install_component(self, component: dict) -> None:
        base = paths.runtimes_dir()
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="install-", dir=base) as temporary:
            stage = Path(temporary)
            archive = stage / "runtime.zip"
            if component.get("bundle"):
                source = (Path(self.manifest()["_directory"]) / component["bundle"]).resolve()
                if not source.is_relative_to(Path(self.manifest()["_directory"]).resolve()):
                    raise ValueError("Unsafe bundled archive")
                shutil.copyfile(source, archive)
            else:
                url = component["url"]
                if urllib.parse.urlparse(url).scheme != "https":
                    raise ValueError("Runtime downloads require HTTPS")
                request = urllib.request.Request(url, headers={"User-Agent": "U-Pool/1.0"})
                with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
                    if urllib.parse.urlparse(response.url).scheme != "https":
                        raise ValueError("Insecure runtime redirect")
                    shutil.copyfileobj(response, output)
            if not secrets.compare_digest(_sha256(archive), component["sha256"]):
                raise ValueError("Runtime checksum does not match the pinned manifest")
            extracted = stage / "extracted"
            extracted.mkdir()
            extract_zip(archive, extracted)
            if not (extracted / component["executable"]).is_file():
                raise ValueError("Runtime executable missing from archive")
            write_json(extracted / "installed.json", {"version": component["version"], "sha256": component["sha256"]})
            target = self.directory(component)
            target.parent.mkdir(parents=True, exist_ok=True)
            old = target.with_name(target.name + ".previous")
            _managed_path(old, base)
            if old.exists():
                shutil.rmtree(old)
            if target.exists():
                target.rename(old)
            try:
                extracted.rename(target)
            except BaseException:
                if old.exists():
                    old.rename(target)
                raise
            if old.exists():
                shutil.rmtree(old)


class OwnedProcess:
    """A private process group/job; closing never enumerates unrelated Chrome."""
    def __init__(self, argv: list[str], cwd: Path, env: dict | None = None) -> None:
        self.job = None
        if sys.platform == "win32":
            self._start_windows(argv, cwd, env)
        else:
            self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            start_new_session=True)

    def _start_windows(self, argv: list[str], cwd: Path, env: dict | None) -> None:
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.job = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.job or not kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            if self.job:
                kernel.CloseHandle(self.job)
            raise ProxyError("Could not create the managed process job.", 503, "process_failed")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        try:
            self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            startupinfo=startup, creationflags=0x4 | subprocess.CREATE_NO_WINDOW)
            if not kernel.AssignProcessToJobObject(self.job, int(self.process._handle)):
                self.process.kill()
                raise ProxyError("Could not contain the managed child process.", 503, "process_failed")
            resume = ctypes.WinDLL("ntdll").NtResumeProcess
            resume.argtypes = [wintypes.HANDLE]
            if resume(int(self.process._handle)) != 0:
                self.process.kill()
                raise ProxyError("Could not start the managed child process.", 503, "process_failed")
        except BaseException:
            kernel.CloseHandle(self.job)
            self.job = None
            raise

    def close(self) -> None:
        if self.job:
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
            class Accounting(ctypes.Structure):
                _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                            ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                            ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                            ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]
            try:
                kernel.TerminateJobObject(self.job, 0)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    accounting = Accounting()
                    if not kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                        break
                    if not accounting.ActiveProcesses:
                        break
                    time.sleep(0.05)
            finally:
                kernel.CloseHandle(self.job)
                self.job = None
        elif self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if sys.platform != "win32":
                os.killpg(self.process.pid, signal.SIGKILL)
            else:
                self.process.kill()
            self.process.wait(timeout=5)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@dataclass
class _Instance:
    account_id: str
    provider: str
    port: int
    key: str
    management_key: str
    process: OwnedProcess
    active: int = 0
    touched: float = field(default_factory=time.monotonic)


class _InterruptibleReader(io.RawIOBase):
    """Windows shutdown does not wake every pending socket-file read.

    Read only after select says data is ready, checking cancellation between
    polls. Keep the original socket file alive until the response closes so
    HTTPConnection may safely detach a Connection: close response.
    """
    def __init__(self, sock, keepalive, finished: threading.Event,
                 cancelled: threading.Event | None, timeout: float) -> None:
        self._sock, self._keepalive = sock, keepalive
        self._finished, self._cancelled, self._timeout = finished, cancelled, timeout

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        deadline = time.monotonic() + self._timeout
        while True:
            if self._finished.is_set() or (self._cancelled and self._cancelled.is_set()):
                raise ProxyError("The client cancelled the request.", 499, "request_cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Managed upstream response timed out")
            readable, _, _ = select.select([self._sock], [], [], min(remaining, 0.1))
            if readable:
                if self._cancelled and self._cancelled.is_set():
                    raise ProxyError("The client cancelled the request.", 499, "request_cancelled")
                try:
                    return self._sock.recv_into(buffer)
                except BlockingIOError:
                    continue

    def close(self) -> None:
        if not self.closed:
            self._keepalive.close()
        super().close()


class ManagedEngines:
    def __init__(self, installer: RuntimeInstaller | None = None) -> None:
        self.installer = installer or RuntimeInstaller()
        self._instances: dict[str, _Instance] = {}
        self._lock = threading.RLock()

    def snapshot(self) -> list[dict]:
        return self.installer.snapshot()

    def install(self) -> list[dict]:
        return self.installer.install()

    def account_dir(self, identifier: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
            raise ProxyError("Invalid managed account identifier.", 400, "invalid_account")
        return _managed_path(paths.proxy_dir() / "accounts" / identifier, paths.proxy_dir() / "accounts")

    def _start(self, account: dict) -> _Instance:
        with self._lock:
            identifier, provider = account["id"], account["provider"]
            existing = self._instances.get(identifier)
            if existing and existing.process.process.poll() is None:
                healthy = True
                if provider == "chatgpt":
                    try:
                        healthy = self._json(existing, "/health").get("status") == "ready"
                    except ProxyError:
                        healthy = False
                if healthy:
                    existing.touched = time.monotonic()
                    return existing
            if existing:
                self.stop_account(identifier)
            if provider == "chatgpt":
                browsers = [i for i in self._instances.values() if i.provider == "chatgpt"]
                if len(browsers) >= 2:
                    available = sorted((i for i in browsers if not i.active), key=lambda i: i.touched)
                    if not available:
                        raise ProxyError("Both managed ChatGPT browsers are busy.", 503, "browsers_busy")
                    self.stop_account(available[0].account_id)
            directory = self.account_dir(identifier)
            directory.mkdir(parents=True, exist_ok=True)
            port, key, management = _free_port(), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            if provider == "chatgpt":
                exe = self.installer.executable("chatgpt-worker")
                chrome = self.installer.executable("chrome")
                config = {"port": port, "api_key": key, "chrome": str(chrome), "cdp_port": _free_port(),
                          "profile": str(directory / "profile"), "state": str(directory / "worker"),
                          "account_id": identifier}
                config_path = directory / "worker-config.json"
                write_json(config_path, config, secret=True)
                argv = [str(exe), "--config", str(config_path)]
            elif provider == "grok":
                exe = self.installer.executable("cliproxyapi")
                auth = directory / "auth"
                auth.mkdir(exist_ok=True)
                config_path = directory / "config.yaml"
                # JSON scalar quoting is a valid YAML subset and escapes Windows paths.
                write_text(config_path,
                    "config-version: 8\nserver:\n  host: 127.0.0.1\n  port: " + str(port) +
                    "\n  commercial-mode: true\nmanagement:\n  allow-remote: false\n  secret-key: " + json.dumps(management) +
                    "\n  disable-control-panel: true\n  disable-auto-update-panel: true\naccess:\n  api-keys:\n    - " + json.dumps(key) +
                    "\noauth:\n  auth-dir: " + json.dumps(str(auth)) +
                    "\nrouting:\n  retry:\n    request-retry: 0\n    max-retry-interval: 0\n" +
                    "observability:\n  logs:\n    debug: false\n    logging-to-file: false\n    request-log: false\n  usage:\n    usage-statistics-enabled: false\n",
                    secret=True)
                argv = [str(exe), "-config", str(config_path), "-local-model"]
            else:
                raise ProxyError("Unknown API provider.", 400, "invalid_provider")
            # Do not inherit the user's provider/GitHub tokens or their HOME.
            # The managed executables get only the OS environment they need.
            os_names = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP", "PATHEXT",
                        "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}
            environment = {name: value for name, value in os.environ.items() if name.upper() in os_names}
            environment.update({"USERPROFILE": str(directory), "HOME": str(directory),
                                "LOCALAPPDATA": str(directory / "Local"), "APPDATA": str(directory / "Roaming")})
            process = OwnedProcess(argv, directory, environment)
            instance = _Instance(identifier, provider, port, key, management, process)
            self._instances[identifier] = instance
            for _ in range(150):
                if process.process.poll() is not None:
                    self.stop_account(identifier)
                    raise ProxyError("The managed engine exited during startup.", 503, "engine_failed")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        self._restore_chatgpt_session(instance, account)
                        return instance
                except OSError:
                    time.sleep(0.1)
            self.stop_account(identifier)
            raise ProxyError("The managed engine did not become ready.", 503, "engine_failed")

    def _restore_chatgpt_session(self, instance: _Instance, account: dict) -> None:
        """Restore imported session cookies once after a browser restart.

        Session cookies may disappear when the owned process job stops. Probe
        the current profile first so a valid manual login is never overwritten
        by an older cookie export. Calls go directly to the ready instance.
        """
        cookies = account.get("cookies")
        if account.get("provider") != "chatgpt" or not cookies:
            return
        try:
            self._json(instance, "/internal/facts", {})
        except ProxyError as error:
            if error.status != 401:
                return
        else:
            return
        try:
            self._json(instance, "/internal/verify", {"cookies": cookies})
        except ProxyError:
            # Keep an expired profile available for /internal/reveal and user
            # login. Its normal facts/generation routes report their own error.
            pass

    def _json(self, instance: _Instance, endpoint: str, payload: dict | None = None, method: str | None = None, management: bool = False) -> dict:
        response = self._request(instance, endpoint, payload, method=method, management=management)
        try:
            raw = response.body if isinstance(response.body, bytes) else b"".join(response.body)
            if response.status >= 400:
                raise ProxyError("The upstream account action failed; reconnect the account or retry.", response.status, "account_action_failed")
            return json.loads(raw)
        finally:
            if response.close:
                response.close()

    def _account_json(self, account: dict, endpoint: str, payload: dict | None = None,
                      method: str | None = None, management: bool = False) -> dict:
        with self._lock:
            instance = self._start(account)
            instance.active += 1
        try:
            return self._json(instance, endpoint, payload, method, management)
        finally:
            with self._lock:
                instance.active -= 1
                instance.touched = time.monotonic()

    def _request(self, instance: _Instance, endpoint: str, payload: dict | None = None, method: str | None = None,
                 management: bool = False, cancelled: threading.Event | None = None) -> UpstreamResponse:
        connection = http.client.HTTPConnection("127.0.0.1", instance.port, timeout=5 if endpoint == "/health" else 180)
        key = instance.management_key if management else instance.key
        body = json.dumps(payload).encode() if payload is not None else None
        finished = threading.Event()
        response = None
        class Response(http.client.HTTPResponse):
            def __init__(self, sock, *args, **kwargs):
                super().__init__(sock, *args, **kwargs)
                self.fp = io.BufferedReader(_InterruptibleReader(sock, self.fp, finished, cancelled, connection.timeout))
        connection.response_class = Response
        def shutdown_socket():
            raw = getattr(getattr(response, "fp", None), "raw", None)
            active_socket = connection.sock or getattr(raw, "_sock", None)
            if active_socket:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return True
            return False
        def watch_cancel():
            while not finished.wait(0.05):
                if cancelled.is_set():
                    if shutdown_socket():
                        return
        if cancelled:
            if cancelled.is_set():
                raise ProxyError("The client cancelled the request.", 499, "request_cancelled")
            threading.Thread(target=watch_cancel, name="upool-engine-cancel", daemon=True).start()
        try:
            connection.request(method or ("POST" if body is not None else "GET"), endpoint, body,
                               {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            response = connection.getresponse()
        except (OSError, http.client.HTTPException, ProxyError):
            finished.set()
            connection.close()
            if cancelled and cancelled.is_set():
                raise ProxyError("The client cancelled the request.", 499, "request_cancelled") from None
            raise ProxyError("The managed engine connection failed.", 502, "engine_failed") from None
        headers = {name.lower(): value for name, value in response.getheaders()
                   if name.lower() in {"content-type", "retry-after"}}
        if "text/event-stream" not in headers.get("content-type", ""):
            try:
                raw = response.read(16 * 1024 * 1024 + 1)
                if cancelled and cancelled.is_set():
                    raise ProxyError("The client cancelled the request.", 499, "request_cancelled")
            except (OSError, http.client.HTTPException):
                if cancelled and cancelled.is_set():
                    raise ProxyError("The client cancelled the request.", 499, "request_cancelled") from None
                raise ProxyError("The managed engine response failed.", 502, "engine_failed") from None
            finally:
                finished.set()
                response.close()
                connection.close()
            if len(raw) > 16 * 1024 * 1024:
                raise ProxyError("Upstream response exceeds the size limit.", code="response_too_large")
            return UpstreamResponse(response.status, headers, raw)
        abort_lock = threading.Lock()
        aborted = False
        def abort():
            nonlocal aborted
            with abort_lock:
                if aborted:
                    return
                aborted = True
                # HTTPResponse's socket file may outlive HTTPConnection.sock.
                # Shutdown before closing it to wake a thread blocked in readline.
                finished.set()
                shutdown_socket()
                response.close()
                connection.close()
        def chunks():
            try:
                while data := response.readline(1024 * 1024):
                    if cancelled and cancelled.is_set():
                        raise ProxyError("The client cancelled the request.", 499, "request_cancelled")
                    yield data
                if cancelled and cancelled.is_set():
                    raise ProxyError("The client cancelled the request.", 499, "request_cancelled")
            except (OSError, http.client.HTTPException):
                if cancelled and cancelled.is_set():
                    raise ProxyError("The client cancelled the request.", 499, "request_cancelled") from None
                raise ProxyError("The managed engine stream failed.", 502, "engine_failed") from None
            finally:
                abort()
        return UpstreamResponse(response.status, headers, chunks(), abort)

    def verify_chatgpt(self, account: dict, cookies: list[dict]) -> dict:
        return self._account_json(account, "/internal/verify", {"cookies": cookies})

    def refresh_chatgpt(self, account: dict) -> dict:
        return self._account_json(account, "/internal/facts", {})

    def begin_grok(self, account: dict) -> dict:
        result = self._account_json(account, "/v8/management/oauth/auth-url?provider=xai", management=True)
        return {name: result.get(name, "") for name in ("state", "url", "user_code", "expires_in")}

    def poll_grok(self, account: dict, state: str) -> dict:
        result = self._account_json(account, "/v8/management/oauth/status?" + urllib.parse.urlencode({"state": state}), management=True)
        status = result.get("status")
        if status == "ok":
            # Read only the specific newly-generated isolated auth directory.
            facts = {}
            for file in (self.account_dir(account["id"]) / "auth").glob("*.json"):
                try:
                    metadata = json.loads(file.read_text(encoding="utf-8"))
                    if metadata.get("type") == "xai":
                        facts = {"email": metadata.get("email", ""), "user_id": metadata.get("sub", ""),
                                 "name": metadata.get("email") or "Grok", "models": self.models(account)}
                        break
                except (OSError, ValueError):
                    continue
            if not facts:
                return {"status": "error", "error": "OAuth completed without a usable account record."}
            return {"status": "ok", **facts}
        return {"status": status or "error", "error": "Grok authentication failed." if status == "error" else None}

    def cancel_grok(self, account: dict, state: str) -> None:
        if account["id"] in self._instances:
            self._account_json(account, "/v8/management/oauth/session?" + urllib.parse.urlencode({"state": state}), method="DELETE", management=True)

    def models(self, account: dict) -> list[str]:
        result = self._account_json(account, "/v1/models")
        return [item["id"] for item in result.get("data", []) if isinstance(item.get("id"), str)
                and not any(part in item["id"].lower() for part in ("image", "video", "audio", "imagine"))]

    def complete(self, account: dict, endpoint: str, payload: dict) -> UpstreamResponse:
        return self._complete(account, endpoint, payload)

    def complete_cancellable(self, account: dict, endpoint: str, payload: dict,
                             cancelled: threading.Event) -> UpstreamResponse:
        return self._complete(account, endpoint, payload, cancelled)

    def _complete(self, account: dict, endpoint: str, payload: dict,
                  cancelled: threading.Event | None = None) -> UpstreamResponse:
        endpoint = endpoint if endpoint.startswith("/v1/") else "/v1/" + endpoint.lstrip("/")
        with self._lock:
            instance = self._start(account)
            instance.active += 1
        try:
            response = (self._request(instance, endpoint, payload, cancelled=cancelled)
                        if cancelled else self._request(instance, endpoint, payload))
        except BaseException:
            with self._lock:
                instance.active -= 1
            raise
        closed = False
        close_lock = threading.Lock()
        upstream_close = response.close
        def close():
            nonlocal closed
            with close_lock:
                if closed:
                    return
                closed = True
                if upstream_close:
                    upstream_close()
                with self._lock:
                    instance.active -= 1
                    instance.touched = time.monotonic()
        response.close = close
        return response

    def open_profile(self, account: dict) -> dict:
        if account["provider"] != "chatgpt":
            raise ProxyError("Grok uses device login; it has no managed browser profile.", 400, "invalid_provider")
        return self._account_json(account, "/internal/reveal", {})

    def adopt_account(self, from_id: str, to_id: str) -> None:
        with self._lock:
            source, target = self.account_dir(from_id), self.account_dir(to_id)
            if source == target:
                return
            base = paths.proxy_dir() / "accounts"
            backup = _managed_path(base / ("." + to_id + ".previous-" + secrets.token_hex(16)), base)
            saved_previous = False
            try:
                self.stop_account(from_id)
                self.stop_account(to_id)
                if not source.is_dir():
                    raise ProxyError("The verified managed profile is missing. Import the account again.",
                                     503, "account_replace_failed")
                # Never recursively delete the existing profile before the new
                # verified profile has committed to its final account path.
                if target.exists():
                    _retry_managed_fs(lambda: _managed_path(target, base).rename(_managed_path(backup, base)))
                    saved_previous = True
                try:
                    _retry_managed_fs(lambda: _managed_path(source, base).rename(_managed_path(target, base)))
                except BaseException:
                    if saved_previous:
                        try:
                            _retry_managed_fs(lambda: _managed_path(backup, base).rename(_managed_path(target, base)))
                        except OSError:
                            raise ProxyError("Managed account replacement failed. The previous profile is preserved in U-Pool state.",
                                             503, "account_restore_failed") from None
                    raise
            except OSError:
                raise ProxyError("The managed profile is still in use. Stop its runtime and retry the account import.",
                                 503, "account_replace_failed") from None
            if saved_previous:
                try:
                    _retry_managed_fs(lambda: shutil.rmtree(_managed_path(backup, base)))
                except (OSError, ProxyError):
                    # The new profile is already installed. Keep a recoverable
                    # managed backup rather than report a failed adoption.
                    pass

    def stop_account(self, identifier: str) -> None:
        with self._lock:
            instance = self._instances.pop(identifier, None)
            if instance:
                instance.process.close()

    def delete_account_data(self, identifier: str) -> None:
        with self._lock:
            directory = self.account_dir(identifier)
            self.stop_account(identifier)
            base = paths.proxy_dir() / "accounts"
            backup_name = re.compile(r"\." + re.escape(identifier) + r"\.previous-[0-9a-f]{32}")
            try:
                # A completed adoption can retain an old profile if Windows
                # temporarily locks its cleanup. Account deletion must purge
                # that account's credentials too, without touching other IDs.
                if base.is_dir():
                    for backup in base.iterdir():
                        if backup_name.fullmatch(backup.name) and backup.is_dir():
                            _retry_managed_fs(lambda: shutil.rmtree(_managed_path(backup, base)))
                # Remove backups first: a locked backup keeps the live profile
                # and its account record available for an explicit retry.
                if directory.exists():
                    _retry_managed_fs(lambda: shutil.rmtree(_managed_path(directory, base)))
            except OSError:
                raise ProxyError("Managed account data is still in use. Stop its runtime and retry deleting the account.",
                                 503, "account_delete_failed") from None

    def stop_all(self) -> None:
        with self._lock:
            for identifier in list(self._instances):
                self.stop_account(identifier)
