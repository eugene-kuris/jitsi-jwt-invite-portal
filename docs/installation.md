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
VERSION=v0.1.0-rc2

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

## 3. Network prerequisites

Define and verify the network topology before changing the portal, Prosody or
Jicofo configuration. Jitsi signaling may appear to work while conferences
fail as soon as media is routed through Jitsi Videobridge.

The deployment requires:

- a stable Jitsi FQDN with correct public DNS;
- a stable public IPv4 address;
- a stable server IPv4 address when the server is behind NAT;
- public TCP port 443 reaching the Jitsi service, either directly at nginx or
  at a trusted HTTPS reverse proxy that forwards to the intended Jitsi
  virtual host;
- inbound TCP port 80 when it is required for HTTP-to-HTTPS redirection or
  certificate issuance;
- inbound UDP port 10000 reaching the Jitsi Videobridge host directly or
  through an explicit port-forwarding rule;
- host and perimeter firewall rules permitting the required traffic;
- outbound DNS, HTTPS and STUN/TURN access required by the Jitsi deployment.

Determine which topology applies:

1. the public IPv4 address is assigned directly to a server interface;
2. the Jitsi server has a private IPv4 address behind NAT.

### Public IPv4 directly assigned to the server

When the public IPv4 address is assigned directly to a server interface, an
explicit Jitsi Videobridge static NAT mapping is normally unnecessary.

Verify the interface address, public DNS resolution and UDP listener:

```bash
ip -4 addr show
getent ahostsv4 meet.example.com
ss -H -lunp "( sport = :10000 )" | head -n 3
```

The public DNS address must match an address assigned to the server, and
Jitsi Videobridge must listen on UDP port 10000.

If nginx terminates TLS on the Jitsi server, inbound HTTPS must reach that
nginx virtual host. If TLS terminates on a trusted reverse proxy, restrict
the backend path appropriately and confirm that the proxy forwards requests
to the intended Jitsi virtual host.

### Private IPv4 behind NAT

When the Jitsi server has a private IPv4 address and the public IPv4 address
is assigned to an edge router or firewall, configure all of the following:

- keep both the private server address and public address stable;
- forward public UDP port 10000 to UDP port 10000 on the Jitsi server;
- permit the forwarded traffic through perimeter and host firewalls;
- configure Jitsi Videobridge to advertise the public address;
- verify the result with at least three simultaneous conference clients.

An HTTPS reverse proxy handles signaling and web traffic only. It does not
proxy Jitsi Videobridge media. UDP port 10000 must be forwarded separately
to the private IPv4 address of the Jitsi server.

Back up `/etc/jitsi/videobridge/jvb.conf` before changing it. Add the
appropriate static mapping inside the existing configuration, replacing the
example addresses:

```hocon
ice4j {
    harvest {
        mapping {
            aws {
                enabled = false
            }
            stun {
                addresses = ["meet-jit-si-turnrelay.jitsi.net:443"]
            }
            static-mappings = [
                {
                    local-address = "192.0.2.10"
                    public-address = "198.51.100.20"
                }
            ]
        }
    }
}
```

`local-address` is the stable private IPv4 address assigned to the Jitsi
server. `public-address` is the stable public IPv4 address whose UDP port
10000 is forwarded to that server.

Do not create a second conflicting `ice4j` block. Merge the mapping into the
existing `/etc/jitsi/videobridge/jvb.conf` structure, then restart
Jitsi Videobridge:

```bash
systemctl restart jitsi-videobridge2
```

Verify the targeted UDP listener and health endpoint:

```bash
ss -H -lunp "( sport = :10000 )" | head -n 3

curl -sS \
    -o /dev/null \
    -w "HTTP %{http_code}\n" \
    http://127.0.0.1:8080/about/health
```

The expected health result is `HTTP 200`. Review Jicofo and Jitsi
Videobridge logs and do not continue if they report bridge-health failures,
ICE harvesting failures, or `No valid IP addresses available for
harvesting`.

Do not continue until the topology, address ownership, TLS termination point
and UDP port-forwarding path are known.

## 4. Install required packages

```bash
apt-get update
apt-get install -y \
    apache2-utils \
    curl \
    lua5.2 \
    lua5.4 \
    openssl \
    python3
```

Python's standard library supplies the HTTP server, SQLite and JWT signing
primitives used by the portal. No Python packages from PyPI are required.

Prosody packages on Debian 12 may use different Lua runtimes. The reference
deployment uses Lua 5.4, while the clean-VM validation host uses Lua 5.2.
Both command-line compilers are therefore installed. During validation, use
the compiler matching the `Lua version` reported by `prosodyctl about`.

## 5. Create the service account and directories

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

## 6. Install the portal and administration utilities

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

## 7. Create the secret configuration files

Install the examples:

