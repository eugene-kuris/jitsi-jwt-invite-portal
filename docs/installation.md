# Installation

This document describes a manual installation of Jitsi JWT Invite Portal on
an **existing Jitsi Meet deployment installed from Debian packages**.

It does not install Jitsi Meet, configure DNS or issue TLS certificates.
Complete those tasks first and confirm that the base Jitsi installation works.

## 1. Obtain a tagged public release

Install from a public, versioned Git tag. Do not copy project
files from the author's private infrastructure and do not reuse
a production working tree.

Install the tools required to obtain the release:

```bash
apt-get update
apt-get install -y ca-certificates git
```

Set `VERSION` to the published release tag and clone it over HTTPS:

```bash
VERSION=v0.1.0-rc1

test ! -e /root/jitsi-jwt-invite-portal || {
    echo "ERROR: /root/jitsi-jwt-invite-portal already exists"
    exit 1
}

git clone \
    --branch "$VERSION" \
    --depth 1 \
    --single-branch \
    https://github.com/eugene-kuris/jitsi-jwt-invite-portal.git \
    /root/jitsi-jwt-invite-portal

REPO=/root/jitsi-jwt-invite-portal
cd "$REPO"

git describe --tags --exact-match
git status --short
```

Continue only if `git describe` prints the requested version tag
and `git status --short` produces no output.

## 2. Scope and assumptions

The original deployment and the current clean-VM validation both use Debian 12. Relevant components include:

- Python 3.11;
- Jitsi Meet installed from Debian packages;
- Prosody 0.12 on the original reference deployment;
- Prosody 13.0.6 in the current clean-VM release-candidate validation;
- Jicofo using `/etc/jitsi/jicofo/jicofo.conf`;
- nginx terminating HTTPS;
- coturn already configured by the Jitsi installation;
- root access for installation.

The commands below use `meet.example.com`. Replace it with the real Jitsi
domain. Never copy production secrets into documentation, shell history,
issues or commits.

Set the repository path:

```bash
REPO=/root/jitsi-jwt-invite-portal
cd "$REPO"
```

Before changing a production host, create a VM snapshot or another tested
rollback point.

## 3. Install required packages

```bash
apt-get update
apt-get install -y \
    apache2-utils \
    curl \
    lua5.4 \
    openssl \
    python3
```

Python's standard library supplies the HTTP server, SQLite and JWT signing
primitives used by the portal. No Python packages from PyPI are required.

## 4. Create the service account and directories

```bash
getent group jitsi-invite >/dev/null \
    || groupadd --system jitsi-invite

id -u jitsi-invite >/dev/null 2>&1 \
    || useradd \
        --system \
        --gid jitsi-invite \
        --home-dir /nonexistent \
        --shell /usr/sbin/nologin \
        jitsi-invite

install -d -m 0755 -o root -g root \
    /opt/jitsi-invite

install -d -m 0750 -o root -g jitsi-invite \
    /etc/jitsi-invite

install -d -m 0750 -o jitsi-invite -g www-data \
    /var/lib/jitsi-invite
```

## 5. Install the portal and administration utilities

```bash
install -m 0755 -o root -g root \
    app/app.py \
    /opt/jitsi-invite/app.py

install -m 0750 -o root -g root \
    scripts/jitsi-invite-user \
    /usr/local/sbin/jitsi-invite-user

install -m 0750 -o root -g jitsi-invite \
    scripts/jitsi-jwt \
    /usr/local/sbin/jitsi-jwt
```

The runtime application is intentionally installed outside the Git working
tree. Deploying a later revision therefore requires an explicit copy followed
by validation and a controlled service restart.

## 6. Create the secret configuration files

Install the examples:

```bash
install -m 0640 -o root -g jitsi-invite \
    config/jwt.env.example \
    /etc/jitsi-invite/jwt.env

install -m 0640 -o root -g jitsi-invite \
    config/portal.env.example \
    /etc/jitsi-invite/portal.env
```

Generate secrets without printing them into public logs:

```bash
JWT_SECRET="$(openssl rand -hex 48)"
CSRF_SECRET="$(openssl rand -hex 32)"
```

Edit `/etc/jitsi-invite/jwt.env` so that it contains:

```dotenv
JITSI_APP_ID=replace-with-a-private-application-id
JITSI_APP_SECRET=replace-with-the-generated-jwt-secret
JITSI_DOMAIN=meet.example.com
```

Edit `/etc/jitsi-invite/portal.env` so that it contains:

```dotenv
JITSI_INVITE_BASE_URL=https://meet.example.com
JITSI_INVITE_DB=/var/lib/jitsi-invite/invite.db
JITSI_INVITE_CSRF_SECRET=replace-with-the-generated-csrf-secret
JITSI_INVITE_GUEST_TOKEN_TTL=14400
JITSI_INVITE_MODERATOR_TOKEN_TTL=28800
```

The `JITSI_APP_ID` and `JITSI_APP_SECRET` values must exactly match the
corresponding Prosody `app_id` and `app_secret`.

After editing, remove the temporary shell variables:

```bash
unset JWT_SECRET CSRF_SECRET
```

Confirm permissions without displaying file contents:

```bash
stat -c '%A %U:%G %n' \
    /etc/jitsi-invite/jwt.env \
    /etc/jitsi-invite/portal.env
```

Expected ownership is `root:jitsi-invite` with mode `0640`.

## 7. Install the Prosody role module in an upgrade-safe directory

Do not place the custom module only in the Jitsi package directory. A package
upgrade may replace or remove files there.

```bash
install -d -m 0755 -o root -g root \
    /usr/local/lib/prosody/modules

install -m 0644 -o root -g root \
    prosody/mod_token_roles.lua \
    /usr/local/lib/prosody/modules/mod_token_roles.lua
```

The effective site configuration must search the local directory first while
retaining the packaged Jitsi modules:

```lua
plugin_paths = {
    "/usr/local/lib/prosody/modules";
    "/usr/share/jitsi-meet/prosody-plugins/";
}
```

The Jitsi VirtualHost must use strict token authentication:

```lua
VirtualHost "meet.example.com"
    authentication = "token"
    app_id = "replace-with-the-private-application-id"
    app_secret = "replace-with-the-generated-jwt-secret"
```

Do **not** enable `allow_empty_token` and do not create an anonymous guest
VirtualHost for this design.

In the conference MUC component, preserve the modules required by the
installed Jitsi release and enable both:

```lua
"token_verification";
"token_roles";
```

Use `examples/prosody-virtualhost.cfg.lua` as a fragment, not as a complete
replacement for the site configuration generated by Jitsi packages.

## 8. Disable Jicofo automatic ownership

This setting is essential. If Jicofo automatic ownership remains enabled, it
can promote the first ordinary participant to owner and defeat the moderator
claim enforced by the Prosody module.

Add exactly one declaration to `/etc/jitsi/jicofo/jicofo.conf`:

```hocon
jicofo.conference.enable-auto-owner = false
```

The repository fragment is available at `examples/jicofo.conf`.

## 9. Disable Jitsi's bare invitation controls

In `/etc/jitsi/meet/meet.example.com-config.js`, set:

```javascript
config.disableInviteFunctions = true;
```

This prevents the interface from offering a room URL without the required
JWT. It is a user-interface safeguard; strict Prosody token authentication
remains the actual access-control boundary.

## 10. Install and configure the systemd service

```bash
install -m 0644 -o root -g root \
    systemd/jitsi-invite.service \
    /etc/systemd/system/jitsi-invite.service

systemctl daemon-reload
systemctl enable jitsi-invite.service
```

The service creates `/run/jitsi-invite` through `RuntimeDirectory` and exposes
only this Unix socket:

```text
/run/jitsi-invite/jitsi-invite.sock
```

It must not listen on a TCP port.

## 11. Create the first organizer account

Run:

```bash
jitsi-invite-user add ORGANIZER_NAME
```

The utility prompts for a password through `systemd-ask-password` and writes
the nginx htpasswd file atomically.

Verify only metadata, not hashes:

```bash
stat -c '%A %U:%G %s %n' \
    /etc/nginx/jitsi-invite.htpasswd
```

The file should be readable by nginx and not world-readable.

## 12. Add the nginx routes

Merge `nginx/jitsi-invite.conf` **inside the existing HTTPS server block** for
the Jitsi domain.

The required behavior is:

- `/invite/` is protected by nginx Basic Auth;
- nginx passes the authenticated username in `X-Remote-User`;
- `/join/` is public but explicitly clears `X-Remote-User`;
- both locations proxy to the portal Unix socket;
- the application remains unreachable over TCP.

