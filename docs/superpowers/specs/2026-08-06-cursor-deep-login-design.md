# Cursor deep-login: turning a browser cookie into a session token — design

Status: designed 2026-08-06, unimplemented. This is the feature the
[0.8.0 design](2026-08-05-upool-0.8.0-design.md) proved possible and then
deferred - its section 11 ends "Deferred rather than rushed into 0.8.0; the
refusal above is what keeps 0.8.0 honest until it lands." This is that landing.

**Read section 11 of the 0.8.0 design first.** It establishes the one fact this
whole feature turns on: a browser cookie is a `web` token, a signed-in desktop is
a `session` token, and writing the former into `state.vscdb` signs Cursor out.
0.8.0 refuses a `web` token on `Use`; this converts it instead.

## 1. The measurement that changed the design

The 0.8.0 design deferred this because a pure-HTTP replay of the confirm step
"stalls on the AuthKit login page", implying a controlled webview that injects the
cookie - real work with a live keystone risk (httpOnly cookie injection in
WebView2). **That premise was re-measured on 2026-08-06 and is wrong.** The stall
was the absence of a cookie jar, not an AuthKit wall.

Measured against the author's own `web` cookie, carrying a `http.cookiejar`
through all three calls:

```
GET  cursor.com/loginDeepControl?challenge&uuid&mode=login   (cookie authorises)
        -> 307, stays on cursor.com, jar gains `cursor-web-target-synced-user`
POST cursor.com/api/auth/loginDeepCallbackControl {uuid, challenge}
        -> 200 "OK"   (jar cookie rides along; Origin/Referer set)
GET  api2.cursor.sh/auth/poll?uuid&verifier
        -> 200 {accessToken: <session JWT>, authId: "google-oauth2|user_01KXX…"}
```

The GET is not decorative: its 307 sets `cursor-web-target-synced-user` in the
jar, and that cookie is what the confirm POST needs. A POST without the preceding
GET is untested and assumed to fail - the GET stays because it was measured with
the GET.

So the design is **pure `urllib` with a `CookieJar`**. No webview, no cookie
injection, no new dependency. The keystone risk 0.8.0 named does not exist on this
path.

The confirm body carries no `selectedTeamId` for an individual account. Cursor's
own page sends it only for team accounts, and omitting it on an individual account
returned `200`. A team account is expected to answer the confirm with a plain-text
`Select a team to continue.`, which reaches the user verbatim (section 5).

## 2. PKCE, exactly as the client does it

```
verifier  = secrets.token_urlsafe(43)
challenge = base64.urlsafe_b64encode(sha256(verifier)).rstrip("=")
uuid      = uuid4()
```

`verifier` never leaves the process except on the poll URL that redeems it; the
server only ever saw `challenge` until then. This is the standard PKCE handshake
and it is reproduced rather than invented - the constants (the `Cursor/…`
User-Agent, the `%3A%3A` separator) match what a real client sends.

## 3. New module: `upool/cursor/deeplogin.py`

One file, because this is the only reverse-engineered surface in the codebase and
a protocol that spans two hosts - it earns isolation from `switch.py`, which is
already dense. It does not import `CursorAccount`; its input is the ready-made
`Cookie:` header string, so the boundary is a string in and a small record out.

```python
@dataclass(frozen=True)
class DeepLogin:
    user_id: str        # from the poll's authId, after the "|"
    token: str          # the minted session JWT

def exchange(cookie_header: str, *, timeout: float = 20.0,
             attempts: int = 5, interval: float = 2.0) -> DeepLogin
```

- `cookie_header` is exactly what `models.cookie_header(account)` already builds -
  `WorkosCursorSessionToken=user_id%3A%3Atoken`. The module reuses the one
  function that already knows the encoding rather than re-deriving it.
- HTTP is `urllib.request` with an opener wrapping `HTTPCookieProcessor(jar)`, so
  one jar spans the GET and the POST. The poll goes to a different host and needs
  no jar.
