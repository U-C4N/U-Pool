"""Making one pooled account the one Cursor signs in as.

The order is the whole module. Cursor holds ``state.vscdb`` open while it runs,
so a switch has to ask the editor to leave before it writes - and because that
request is polite, :mod:`upool.cursor.process` having no force kill and no room
in its signature to grow one, it can fail. When it does, **nothing is written**:
not a key, not even the backup. The failure mode of a half-applied auth record is
an editor that will not sign in and cannot be signed out, and the account being
switched to is worth less than the file the user had unsaved.

Everything that can refuse refuses before the editor is touched at all - an
account that is not in the pool, one whose cookie cursor.com has already
rejected, one with no cookie stored, a machine with no Cursor database, and the
unmeasured key list :func:`_auth_values` stands in for. Closing somebody's editor
and only then discovering the switch was never going to work is the failure that
ordering exists to prevent.

The backup lives here rather than in :mod:`upool.cursor.vscdb` so there is
exactly one copy per switch, taken once, immediately before the only write -
``state.vscdb.backup`` beside the original, which is a rename away from undone in
a folder the user can already open.

The confirmation - "Cursor will be closed. Continue?" - belongs to the UI.
:func:`use` assumes it was answered.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field

from .. import backup, paths
from ..adapters.base import ApplyResult
from ..models import UPoolError
from . import process, vscdb
from .models import STATUS_EXPIRED, CursorAccount
from .store import CursorStore

# What the sign-in snapshot held in ``cursorAuth/cachedSignUpType``, and the prefix
# its JWT subject carried. Constants rather than literals inline because they are
# the two things in this module that came from one machine's measurement and would
# be the first suspects if a future Cursor signed in differently.
SIGN_UP_TYPE = "Auth_0"
AUTH_PREFIX = "auth0|"


@dataclass
class SwitchOutcome:
    """An :class:`ApplyResult` plus the two facts that are about the editor.

    ``closed_cursor`` and ``relaunched`` sit out here rather than inside
    ``ApplyResult`` because five adapters share that dataclass and not one of
    them closes an application. A pair of fields that are always ``False`` for
    Claude, Codex, Hermes, OpenCode and Claude Desktop is a pair the UI has to
    explain away everywhere else it renders a switch. So the bridge spreads these
    two alongside ``files``, ``backups`` and ``warnings``, and the shared shape
    stays the shape every other switch already reports in.
    """

    result: ApplyResult = field(default_factory=ApplyResult)
    closed_cursor: bool = False
    relaunched: bool = False


def use(account_id: str, pool: CursorStore | None = None) -> SwitchOutcome:
    """Sign Cursor in as ``account_id``, closing and restarting it if it is up.

    ``pool`` is a parameter because :class:`upool.api.Api` holds one
    :class:`CursorStore` and its in-memory document is what the next
    ``cursor_state()`` reads. A store constructed in here would record the new
    ``current`` on disk and leave that live instance still reporting the old
    account, so the green dot would not move until the app restarted.
    """
    store = pool if pool is not None else CursorStore()
    account = store.get(account_id)
    _refuse_unswitchable(account)

    values = _auth_values(account)
    db = paths.cursor_state_db()
    if not db.is_file():
        raise UPoolError(f"Cursor has no database at {db} yet - open Cursor once, then try again.")

    closed = False
    if process.running():
        if not process.close(process.CLOSE_TIMEOUT):
            # Deliberately before the backup: "write nothing" means the switch
            # leaves no trace at all, not that it stopped halfway politely.
            raise UPoolError(process.CLOSE_FAILED_NOTE)
        closed = True

    result = ApplyResult()
    sidecar = backup.sidecar(db)
    if sidecar:
        result.backups.append(str(sidecar))
    # The same path that was backed up a moment ago, rather than resolved a second
    # time and hoped to agree with the first.
    vscdb.write_auth(values, db)
    result.files.append(str(db))

    # Only now: this field describes what is in state.vscdb, so it cannot be set
    # before the write that put it there.
    store.set_current(account.id)

    relaunched = False
    if closed:
        relaunched = process.launch()
        if not relaunched:
            # The account is already written, so this is a warning on a switch
            # that worked - never a failure, and nothing to roll back.
            result.warnings.append(process.LAUNCH_FAILED_NOTE)

    return SwitchOutcome(result=result, closed_cursor=closed, relaunched=relaunched)


def live_account() -> CursorAccount | None:
    """The account Cursor is signed in as right now, assembled from its own keys.

    The exact inverse of :func:`_auth_values`, and here rather than in
    :mod:`upool.api` so the two directions of one measurement stay in one file: a
    key that moves has to move twice, and both edits are on the same screen.

    It reads the named keys rather than running the stored values through the
    paste parser, because the measurement settled what that parser cannot know -
    **Cursor does not store the credential in cookie form.** The token sits bare
    in ``accessToken`` with no ``user_id::`` in front of it, and the identity is a
    separate row. A parser looking for a cookie finds nothing here, every time.

    ``None`` when Cursor is signed out, which is the whole of what "no account to
    adopt" means - a signed-out Cursor has these rows empty rather than absent.
    """
    stored = vscdb.read_auth()
    token = stored.get("cursorAuth/accessToken", "").strip()
    if not token:
        return None

    auth_id = stored.get("glass.lastSignedInAuthId", "")
    subject = _jwt_payload(token).get("sub")
    identity = subject if isinstance(subject, str) and subject else auth_id
    user_id = identity.split("|", 1)[-1]
    if not user_id:
        return None

    profile = _parse_profile(stored.get("cursorAuth/cachedScopedProfile", ""))
    return CursorAccount(
        user_id=user_id,
        token=token,
        email=stored.get("cursorAuth/cachedEmail", ""),
        name=str(profile.get("displayName") or ""),
        avatar=str(profile.get("pictureUrl") or ""),
        plan=stored.get("cursorAuth/stripeMembershipType", ""),
        plan_status=stored.get("cursorAuth/stripeSubscriptionStatus", ""),
    )


def _parse_profile(raw: str) -> dict[str, object]:
    """``cachedScopedProfile`` as a mapping, or empty for anything else.

    The one owned key that is JSON rather than a bare string, and the one that a
    future Cursor is most likely to reshape - so a blob that no longer parses
    costs the display name and nothing else.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _refuse_unswitchable(account: CursorAccount) -> None:
    """Both ways an account in the pool is still not one Cursor can be signed in as.

    An expired row is kept on purpose - it carries the email and the name, so
    pasting a fresh cookie revives it in place - which is exactly why ``Use`` has
    to say no to it rather than write a cookie cursor.com has already rejected.

    A row with no token can only come from a hand-edited ``cursor.json``:
    :meth:`CursorStore.upsert` will not create one. It is refused here because
    writing a blank credential would not fail, it would sign the editor out.
    """
    if account.status == STATUS_EXPIRED:
        raise UPoolError(
            f"{_label(account)}'s session has expired - paste a fresh cookie for it first."
        )
    if not account.token:
        raise UPoolError(f"{_label(account)} has no session cookie stored - paste it again.")