Do not install the fragment as a second independent virtual host.

## 13. Validate before restart

Validate source and configuration syntax:

```bash
python3 -m py_compile /opt/jitsi-invite/app.py
python3 -m py_compile /usr/local/sbin/jitsi-jwt
bash -n /usr/local/sbin/jitsi-invite-user
luac5.4 -p /usr/local/lib/prosody/modules/mod_token_roles.lua
systemd-analyze verify /etc/systemd/system/jitsi-invite.service
nginx -t
prosodyctl check config
```

`prosodyctl check config` may return a nonzero code for unrelated warnings in
an existing Jitsi-generated configuration. Read the complete message and do
not ignore module-loading or syntax errors.

Confirm that `enable-auto-owner` occurs exactly once:

```bash
grep -nE \
  '^[[:space:]]*(jicofo\.conference\.)?enable-auto-owner[[:space:]]*=' \
  /etc/jitsi/jicofo/jicofo.conf
```

## 14. Start services in a controlled order

```bash
systemctl restart prosody
systemctl restart jicofo
systemctl start jitsi-invite
systemctl reload nginx
```

Confirm state:

```bash
systemctl is-active \
    prosody \
    jicofo \
    jitsi-videobridge2 \
    jitsi-invite

systemctl --no-pager --full status jitsi-invite
```

Confirm the socket and absence of a portal TCP listener:

```bash
stat -c '%A %U:%G %n' \
    /run/jitsi-invite/jitsi-invite.sock

ss -lntp
```

## 15. HTTP smoke tests

Without organizer credentials, the protected route must return `401` and a
Basic Auth challenge:

```bash
curl -skS -o /dev/null -D - \
    https://meet.example.com/invite/
```

Expected headers include:

```text
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Basic realm="Jitsi Invite"
```

After authentication, the organizer portal should return `200`.

An invalid invitation code must return `404`:

```bash
curl -skS -o /dev/null -w '%{http_code}\n' \
    https://meet.example.com/join/not-a-real-invitation
```

## 16. Mandatory browser role test

Use a **new room** for each role test.

1. Authenticate to `/invite/` as an organizer.
2. Create a temporary conference.
3. Open the moderator entry first.
4. Confirm that the moderator has the Jitsi moderator badge and controls.
5. Open the guest invitation in a different browser profile.
6. Confirm that the guest enters but has no moderator badge or controls.
7. Open the bare room URL without `?jwt=...`.
8. Confirm that Jitsi rejects the unauthenticated entry.
9. Revoke the test invitation.
10. Review service logs for role or authentication errors.

A deployment is not accepted until all four outcomes are verified:

```text
Moderator: moderator
Guest: participant
Bare room URL: rejected
Audio/video: working
```

## 17. Rollback

Before every change, copy the original files to a root-only checkpoint.
A minimal rollback consists of:

1. restoring the previous Prosody site configuration;
2. restoring the previous Jicofo configuration;
3. restoring or removing the local custom module as appropriate;
4. restoring the previous nginx site configuration;
5. restarting Prosody and Jicofo;
6. reloading nginx;
7. confirming service state and repeating the browser test.

Do not delete checkpoints until the functional browser test has passed.

## 18. Upgrade procedure

After any Jitsi Meet, Prosody, Lua, Jicofo, Jitsi Videobridge or nginx upgrade:

1. verify that `/usr/local/lib/prosody/modules/mod_token_roles.lua` remains;
2. verify that the effective `plugin_paths` still contains the local directory
   before the package directory;
3. verify `jicofo.conference.enable-auto-owner = false`;
4. run all syntax and service checks;
5. repeat the moderator, guest and bare-room browser test;
6. compare installed application files with the intended repository commit.

## 19. Secret handling

Never publish or paste:

- `/etc/jitsi-invite/jwt.env`;
- `/etc/jitsi-invite/portal.env`;
- JWTs or full JWT-bearing links;
- htpasswd hashes;
- production SQLite databases;
- TLS private keys;
- Prosody component secrets;
- Jicofo credentials;
- TURN shared secrets;
- production logs without redaction;
- production IP addresses or private hostnames.

See `SECURITY.md` for the project reporting policy.