- **Identity check.** The `user_id` parsed out of the poll's `authId` (the half
  after `|`) is compared against the `user_id` in the input cookie. A mismatch
  raises rather than returns: for an account switcher, silently minting a token
  for the *wrong* account is the worst possible outcome, worse than failing.
- Failure is a raised `UPoolError` on: confirm non-2xx (message is the server's
  own body, trimmed), poll exhausted without a token, unparseable poll JSON, any
  `OSError`, or the identity mismatch above. The module writes nothing and knows
  nothing about the store; a caller decides what a failure means.

## 4. `web_token`: keep both credentials

`CursorAccount` gains one field:

```python
web_token: str = ""     # the browser cookie, kept to re-mint when the session dies
```

`token` becomes "the credential to write into Cursor" and `web_token` becomes
"the cookie that can refresh it". This is the choice to keep both - a session JWT
lives ~60 days (the measured `exp` was 1789922267, ~60 days out), and when it
dies U-Pool re-runs the exchange from `web_token` silently rather than greying the
row and asking the user to paste again.

It is a secret, so it is handled as one, in the same three places `token` is:

- `_TEXT_FIELDS` gains `web_token`, so `from_dict` coerces it and `to_dict`
  round-trips it through `cursor.json`.
- `redacted()` masks it alongside `token`.
- `summary()` **pops** it, exactly as it pops `token` and `avatar`. `api.py`'s
  rule that no secret enters a list response covers both cookies, not one. In its
  place it adds one non-secret boolean, `has_web_token = bool(self.web_token)` -
  the card needs to know a cookie exists to decide whether an expired row can
  still be used, the same way `has_token` already stands in for `token`.

This widens what the Cursor adapter owns in `cursor.json` by one key. That file is
U-Pool's own, not shared like `settings.json`, so this is not a data-loss change
against another program - but it is called out because the ownership rule in
CLAUDE.md asks for it.

## 5. The flow: `switch.use()`, before the editor is touched

The 0.8.0 `use()` refuses a `web` token in `_refuse_unswitchable`. That refusal is
removed and replaced by a conversion, placed where the module's whole discipline
requires - **before `process.close()`**, so a network failure leaves Cursor
running and nothing written:

```
store = ...
account = store.get(account_id)
account = _ensure_session_token(account, store)   # new; replaces _refuse_unswitchable, may raise
values = _auth_values(account)
db = paths.cursor_state_db()
if not db.is_file(): raise ...                     # unchanged, already inline in use()
...                                                # unchanged from here down
```

`_ensure_session_token`, as a decision over what credential the row can offer:

1. `token` is a live session token (`token_kind == KIND_SESSION` **and**
   `status != STATUS_EXPIRED`): return unchanged, no network call. This is the
   common path - a hand-pasted session token, or one `_adopt_live_session` banked
   from the live editor.
2. A cookie is available to exchange (`web_token`, or `token` is itself a
   `KIND_WEB` cookie): run `deeplogin.exchange` on it, persist via
   `store.upgrade_token`, return the updated record. A genuinely dead cookie fails
   here at the confirm step with cursor.com's own message (section 3) - which is
   the honest answer, not a guess.
3. Neither: an expired bare session token with no `web_token` behind it. Nothing
   can revive it. Raise `UPoolError` telling the user to paste a fresh cookie -
   the one case 0.8.0's `STATUS_EXPIRED` refusal was really about.

The order matters: case 1 is checked before case 2 so a live session token is
never needlessly re-minted, and case 3 is the explicit else so a dead token can
never fall through to being written into `state.vscdb`.

`_refuse_unswitchable` is removed: all three of its branches - no token,
`STATUS_EXPIRED`, `KIND_WEB` - move into `_ensure_session_token`'s three cases,
where they become a conversion (case 2) and two precise refusals (case 3) rather
than a blanket no. The one check that was never in it - a machine with no Cursor
database - is already inline in `use()` after `_auth_values`, and stays exactly
there.

