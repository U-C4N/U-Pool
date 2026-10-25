"""Read browser exports without losing HttpOnly or cookie scope."""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any

from .models import ProxyError

_SESSION = ("__Secure-next-auth.session-token", "__Secure-authjs.session-token")
_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def _session(name: str) -> bool:
    return any(name == prefix or re.fullmatch(re.escape(prefix) + r"\.\d+", name) for prefix in _SESSION)


def _cookie(raw: dict[str, Any]) -> dict[str, Any] | None:
    domain = str(raw.get("domain") or "chatgpt.com").lower()
    host = domain.lstrip(".")
    if host != "chatgpt.com" and not host.endswith(".chatgpt.com"):
        return None
    name, value = raw.get("name"), raw.get("value")
    if not isinstance(name, str) or not isinstance(value, str) or not _NAME.fullmatch(name) or any(c in value for c in "\r\n\x00"):
        raise ProxyError("Invalid cookie name or value.", 400, "invalid_cookie")
    if any(name in raw and type(raw[name]) is not bool for name in ("secure", "httpOnly", "session")):
        raise ProxyError("Cookie flags must be booleans.", 400, "invalid_cookie")
    path = str(raw.get("path") or "/")
    if not path.startswith("/"):
        raise ProxyError("Invalid cookie path.", 400, "invalid_cookie")
    result: dict[str, Any] = {
        "name": name, "value": value, "domain": domain, "path": path,
        "secure": bool(raw.get("secure", True)), "httpOnly": bool(raw.get("httpOnly", False)),
    }
    expires = raw.get("expires", raw.get("expirationDate"))
    if expires is not None and not raw.get("session", False):
        try:
            expiry = float(expires)
        except (ValueError, TypeError):
            raise ProxyError("Invalid cookie expiry.", 400, "invalid_cookie") from None
        if not math.isfinite(expiry):
            raise ProxyError("Invalid cookie expiry.", 400, "invalid_cookie")
        if expiry > 0:
            result["expires"] = expiry
    same_site = str(raw.get("sameSite") or "").lower()
    if same_site in ("strict", "lax", "none", "no_restriction"):
        result["sameSite"] = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}[same_site]
    return result


def parse_cookies(text: str) -> list[dict[str, Any]]:
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 2_000_000:
        raise ProxyError("Paste a cookie export of at most 2 MB.", 400, "invalid_cookie")
    text = text.lstrip("\ufeff").strip()
    raw: list[dict[str, Any]] = []
    if text.startswith(("[", "{")):
        try:
            parsed = json.loads(text)
        except ValueError:
            raise ProxyError("Cookie export is not valid JSON.", 400, "invalid_cookie") from None
        if isinstance(parsed, dict):
            parsed = parsed.get("cookies", [parsed] if "name" in parsed else None)
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            raise ProxyError("Expected a cookie array or an object containing cookies.", 400, "invalid_cookie")
        raw = parsed
    elif "\t" in text:
        for line in text.splitlines():
            http_only = line.startswith("#HttpOnly_")
            if http_only:
                line = line[len("#HttpOnly_"):]
            elif not line.strip() or line.startswith("#"):
                continue
            fields = line.split("\t", 6)
            if len(fields) != 7:
                raise ProxyError("Invalid Netscape cookie row.", 400, "invalid_cookie")
            domain, include_subdomains, path, secure, expiry, name, value = fields
            if include_subdomains.upper() not in ("TRUE", "FALSE") or secure.upper() not in ("TRUE", "FALSE"):
                raise ProxyError("Invalid Netscape cookie flags.", 400, "invalid_cookie")
            domain = "." + domain.lstrip(".") if include_subdomains.upper() == "TRUE" else domain.lstrip(".")
            raw.append({"domain": domain, "path": path, "secure": secure.upper() == "TRUE",
                        "expires": expiry, "name": name, "value": value, "httpOnly": http_only})
    else:
        text = re.sub(r"^cookie\s*:\s*", "", text, flags=re.I)
        for part in text.split(";"):
            if not part.strip():
                continue
            name, separator, value = part.strip().partition("=")
            if not separator:
                raise ProxyError("Expected name=value cookie pairs.", 400, "invalid_cookie")
            raw.append({"name": name, "value": value, "httpOnly": _session(name)})
    cookies: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in raw:
        cookie = _cookie(item)
        if cookie:
            cookies[(cookie["domain"], cookie["path"], cookie["name"])] = cookie
    sessions = [c for c in cookies.values() if _session(c["name"]) and c["value"]]
    if not sessions:
        raise ProxyError("The export does not contain a ChatGPT session cookie.", 400, "invalid_cookie")
    now = time.time()
    live = [c for c in cookies.values() if c.get("expires", now + 1) > now]
    if not any(_session(c["name"]) and c["value"] for c in live):
        raise ProxyError("The ChatGPT session cookie has expired. Export a fresh session.", 401, "auth_expired")
    return live
