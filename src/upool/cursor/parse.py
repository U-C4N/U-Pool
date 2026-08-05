"""Turning whatever the user pasted into Cursor accounts.

One box, one entry point. A session cookie reaches the clipboard in at least
five shapes - a browser's ``cookies.txt`` export, a devtools cookie assignment,
a whole ``Cookie:`` request header, the reference switcher's ``email,token``
CSV, and a JSON dump from somebody's script - and asking the user which of those
they are holding is asking them to know. Bulk and single are the same path for
the same reason: one line or two hundred, typed or read out of a ``.txt`` file,
all arrive here.

A line that matches nothing is **skipped silently** and only counted. A
``cookies.txt`` exported from a browser is a comment header followed by every
other domain the user has visited, so naming each rejected line would bury the
one number that matters - how many accounts came in - under two hundred that
never claimed to be one.

Two rules hold across every form:

*The stored token is the decoded half.* The cookie carries ``::`` percent-encoded
and the other four forms quote it however the tool that wrote them felt like, so
decoding on the way in leaves one spelling to split on and one value to store.
:func:`upool.cursor.models.cookie_header` puts the encoding back on the way out.

*No user id, no account.* ``user_id`` is identity for the pool - it is what makes
a re-pasted rotated cookie refresh the row that is already there instead of
adding a second one - so a credential that arrives without one is dropped rather
than stored under an invented key. That is the whole reason a CSV row whose
token half is a bare JWT is skipped even though the email beside it is perfectly
readable: an email is not identity, and a row nothing can be matched against
could never be deduplicated or refreshed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import unquote

from .models import COOKIE_NAMES, COOKIE_SEPARATOR

_SEPARATOR = unquote(COOKIE_SEPARATOR)

# Compared lower-cased: the three names differ in case in the wild - a hand-typed
# ``workoscursorsessiontoken`` is the same cookie - and the user did not choose
# the spelling, the site that issued it did.
_COOKIE_NAMES = {name.lower() for name in COOKIE_NAMES}

# Where a script that dumped a session put the credential, in the order to trust
# them. ``cookie`` and ``WorkosCursorSessionToken`` usually hold the whole
# ``name=value`` pair rather than the value alone, which is why every form goes
# through :func:`_credential` instead of splitting on its own.
_TOKEN_KEYS = ("token", "accessToken", "cookie", "WorkosCursorSessionToken")
_EMAIL_KEY = "email"

# domain, include-subdomains, path, secure, expiry, name, value.
_NETSCAPE_FIELDS = 7
_NETSCAPE_NAME = 5
_NETSCAPE_VALUE = 6

_HEADER = re.compile(r"^\s*(?:set-)?cookie\s*:\s*", re.IGNORECASE)
# The whole line and nothing else, because here the line is the only evidence
# that it was meant to be a credential at all.
_BARE = re.compile(
    rf"^[A-Za-z0-9_-]+(?:{re.escape(_SEPARATOR)}|{re.escape(COOKIE_SEPARATOR)})\S+$",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"^[^\s@,]+@[^\s@,]+\.[^\s@,]+$")


@dataclass(frozen=True)
class ParsedAccount:
    """One credential read out of the paste. ``email`` is a hint, not a fact.

    It is whatever the export happened to carry beside the token, and nothing has
    checked it against the account. ``/api/auth/me`` overwrites it as soon as the
    first refresh answers; until then it is better than an empty card.
    """

    user_id: str
    token: str
    email: str = ""


@dataclass
class ParseResult:
    accounts: list[ParsedAccount] = field(default_factory=list)
    skipped: int = 0


def parse(text: str) -> ParseResult:
    """Read every account in ``text``, counting what could not be read.

    The whole input is tried as JSON before the lines are, because a pretty
    printed dump is one record spread over twenty lines and the line loop would
    report it as twenty failures.

    ``skipped`` counts non-blank lines that yielded nothing. Blank lines are not
    failures - a paste with paragraph breaks between accounts would otherwise
    claim a handful of phantom rejections.
    """
    accounts: dict[str, ParsedAccount] = {}
    skipped = 0

    def keep(found: ParsedAccount) -> None:
        """Last paste of a user id wins, in the position the first one took.

        The row is a rotated cookie for an account already in the pool far more
        often than it is a different account, so the newer token replaces the
        older one - but an email learned from an earlier line survives a later
        one that has none, since a hint that was already true cannot become
        wrong by being repeated without it.
        """
        previous = accounts.get(found.user_id)
        if previous is not None and not found.email:
            found = replace(found, email=previous.email)
        accounts[found.user_id] = found

    document = _load_json(text)
    if document is not None:
        harvested, missed = _harvest(document)
        for account in harvested:
            keep(account)
        # A document that parsed and held nothing is still one thing that could
        # not be read. Zero on every count would report it as nothing happening.
        return ParseResult(list(accounts.values()), missed or (0 if harvested else 1))

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        account = (
            _from_netscape(stripped)
            or _from_cookie_pairs(stripped)
            or _from_bare(stripped)
            or _from_csv(stripped)
        )
        if account is not None:
            keep(account)
            continue
        embedded = _load_json(stripped)
        harvested, missed = _harvest(embedded) if embedded is not None else ([], 0)
        if harvested:
            for found in harvested:
                keep(found)
            skipped += missed
        else:
            skipped += 1

    return ParseResult(list(accounts.values()), skipped)


def _from_netscape(line: str) -> ParsedAccount | None:
    """A ``cookies.txt`` row - seven tab-separated fields, credential in the last.

    Nothing looks at the domain field. Real exports write ``#HttpOnly_`` in front
    of it, which would make the row indistinguishable from the comment header if
    the ``#`` were read before the tabs were counted - so this form is tried
    first and a comment only becomes a comment by failing it.
    """
    fields = line.split("\t")
    if len(fields) != _NETSCAPE_FIELDS:
        return None
    if fields[_NETSCAPE_NAME].strip().lower() not in _COOKIE_NAMES:
        return None
    return _credential(fields[_NETSCAPE_VALUE])


def _from_cookie_pairs(line: str) -> ParsedAccount | None:
    """``name=value``, alone or in a whole ``Cookie:`` header among other cookies."""
    for chunk in _HEADER.sub("", line).split(";"):
        name, sign, value = chunk.strip().partition("=")
        if not sign or name.strip().lower() not in _COOKIE_NAMES:
            continue
        account = _credential(value)
        if account is not None:
            return account
    return None


def _from_bare(line: str) -> ParsedAccount | None:
    """``user_01AB::eyJ...`` on its own, percent-encoded or not."""
    return _credential(line) if _BARE.match(line) else None


def _from_csv(line: str) -> ParsedAccount | None:
    """``email,token`` - the shape the reference switcher exports.

    The email is required to look like one. Without that this form matches any
    line with a comma in it, and a two-column CSV of something else entirely
    would be read as a pool.
    """
    parts = line.split(",")
    if len(parts) != 2 or not _EMAIL.match(parts[0].strip()):
        return None
    return _credential(parts[1], email=parts[0].strip())


def _credential(value: str, email: str = "") -> ParsedAccount | None:
    """The one place a cookie value becomes a record, or does not.

    Split on the *first* separator only. Everything after it is the token
    verbatim, whatever it contains - the halves are user id and credential, not
    a list, and a second ``::`` further along is part of the credential rather
    than a third field.
    """
    decoded = unquote(_strip_cookie_name(value.strip()))
    # A cookie value cannot contain whitespace, so anything that does is prose
    # that happened to have a ``::`` in it.
    if not decoded or any(character.isspace() for character in decoded):
        return None
    user_id, separator, token = decoded.partition(_SEPARATOR)
    if not separator or not user_id or not token:
        return None
    return ParsedAccount(user_id=user_id, token=token, email=email)


def _strip_cookie_name(value: str) -> str:
    """Drop a leading ``<cookie name>=``.

    A CSV cell and a JSON ``cookie`` key both turn up holding the whole pair
    rather than the value, and the pair is what a browser hands over when it is
    asked for "the cookie".
    """
    name, sign, rest = value.partition("=")
    if sign and name.strip().lower() in _COOKIE_NAMES:
        return rest.strip()
    return value


def _load_json(text: str) -> Any | None:
    """``text`` as an object or array, or ``None`` if it is not one.

    Only those two shapes count. ``json.loads`` reads a line of digits as an
    integer and a quoted word as a string, and treating either as a document to
    search would mean a paste of junk taking a different path from the rest of
    the junk around it.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def _harvest(node: Any, email: str = "") -> tuple[list[ParsedAccount], int]:
    """Every credential anywhere in a JSON document, and how many were unusable.

    The whole tree is walked rather than a known layout read, because the shape
    is whatever the script that wrote it chose - ``{"accounts": [...]}``,
    ``{"data": {"session": {...}}}``, a bare array. An ``email`` seen on the way
    down carries to the objects below it, which is how a wrapper naming the
    account and an inner object holding its token end up on the same record.

    Only an object that carried a token key and could not be read counts as
    skipped. The containers on the way to it did not claim to be accounts.
    """
    if isinstance(node, dict):
        email = _first_string(node, (_EMAIL_KEY,)) or email
        found: list[ParsedAccount] = []
        missed = 0
        raw = _first_string(node, _TOKEN_KEYS)
        if raw:
            account = _credential(raw, email=email)
            if account is None:
                missed = 1
            else:
                found.append(account)
        children: Any = node.values()
    elif isinstance(node, list):
        found, missed, children = [], 0, node
    else:
        return [], 0

    for child in children:
        child_found, child_missed = _harvest(child, email)
        found.extend(child_found)
        missed += child_missed
    return found, missed


def _first_string(data: dict, keys: tuple[str, ...]) -> str:
    """The first of ``keys`` present as a non-empty string, matched case-blind.

    ``accessToken`` and ``AccessToken`` are the same key to everyone except a
    dict lookup, and the user did not choose the casing - the exporter did.
    """
    lowered = {str(key).lower(): value for key, value in data.items() if isinstance(value, str)}
    for key in keys:
        value = lowered.get(key.lower(), "").strip()
        if value:
            return value
    return ""