## 6. Store: record the upgrade

`upsert` (the paste path) sets `web_token` when the pasted token is a `web` one,
so the cookie is banked the moment it arrives:

```python
if token_kind(parsed.token) == KIND_WEB:
    account.web_token = parsed.token
```

New method, the one place an upgrade is written:

```python
def upgrade_token(self, account_id: str, session_token: str) -> CursorAccount:
    """Record a session token minted from this row's web cookie.

    web_token is left untouched - it is the only thing that can re-mint when the
    session dies. status becomes ok: a row that just proved it can sign in is not
    expired whatever a prior refresh concluded.
    """
```

It sets `token = session_token`, leaves `web_token`, sets `status = STATUS_OK`,
and does not touch usage figures - they were true when measured.

## 7. UI: the block is gone

`CursorCard.tsx`'s `blocked` (line 163-171) is rewritten to match what `use()`
can now actually do, using the new `has_web_token`:

- Cursor not found on the machine → block (unchanged).
- No token stored (`!has_token`) → block, "no session cookie" (unchanged).
- Expired **and** no cookie to re-mint from (`expired && token_kind === "session"
  && !has_web_token`) → block, "paste a fresh cookie". This is case 3 of
  `_ensure_session_token`, mirrored so the user sees it before pressing `Use`.
- Otherwise → allow. The `token_kind === "web"` branch is **gone**: a browser
  cookie is now the thing `Use` converts, not a thing it refuses. An expired row
  with a `web_token` behind it also falls here - `Use` re-mints it.

`token_kind` and the new `has_web_token` both stay in the summary and the type -
the card may still want to show that a row is a browser cookie until its first
`Use`, and that is a separate, later decision from unblocking `Use`.

## 8. Errors, surfaced honestly

Every failure in section 3 becomes a toast carrying the reason. The confirm step's
plain-text body reaches the user unaltered, so a team account's
`Select a team to continue.` reads as exactly what it is - U-Pool not sending a
team id - rather than a generic failure. A network error mid-exchange says so, and
because the exchange is before `process.close()`, the editor is still up and the
pool is unchanged: the user retries, nothing to undo.

## 9. Testing

All offline. `urllib.request.urlopen` / the opener is monkeypatched; the `sandbox`
fixture already redirects every write target, so a test that reaches the store
writes into the temp home.

- **deeplogin** — happy path (GET sets jar, POST 200, poll returns a session
  token, `user_id` parsed from `authId`); confirm 4xx carries the server's text
  into the `UPoolError`; poll exhausts without a token; poll returns unparseable
  JSON; **identity mismatch** (poll `authId` disagrees with the cookie's user id)
  raises; the PKCE invariant `challenge == b64url(sha256(verifier)).rstrip("=")`
  holds for a generated pair.
- **switch** — `_ensure_session_token` leaves a session-token row untouched with
  no network call; converts a `web` row and persists; **a failing exchange raises
  before `process.close` is called** (the module's load-bearing order, pinned by
  asserting the process was never closed); an expired row with a `web_token` is
  re-minted, one without is refused.
- **models / store** — `web_token` round-trips through `from_dict`/`to_dict`;
  `summary()` omits `web_token` but sets `has_web_token` to match; `redacted()`
  masks it; `upsert` banks a pasted `web` token into `web_token`; `upgrade_token`
  sets the session token and leaves `web_token` intact.

## 10. What this design will not claim

The protocol is measured end to end and the tests pin it, but the tests mock the
network - they prove U-Pool sends what cursor.com accepted on 2026-08-06 and
handles each answer, not that cursor.com will keep accepting it. It is an
undocumented flow and can change. Verifying that a real `web` cookie becomes a
working desktop sign-in is a manual step the author runs; this document does not
assert it works, only that it is built to the shape that did.
