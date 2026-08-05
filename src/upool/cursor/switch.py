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

from dataclasses import dataclass, field

from .. import backup, paths
from ..adapters.base import ApplyResult
from ..models import UPoolError
from . import process, vscdb
from .models import STATUS_EXPIRED, CursorAccount
from .store import CursorStore

PENDING_KEYS_NOTE = (
    "U-Pool does not yet know which keys Cursor writes when it signs in, so "
    "nothing was closed and nothing was written. That list comes from the "
    "sign-in diff in the 0.8.0 design, section 6."
)


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
    if not values:
        raise UPoolError(PENDING_KEYS_NOTE)

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
    """What Cursor's owned keys should hold for ``account`` - the other half of a
    measurement that has not been taken yet.

    ``vscdb.AUTH_KEYS`` is which keys a sign-in writes; this is what goes in them.
    Both come out of the same diff described in the 0.8.0 design, section 6 -
    snapshot ``state.vscdb`` signed out, sign in to Cursor, snapshot again - and
    neither may be filled in from the ecosystem's guesses at it. Until that
    happens this returns nothing, :func:`use` refuses rather than reporting a
    switch that wrote nothing, and the mapping when it lands is a dict literal
    here and a tuple there.

    Nothing is filtered against ``AUTH_KEYS`` on the way out. A key with a value
    but no ownership must reach ``vscdb.write_auth`` and be refused loudly there;
    dropping it quietly is how a switch half-applies.
    """
    return {}
