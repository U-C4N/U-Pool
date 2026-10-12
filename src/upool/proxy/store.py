"""Keep proxy accounts separate from provider configuration migrations."""

from __future__ import annotations

import copy
import secrets
import threading
from typing import Any

from .. import atomicio, paths
from .models import ProxyError

PUBLIC_FIELDS = ("id", "provider", "name", "email", "enabled", "status", "models",
                 "last_checked", "cooldown_until", "error")


class AccountStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] | None = None

    def data(self) -> dict[str, Any]:
        with self._lock:
            if self._data is None:
                try:
                    raw = atomicio.read_json(paths.proxy_dir() / "accounts.json", None)
                except (ValueError, OSError):
                    raise ProxyError("Proxy account storage cannot be read. Restore its backup before continuing.", 500, "storage_error") from None
                if raw is not None and (not isinstance(raw, dict) or not isinstance(raw.get("accounts"), list)):
                    raise ProxyError("Proxy account storage is invalid.", 500, "storage_error")
                candidate = raw if raw is not None else {"version": 1, "port": 8318, "key": "", "accounts": []}
                if candidate.get("version") != 1:
                    raise ProxyError("Unsupported proxy account storage version.", 500, "storage_error")
                port = candidate.get("port")
                if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535 or not isinstance(candidate.get("key"), str):
                    raise ProxyError("Proxy account storage is invalid.", 500, "storage_error")
                account_ids: set[str] = set()
                for account in candidate["accounts"]:
                    account_id = account.get("id") if isinstance(account, dict) else None
                    if not isinstance(account_id, str) or not account_id or account_id in account_ids or account.get("provider") not in ("grok", "chatgpt"):
                        raise ProxyError("Proxy account storage is invalid.", 500, "storage_error")
                    account_ids.add(account_id)
                    if account.get("status") == "busy":
                        account["status"] = "ready"
                self._data = candidate
            return copy.deepcopy(self._data)

    def save(self, data: dict[str, Any]) -> None:
        atomicio.write_json(paths.proxy_dir() / "accounts.json", data, secret=True)
        self._data = copy.deepcopy(data)

    def key(self) -> str:
        with self._lock:
            data = self.data()
            if not data.get("key"):
                data["key"] = "upool_" + secrets.token_urlsafe(32)
                self.save(data)
            return str(data["key"])

    def set_port(self, port: int) -> None:
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ProxyError("Choose a port between 1024 and 65535.", 400, "invalid_port")
        with self._lock:
            data = self.data()
            data["port"] = port
            self.save(data)

    def accounts(self) -> list[dict[str, Any]]:
        return self.data()["accounts"]

    def get(self, account_id: str) -> dict[str, Any]:
        for account in self.accounts():
            if account["id"] == account_id:
                return account
        raise ProxyError("Account not found.", 404, "account_not_found")

    def put(self, account: dict[str, Any]) -> None:
        with self._lock:
            data = self.data()
            for index, entry in enumerate(data["accounts"]):
                if entry["id"] == account["id"]:
                    data["accounts"][index] = copy.deepcopy(account)
                    break
            else:
                data["accounts"].append(copy.deepcopy(account))
            self.save(data)

    def update(self, account_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            account = self.get(account_id)
            account.update(patch)
            self.put(account)
            return account

    def delete(self, account_id: str) -> None:
        with self._lock:
            self.get(account_id)
            data = self.data()
            data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
            self.save(data)

    def public(self) -> list[dict[str, Any]]:
        return [{key: copy.deepcopy(account.get(key)) for key in PUBLIC_FIELDS} for account in self.accounts()]
