# Security Policy

## Project status

Jitsi JWT Invite Portal is currently a working reference
implementation. It has been tested in a real deployment, but automated
installation and clean-environment validation are still in progress.

Review all configuration examples before using them in a production
system.

## Reporting a vulnerability

Do not disclose exploitable security issues in a public GitHub issue.

Use a private GitHub security advisory when the repository supports it,
or contact the repository maintainer privately.

Include:

- affected component;
- affected version or commit;
- reproduction steps;
- expected and actual behavior;
- security impact;
- relevant logs with secrets and personal data removed.

## Sensitive data

Never include the following in reports, issues, pull requests, or test
fixtures:

- JWT signing secrets;
- usable JWTs;
- organizer password hashes;
- invitation codes from real deployments;
- SQLite production databases;
- TLS private keys;
- Prosody, Jicofo, or TURN credentials;
- production IP addresses unless strictly necessary;
- unredacted production logs.

## Security assumptions

The design assumes:

- HTTPS is correctly configured;
- nginx is the only HTTP entry point;
- the portal backend is reachable only through its Unix socket;
- `/invite/` is protected by nginx Basic Auth;
- `/join/` explicitly clears `X-Remote-User`;
- the JWT signing secret is sufficiently random;
- production environment files have restrictive permissions;
- organizer accounts are individual and not shared;
- server administrators protect root access and backups.

## Operational recommendations

- Rotate the JWT signing secret after suspected disclosure.
- Revoke affected invitation links.
- Review SQLite audit events after suspicious activity.
- Validate nginx and Prosody configuration before every reload.
- Repeat moderator/guest role tests after package upgrades.
- Back up the SQLite database using a transaction-safe method.
- Do not restore databases or configuration files from untrusted hosts.

## Supported versions

Security fixes are initially expected to target the latest repository
state until a formal release policy is established.
