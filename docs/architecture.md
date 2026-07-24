# Architecture

## Overview

Jitsi JWT Invite Portal is an access-control layer placed beside an
existing self-hosted Jitsi Meet installation.

It does not replace Jitsi Meet, Prosody, Jicofo, Jitsi Videobridge, or
nginx. It coordinates them by issuing short-lived JWTs and enforcing
role semantics.

## Components

### Invitation portal

`app/app.py` provides:

- organizer dashboard;
- invitation creation;
- invitation details;
- moderator entry;
- invitation revocation;
- guest landing page;
- guest JWT issuance;
- SQLite persistence;
- audit logging;
- CSRF validation;
- process-local rate limiting.

It uses the Python standard library and runs behind nginx.

### nginx

nginx provides two distinct access paths.

`/invite/`:

- requires Basic Auth;
- forwards the authenticated username in `X-Remote-User`;
- provides organizer access.

`/join/`:

- is publicly reachable;
- requires a random invitation code;
- explicitly clears `X-Remote-User`;
- provides guest access.

Both routes communicate with the portal through:

```text
/run/jitsi-invite/jitsi-invite.sock
```

### SQLite

The database stores two principal entities.

`invitations`:

- random invitation code;
- unique room identifier;
- conference title;
- organizer display name;
- owning organizer account;
- creation time;
- optional expiration time;
- optional revocation time.

`audit_events`:

- event time;
- event type;
- invitation code;
- room identifier;
- actor;
- remote address;
- event details.

### JWT generation

JWTs use HS256 and are created only after invitation validation.

Moderator tokens contain:

```json
"context.user.moderator": true
```

Guest tokens contain:

```json
"context.user.moderator": false
```

Both token types are:

- room-bound;
- time-limited;
- assigned a unique `jti`;
- generated separately for every entry.

### Prosody role enforcement

`prosody/mod_token_roles.lua` maps JWT role information to MUC
affiliations.

Moderator:

```text
JWT moderator -> owner affiliation
```

Guest:

```text
JWT guest -> member affiliation
```

The module tracks active JWT moderators in each room and denies guest
entry until at least one moderator is present.

Jicofo is explicitly allowed to perform administrative room activity.
This is necessary because Jicofo may create the MUC before a human
moderator joins.

## Organizer request sequence

```text
1. Organizer authenticates through nginx Basic Auth.
2. nginx passes X-Remote-User to the portal.
3. Portal creates or loads an invitation owned by that account.
4. Organizer requests moderator entry.
5. Portal validates ownership and invitation state.
6. Portal generates a short-lived moderator JWT.
7. Browser is redirected to the room with the JWT.
8. Prosody assigns owner affiliation.
9. Audit event is stored.
```

## Guest request sequence

```text
1. Guest opens /join/<random-code>.
2. Portal validates that the invitation exists and is active.
3. Guest submits a display name and CSRF token.
4. Portal applies rate limiting.
5. Portal generates a short-lived guest JWT.
6. Browser is redirected to the room with the JWT.
7. Prosody rejects entry when no JWT moderator is active.
8. Otherwise Prosody assigns member affiliation.
9. Audit event is stored.
```

## Invitation lifecycle

Temporary invitation:

```text
active -> expired
active -> revoked
```

Permanent invitation:

```text
permanent -> revoked
```

A permanent invitation has `expires_at = NULL`. This affects only the
invitation link. Generated JWTs always remain short-lived.

## Trust boundaries

### Public network to nginx

Controls:

- HTTPS;
- request-size limits;
- route separation;
- authentication on `/invite/`;
- short proxy timeouts.

### nginx to portal

Controls:

- Unix socket rather than TCP;
- explicit `X-Remote-User` handling;
- socket filesystem permissions.

### Portal to SQLite

Controls:

- dedicated database path;
- restricted service user;
- limited systemd writable paths.

### Portal to Jitsi

Controls:

- short-lived JWTs;
- exact room binding;
- explicit role claim;
- invitation state validation.

### Jitsi to Prosody role module

Controls:

- `token_verification`;
- custom role enforcement;
- moderator-presence tracking;
- Jicofo-specific administrative allowance.

## Failure considerations

### Portal unavailable

Existing active Jitsi sessions may continue, but new portal-issued
entries fail.

### SQLite unavailable

Invitation validation and audit recording fail. The service should not
issue tokens without successfully validating persistent state.

### nginx authentication misconfiguration

Organizer identity isolation may fail. `/invite/` must never be exposed
without authentication.

### Missing Prosody role module

JWT validation alone does not provide the project's moderator/guest
behavior. Role tests are required after every configuration or package
change.

### Signing secret disclosure

An attacker may forge tokens. Rotate the secret immediately and restart
the components that consume it.