```bash
install -m 0640 -o root -g jitsi-invite \
    config/jwt.env.example \
    /etc/jitsi-invite/jwt.env

install -m 0640 -o root -g jitsi-invite \
    config/portal.env.example \
    /etc/jitsi-invite/portal.env
```

Generate a separate secret for portal form protection:

```bash
CSRF_SECRET="$(openssl rand -hex 32)"
```

The JWT signing secret is shared with Prosody. Choose exactly one of
the following cases.

### Existing strict-JWT Jitsi deployment

Reuse the active Prosody `app_id` and `app_secret`. Do not generate a
second unrelated JWT secret: tokens signed by it would be rejected by
Prosody.

Copy the existing values into `/etc/jitsi-invite/jwt.env`:

```dotenv
JITSI_APP_ID=the-active-prosody-app-id
JITSI_APP_SECRET=the-active-prosody-app-secret
JITSI_DOMAIN=meet.example.com
```

### New JWT configuration or intentional secret rotation

Generate a new value only when the Prosody `app_secret` and the portal
configuration will be changed together:

```bash
JWT_SECRET="$(openssl rand -hex 48)"
```

Update both Prosody and `/etc/jitsi-invite/jwt.env` before restarting
Prosody. Existing JWTs signed with the previous secret will stop
working after a rotation.

Configure `/etc/jitsi-invite/portal.env`:

```dotenv
JITSI_INVITE_BASE_URL=https://meet.example.com
JITSI_INVITE_DB=/var/lib/jitsi-invite/invite.db
JITSI_INVITE_CSRF_SECRET=replace-with-the-generated-csrf-secret
JITSI_INVITE_GUEST_TOKEN_TTL=14400
JITSI_INVITE_MODERATOR_TOKEN_TTL=28800
JITSI_INVITE_SOCKET=/run/jitsi-invite/jitsi-invite.sock
```

The `JITSI_APP_ID` and `JITSI_APP_SECRET` values must exactly match
the corresponding active Prosody `app_id` and `app_secret`.

After editing, remove temporary shell variables that were created:

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

## 8. Install the Prosody role module in an upgrade-safe directory

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
    app_secret = "replace-with-the-active-or-new-shared-jwt-secret"
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

## 9. Disable Jicofo automatic ownership

This setting is essential. If Jicofo automatic ownership remains enabled, it
can promote the first ordinary participant to owner and defeat the moderator
claim enforced by the Prosody module.

Add exactly one declaration to `/etc/jitsi/jicofo/jicofo.conf`:

```hocon
jicofo.conference.enable-auto-owner = false
```

The repository fragment is available at `examples/jicofo.conf`.

## 10. Disable Jitsi's bare invitation controls

In `/etc/jitsi/meet/meet.example.com-config.js`, set:

```javascript
config.disableInviteFunctions = true;
```

This prevents the interface from offering a room URL without the required
JWT. It is a user-interface safeguard; strict Prosody token authentication
remains the actual access-control boundary.

## 11. Install and configure the systemd service

```bash
install -m 0644 -o root -g root \
    systemd/jitsi-invite.service \
    /etc/systemd/system/jitsi-invite.service

systemctl daemon-reload
systemctl enable --now jitsi-invite.service
```

The service creates `/run/jitsi-invite` through `RuntimeDirectory` and exposes
only this Unix socket:

```text
/run/jitsi-invite/jitsi-invite.sock
```

It must not listen on a TCP port.

## 12. Create the first organizer account

Run:

```bash
jitsi-invite-user add ORGANIZER_NAME
```

The utility intentionally reads the password with terminal echo enabled and
asks for the same value a second time. This mode is intended for a
physically controlled, single-user administrative session. It makes
unexpected keyboard layouts, automatic language switching and faulty
keyboard keys easier to detect.

After a successful update, the assigned plaintext password is printed
once together with the resulting APR1 hash. Both values are sensitive
and may remain in terminal scrollback. Do not run this command where
another person can observe the screen or where the terminal is shared
or recorded.

The nginx htpasswd file is updated through a temporary file and atomic
rename.

Verify only metadata, not hashes:

```bash
stat -c '%A %U:%G %s %n' \
    /etc/nginx/jitsi-invite.htpasswd
```

The file should be readable by nginx and not world-readable.

## 13. Add the nginx routes

Merge `nginx/jitsi-invite.conf` **inside the existing HTTPS server block** for
the Jitsi domain.

The required behavior is:

- `/invite/` is protected by nginx Basic Auth;
- nginx passes the authenticated username in `X-Remote-User`;
- `/join/` is public but explicitly clears `X-Remote-User`;
- both locations proxy to the portal Unix socket;
- the application remains unreachable over TCP.

Do not install the fragment as a second independent virtual host.

## 14. Validate before restart

Validate source and configuration syntax:

```bash
python3 -m py_compile /opt/jitsi-invite/app.py
python3 -m py_compile /usr/local/sbin/jitsi-jwt
bash -n /usr/local/sbin/jitsi-invite-user
PROSODY_LUA_VERSION="$(
    prosodyctl about 2>/dev/null |
    sed -nE "s/^Lua version:.* ([0-9]+\.[0-9]+)$/\1/p"
)"

case "$PROSODY_LUA_VERSION" in
    5.2|5.4)
        "luac${PROSODY_LUA_VERSION}" \
            -p /usr/local/lib/prosody/modules/mod_token_roles.lua
        ;;
    *)
        echo "ERROR: unsupported or undetected Prosody Lua runtime" >&2
        exit 1
        ;;
esac

unset PROSODY_LUA_VERSION
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

## 15. Start services in a controlled order

```bash
systemctl restart prosody
systemctl restart jicofo
systemctl restart jitsi-invite
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

Verify the Jitsi Videobridge health endpoint:

```bash
curl -sS \
    -o /dev/null \
    -w "HTTP %{http_code}\n" \
    http://127.0.0.1:8080/about/health
```

Expected result:

```text
HTTP 200
```

Confirm the Unix socket and verify that the portal process has no TCP
listener:

```bash
stat -c '%A %U:%G %n' \
    /run/jitsi-invite/jitsi-invite.sock

ss -H -lxnp |
grep -F '/run/jitsi-invite/jitsi-invite.sock'

PORTAL_PID="$(systemctl show -p MainPID --value jitsi-invite.service)"

if ss -H -lntp | grep -Fq "pid=${PORTAL_PID},"; then
    echo "ERROR: portal process has a TCP listener" >&2
    exit 1
fi

echo "Portal TCP listener: none"
unset PORTAL_PID
```

## 16. HTTP smoke tests

First verify the backend contract directly through the Unix socket.
The organizer route must reject a request without an authenticated
organizer identity:

```bash
curl -sS \
    --unix-socket /run/jitsi-invite/jitsi-invite.sock \
    -o /dev/null \
    -w '%{http_code}\n' \
    http://localhost/invite/
```

Expected result:

```text
403
```

A request carrying the trusted identity normally supplied by nginx
must succeed:

```bash
curl -sS \
    --unix-socket /run/jitsi-invite/jitsi-invite.sock \
    -H 'X-Remote-User: local-smoke-test' \
    -o /dev/null \
    -w '%{http_code}\n' \
    http://localhost/invite/
```

Expected result:

```text
200
```

The portal does not expose a `/health` route. A `404` response from
that path is therefore not a service failure.

Without organizer credentials, the public protected route must return
`401` and a Basic Auth challenge:

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

## 17. Mandatory browser role test

Use a **new room** for each role test.

The acceptance test requires at least three simultaneous clients. A
two-client conference may remain in peer-to-peer mode and therefore does not
prove that Jitsi Videobridge, UDP port 10000, ICE advertisement, or NAT
configuration works.

1. Authenticate to `/invite/` as an organizer.
2. Create a temporary conference.
3. Before opening the moderator entry, try the guest invitation and confirm
   that the guest cannot enter the empty room.
4. Open the moderator entry.
5. Confirm that the moderator has the Jitsi moderator badge and controls.
6. Open the guest invitation in a different browser profile.
7. Confirm that the first guest enters but has no moderator badge or
   controls.
8. Open the guest invitation on a third client, preferably a separate
   device using an external network such as mobile Internet.
9. Confirm that the second guest enters but has no moderator badge or
   controls.
10. Keep all three clients connected simultaneously and verify
    bidirectional audio and video.
11. Open the bare room URL without `?jwt=...`.
12. Confirm that Jitsi rejects the unauthenticated entry.
13. Repeat the Jitsi Videobridge health check and require `HTTP 200`.
14. Revoke the test invitation.
15. Review Prosody, Jicofo and Jitsi Videobridge logs for role,
    authentication, bridge-health or ICE errors.

A deployment is not accepted until all outcomes are verified:

```text
Moderator: moderator
First guest: participant
Second guest: participant
Guest before moderator: rejected
Bare room URL: rejected
Three-client audio/video: working
Jitsi Videobridge health: HTTP 200
New bridge-health or ICE errors: none
```

## 18. Rollback

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

## 19. Upgrade procedure

After any Jitsi Meet, Prosody, Lua, Jicofo, Jitsi Videobridge or nginx upgrade:

1. verify that `/usr/local/lib/prosody/modules/mod_token_roles.lua` remains;
2. verify that the effective `plugin_paths` still contains the local directory
   before the package directory;
3. verify `jicofo.conference.enable-auto-owner = false`;
4. run all syntax and service checks;
5. repeat the moderator, guest and bare-room browser test;
6. compare installed application files with the intended repository commit.

## 20. Secret handling

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
