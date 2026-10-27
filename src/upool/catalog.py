"""Read a provider's complete model catalogue without saving or activating it."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import __version__, adapters
from .models import Provider, UPoolError

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_PAGES = 100


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Catalogue reads carry credentials; never forward them to another URL.
        return None


def fetch(provider: Provider, timeout: float = 15.0) -> dict[str, Any]:
    target = adapters.get(provider.app).health_target(provider)
    if target is None:
        raise UPoolError("Enter a provider endpoint to load its models.")
    url, headers = target
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise UPoolError("Model endpoint must be an HTTP(S) URL without embedded credentials.")
    headers = {**headers, "accept": "application/json", "user-agent": f"U-Pool/{__version__}"}
    opener = urllib.request.build_opener(_NoRedirect())
    models: dict[str, dict[str, Any]] = {}
    cursors: set[str] = set()
    page_url = url
    try:
        for _ in range(MAX_PAGES):
            request = urllib.request.Request(page_url, headers=headers)
            with opener.open(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise UPoolError("Model catalogue response is too large.")
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise UPoolError("The endpoint did not return a model catalogue (expected a data array).")
            for item in payload["data"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                    raise UPoolError("The model catalogue contains an invalid model id.")
                model_id = item["id"]
                context = item.get("context_length")
                name = item.get("display_name") or item.get("name") or model_id
                models[model_id] = {
                    "id": model_id,
                    "name": name if isinstance(name, str) else model_id,
                    "context_length": context if type(context) is int and context > 0 else None,
                }
            if not payload.get("has_more"):
                return {"models": list(models.values())}
            cursor = payload.get("last_id")
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise UPoolError("The model catalogue returned an invalid pagination cursor.")
            cursors.add(cursor)
            query = dict(urllib.parse.parse_qsl(parsed.query))
            query["after_id"] = cursor
            page_url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
        raise UPoolError("The model catalogue exceeded the pagination limit; no partial list was loaded.")
    except urllib.error.HTTPError as exc:
        messages = {
            401: "API key rejected. Check the key and authentication header.",
            403: "Access denied. Check the key's model catalogue permissions.",
            404: "This provider does not expose a model catalogue at the configured endpoint. Enter a model ID manually.",
            429: "The provider is rate limiting model requests. Try again later.",
        }
        # Do not surface upstream bodies: they can echo request credentials.
        raise UPoolError(messages.get(exc.code, f"Could not load models (HTTP {exc.code}).")) from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise UPoolError("The model endpoint did not return valid JSON.") from None
    except (TimeoutError, urllib.error.URLError, OSError):
        raise UPoolError("Could not reach the model endpoint. Check the URL and connection, then retry.") from None
    except ValueError:
        raise UPoolError("Check the endpoint URL and authentication header for invalid characters.") from None
