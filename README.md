# Jitsi JWT Invite Portal

A security-oriented invitation and access-control layer for self-hosted
Jitsi Meet installations operating in strict JWT mode.

The project provides authenticated organizer accounts, revocable guest
invitation links, short-lived room-bound JWTs, explicit moderator and
guest roles, SQLite audit logging, and a hardened systemd service that
communicates with nginx only through a Unix socket.

> **Project status:** working reference implementation derived from a
> successfully tested deployment. Automated installation and clean-VM
> validation are still in progress.

## Problem

A standard Jitsi room URL does not carry authorization information.
When strict JWT authentication is required, sharing a bare room URL is
therefore insufficient and may bypass the intended invitation workflow.

This project adds a dedicated access layer where:

- every browser participant requires a valid JWT;
- organizers never receive direct access to the JWT signing secret;
- moderator and guest roles are encoded in each token;
- guests cannot join before a JWT-authenticated moderator;
- reusable invitation URLs can be revoked;
- permanent invitations still issue short-lived JWTs;
- every token issuance event is recorded;
- the application exposes no additional TCP listener.

## Main features

- Strict JWT-only Jitsi access
- HS256 room-bound JWT generation
- Explicit moderator and guest roles
- Guest-before-moderator rejection
- Protected organizer portal
- Individual nginx Basic Auth accounts
- Temporary and permanent invitations
- Invitation revocation
- SQLite persistence and audit logging
- CSRF protection for forms
- Application-level guest rate limiting
- Unix-socket-only backend
- Hardened systemd service
- Jitsi built-in invitation controls disabled
- Organizer account management utility
- Standalone JWT generation utility

## Architecture

```text
Organizer browser
    |
    | HTTPS + nginx Basic Auth
    v
/invite/
    |
    | X-Remote-User
    v
nginx
    |
    | Unix socket
    v
Jitsi Invite Portal
    |
    +--> SQLite invitation and audit database
    |
    +--> short-lived moderator JWT
    |
    v
Jitsi Meet


Guest browser
    |
    | public random invitation URL
    v
/join/<invitation-code>
    |
    v
nginx
    |
    | Unix socket
    v
Jitsi Invite Portal
    |
    +--> invitation validation
    +--> guest display-name form
    +--> short-lived guest JWT
    |
    v
Jitsi Meet
```

The application socket is:

```text
/run/jitsi-invite/jitsi-invite.sock
```

The default SQLite database is:

```text
/var/lib/jitsi-invite/invite.db
```

## Security model

### Strict JWT mode

The expected Jitsi configuration uses:

```lua
authentication = "token"
```

The deployment intentionally does not use:

- `allow_empty_token`;
- an anonymous guest VirtualHost;
- unauthenticated bare room URLs.

### JWT claims

Generated tokens include:

- `iss`
- `aud`
- `sub`
- `room`
- `iat`
- `nbf`
- `exp`
- `jti`
- `context.user.id`
- `context.user.name`
- `context.user.moderator`

Moderator tokens use:

```json
"context.user.moderator": true
```

Guest tokens use:

```json
"context.user.moderator": false
```

Every JWT is restricted to one lowercase room identifier and has a
limited lifetime.

### Role enforcement

The custom Prosody module:

```text
prosody/mod_token_roles.lua
```

implements:

- JWT moderator detection;
- owner affiliation for moderators;
- member affiliation for guests;
- rejection of guests before a moderator is active;
- active-moderator tracking per room;
- moderator-state removal when the moderator leaves;
- explicit allowance for Jicofo administration activity.

Guest-first blocking is enforced primarily in the
`muc-occupant-pre-join` hook because Jicofo may create the MUC before a
human moderator joins.

### Portal isolation

The reference systemd service uses:

- a dedicated `jitsi-invite` user;
- `RestrictAddressFamilies=AF_UNIX`;
- `ProtectSystem=strict`;
- `ProtectHome=true`;
- `PrivateTmp=true`;
- `PrivateDevices=true`;
- `NoNewPrivileges=true`;
- no Linux capabilities;
- explicitly limited writable paths.

## Repository layout

```text
app/
    app.py                         Invitation portal

config/
    jwt.env.example                JWT configuration example
    portal.env.example             Portal configuration example

examples/
    jicofo.conf                    Disable automatic Jicofo ownership
    jitsi-config.js                Disable bare-link invitation UI
    prosody-virtualhost.cfg.lua    Strict JWT and role configuration

nginx/
    jitsi-invite.conf              Organizer and guest routes

prosody/
    mod_token_roles.lua            Moderator/guest enforcement module

scripts/
    jitsi-invite-user              Organizer account management
    jitsi-jwt                      Standalone JWT generator

systemd/
    jitsi-invite.service           Hardened service unit

docs/
    administrator-guide.md         Administrator account management
    moderator-guide.md             Illustrated organizer workflow
    images/                        Documentation images
    installation.md                Reproducible manual installation
    architecture.md                Detailed design notes
```

## User guides

- [Administrator Guide](docs/administrator-guide.md) — create, update,
  list, and remove organizer portal accounts.
