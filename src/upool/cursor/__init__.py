"""The Cursor account pool - a subsystem of its own rather than a sixth adapter.

A Cursor account is not a :class:`~upool.models.Provider`. It has no base URL, no
API key, no model and no auth style; it has a session cookie, a plan and a usage
figure that comes from the network instead of from the record. Putting it in
``SUPPORTED_APPS`` would mean ``validate()`` demanding a request URL for
something that has none, and half the provider form rendering fields with no
meaning. So it lives here, and the five existing apps are untouched.

Nothing is re-exported. The modules underneath reach for ``sqlite3``, ``urllib``
and ``subprocess``, and importing :mod:`upool.cursor.models` - which is a
dataclass and nothing else - should not drag a database writer, an HTTP client
and a process killer in behind it.
"""

from __future__ import annotations