def _label(account: CursorAccount) -> str:
    """Whatever the card shows for this account, so a refusal names the row on screen.

    ``user_id`` is the last resort rather than the first: it is the only field
    guaranteed to be there, and it is the only one the user has never seen.
    """
    return account.name or account.email or account.user_id or "That account"


def _auth_values(account: CursorAccount) -> dict[str, str]:
    """What Cursor's owned keys should hold for ``account``.

    ``vscdb.AUTH_KEYS`` is which keys a sign-in writes; this is what goes in them.
    Both halves come out of the same measurement - the 0.8.0 design, section 6 -
    and the shapes below are what that snapshot actually contained, not what the
    field names suggest. In particular the values are stored **raw**: Cursor
    writes ``umutday11@gmail.com``, not ``"umutday11@gmail.com"``, so nothing here
    is JSON-encoded except ``cachedScopedProfile``, which really is a JSON object.

    ``accessToken`` and ``refreshToken`` are byte-identical in the snapshot - one
    413-character session JWT in both - so both are written from the one token the
    cookie carried. A cookie is all a pooled account has; there is no second
    credential to put in the second key.

    The identity fields are written even when the refresh has not filled them in,
    and written **empty** rather than skipped. Skipping leaves the previous
    account's name and email sitting under the new account's token, which is worse
    than a blank card: it is the wrong person's card. Cursor re-fetches them from
    the token on its next start.

    Nothing is filtered against ``AUTH_KEYS`` on the way out. A key with a value
    but no ownership must reach ``vscdb.write_auth`` and be refused loudly there;
    dropping it quietly is how a switch half-applies.
    """
    return {
        "cursorAuth/accessToken": account.token,
        "cursorAuth/refreshToken": account.token,
        "cursorAuth/cachedEmail": account.email,
        # Constant in the snapshot, and constant across upstream identity
        # providers: Cursor fronts Google and GitHub with Auth0 too, which is why
        # the JWT subject is ``auth0|…`` for an account that never saw an Auth0
        # login form.
        "cursorAuth/cachedSignUpType": SIGN_UP_TYPE,
        "cursorAuth/cachedScopedProfile": _scoped_profile(account),
        "cursorAuth/stripeMembershipType": account.plan,
        "cursorAuth/stripeSubscriptionStatus": account.plan_status,
        "glass.lastSignedInAuthId": _auth_id(account),
    }


def _scoped_profile(account: CursorAccount) -> str:
    """The JSON blob the account menu draws its name and avatar from.

    Written in the same key order and with the same separators the measured blob
    used, so switching to the account already signed in round-trips byte for byte
    rather than leaving a diff that is only formatting.

    ``pictureUrl`` is omitted rather than written empty when it is not known. An
    absent key leaves Cursor to fall back to initials; an empty string is a URL it
    would try to load and fail.
    """
    if not account.name:
        return ""
    profile = {"displayName": account.name}
    if account.avatar:
        profile["pictureUrl"] = account.avatar
    return json.dumps(profile, separators=(",", ":"))


def _auth_id(account: CursorAccount) -> str:
    """The ``auth0|user_…`` identity, read out of the token rather than rebuilt.

    The session JWT's ``sub`` claim is exactly what the snapshot found in
    ``glass.lastSignedInAuthId``, so taking it from there is a copy rather than a
    guess. The fallback assembles it from the cookie's own user id, which is the
    same string without the provider prefix - correct for every account measured,
    but an assumption, so it is second.
    """
    payload = _jwt_payload(account.token)
    subject = payload.get("sub")
    if isinstance(subject, str) and subject:
        return subject
    return f"{AUTH_PREFIX}{account.user_id}" if account.user_id else ""


def _jwt_payload(token: str) -> dict[str, object]:
    """The middle segment of a JWT, or ``{}`` for anything that is not one.

    No signature check: this is not a trust decision. The token came from the
    user's own paste, it is on its way into their own editor, and the one field
    read from it is an identifier that will be checked by cursor.com in a moment
    anyway.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    segment = parts[1]
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        payload = json.loads(raw)
    except (ValueError, binascii.Error):
        return {}
    return payload if isinstance(payload, dict) else {}