- [Moderator Guide](docs/moderator-guide.md) — illustrated workflow for
  creating conferences, sharing guest links, entering as moderator, and
  revoking invitations.

## Configuration

For the complete deployment sequence, validation steps, rollback
procedure, and mandatory role test, see
[docs/installation.md](docs/installation.md).

Create production files from the supplied examples:

```bash
install -d -m 0750 -o root -g jitsi-invite /etc/jitsi-invite

install -m 0640 -o root -g jitsi-invite \
    config/jwt.env.example \
    /etc/jitsi-invite/jwt.env

install -m 0640 -o root -g jitsi-invite \
    config/portal.env.example \
    /etc/jitsi-invite/portal.env
```

Replace every placeholder before starting the service.

Generate a CSRF secret with:

```bash
openssl rand -hex 32
```

Generate a long JWT signing secret using a cryptographically secure
random generator.

## Default token lifetimes

```text
Guest JWT:      14,400 seconds (4 hours)
Moderator JWT:  28,800 seconds (8 hours)
```

When an invitation expires sooner, the generated JWT lifetime is
reduced to the remaining invitation lifetime.

Permanent invitations have no invitation expiration time, but each
entry still generates a new short-lived JWT.

## Organizer accounts

The account utility supports:

```bash
jitsi-invite-user list
jitsi-invite-user add USERNAME
jitsi-invite-user delete USERNAME
```

Adding an existing username updates its password.

Usernames are restricted to:

```text
A-Z a-z 0-9 . _ -
```

Passwords are collected through `systemd-ask-password`, and the
htpasswd file is updated through a temporary file and atomic rename.

## Jitsi invitation controls

The built-in Jitsi invitation functions must be disabled because they
produce a bare room URL without JWT:

```javascript
config.disableInviteFunctions = true;
```

Guests must receive only URLs generated by the portal:

```text
https://meet.example.com/join/<random-code>
```

## Tested software versions

The reference implementation was tested with:

| Component | Version |
|---|---:|
| Debian | 12 |
| Python | 3.11.2 |
| Jitsi Meet | 2.0.10078-1 |
| Jitsi Meet Prosody | 1.0.8448-1 |
| Jicofo | 1.0-1124-1 |
| Jitsi Videobridge 2 | 2.3-209-gb5fbe618-1 |
| Prosody | 0.12.3 |
| nginx | 1.22.1 |
| coturn | 4.6.1 |

Compatibility with other releases has not yet been fully validated.

## Verified behavior

The reference deployment successfully verified:

- protected organizer portal returns HTTP 401 without credentials;
- valid organizer credentials return HTTP 200;
- invalid public invitation codes return HTTP 404;
- organizers see only invitations they created;
- guests are denied before a moderator joins;
- moderators receive owner affiliation;
- guests receive member affiliation;
- guests do not receive moderator privileges;
- permanent invitations survive room reuse;
- repeated entries generate new JWT audit events;
- the backend has no TCP listener;
- the health endpoint returns `ok`;
- controlled service restarts complete successfully.

## Validation

Basic source validation:

```bash
python3 -m py_compile app/app.py
python3 -m py_compile scripts/jitsi-jwt
bash -n scripts/jitsi-invite-user
luac5.4 -p prosody/mod_token_roles.lua
systemd-analyze verify systemd/jitsi-invite.service
```

Validate nginx before reloading:

```bash
nginx -t
```

Validate Prosody configuration:

```bash
prosodyctl check config
```

## Known limitations

- Installation is not yet fully automated.
- A clean installation on a fresh Debian VM remains to be tested.
- Guest rate limiting is stored in process memory.
- Automated SQLite backup and retention are not yet implemented.
- Portal HTML and CSS are embedded in the Python application.
- Portal localization currently requires source changes.
- Automated role integration tests are not yet included.
- Compatibility must be rechecked after Jitsi or Prosody upgrades.

## Upgrade safety

Jicofo automatic ownership must remain disabled so that participant
roles are determined by the JWT-aware Prosody module:

```hocon
jicofo.conference.enable-auto-owner = false
```

Install the custom Prosody module outside package-managed directories:

```text
/usr/local/lib/prosody/modules/mod_token_roles.lua
```

Ensure the effective Prosody plugin path contains both:

```lua
plugin_paths = {
    "/usr/local/lib/prosody/modules";
    "/usr/share/jitsi-meet/prosody-plugins/";
}
```

After every Jitsi, Prosody, Lua, Jicofo, or Jitsi Videobridge upgrade,
repeat the authentication and role test plan.

## Secret handling

Never commit:

- production `.env` files;
- JWT signing secrets;
- real JWTs;
- password hashes;
- SQLite databases;
- private invitation codes;
- TLS private keys;
- Prosody component secrets;
- Jicofo credentials;
- TURN secrets;
- production logs;
- deployment backups or checkpoints.

See [SECURITY.md](SECURITY.md) for additional guidance.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
See [NOTICE](NOTICE) for the project attribution notice.
