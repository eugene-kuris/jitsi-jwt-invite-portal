# Administrator Guide: Organizer Accounts

This guide explains how a server administrator creates, updates, lists, and
removes accounts that may use the protected Jitsi Invite organizer portal.

## Organizer account versus Jitsi moderator

An **organizer account** is an nginx Basic Auth account for the `/invite/`
portal. An authenticated organizer can create conference invitations and use
the portal's **Enter as moderator** action.

The account is not a Prosody user and is not a permanent Jitsi room
administrator. Moderator status is granted by a short-lived JWT issued for a
specific room.

## Requirements

Run the account-management utility as `root` or through `sudo`:

```bash
sudo /usr/local/sbin/jitsi-invite-user COMMAND
```

The utility manages:

```text
/etc/nginx/jitsi-invite.htpasswd
```

Do not edit that file manually.

## List organizer accounts

```bash
sudo jitsi-invite-user list
```

Example:

```text
alice
bob.smith
conference_admin
```

Only usernames are displayed. Password hashes are never shown by this
command.

## Create an organizer account

```bash
sudo jitsi-invite-user add USERNAME
```

Example:

```bash
sudo jitsi-invite-user add alice
```

A secure password prompt appears. Enter the new password when requested.

Allowed username characters are:

```text
A-Z  a-z  0-9  .  _  -
```

Use an individual account for each organizer. Do not create a shared
department-wide account unless accountability is not required.

## Change an organizer password

Run the same `add` command for an existing username:

```bash
sudo jitsi-invite-user add alice
```

The existing entry is replaced with a new password hash. The username and
that organizer's existing conference records remain unchanged.

## Remove an organizer account

```bash
sudo jitsi-invite-user delete USERNAME
```

Example:

```bash
sudo jitsi-invite-user delete alice
```

Removing the portal account prevents future Basic Auth logins. It does not
delete historical invitation or audit records from SQLite.

Review any still-active invitations owned by that account and revoke them
before removing access.

## Verify nginx after an account change

The utility validates nginx configuration and reloads nginx automatically.
For an additional check:

```bash
sudo nginx -t
sudo systemctl is-active nginx
```

Expected result:

```text
nginx: configuration file /etc/nginx/nginx.conf test is successful
active
```

## Verify the password file safely

Check metadata without printing password hashes:

```bash
sudo stat -c '%A %U:%G %s %n'     /etc/nginx/jitsi-invite.htpasswd
```

The file should be owned by `root:www-data`, readable by nginx, and not
world-readable.

## Recommended administrative policy

- Give every organizer a separate account.
- Use a password manager and a unique strong password.
- Remove accounts immediately when access is no longer required.
- Review active invitations before deleting an organizer.
- Never paste htpasswd contents into tickets, chats, logs, or Git commits.
- Do not grant shell access merely to let someone organize conferences.

## Troubleshooting

### The browser returns `401 Authorization Required`

Confirm that the username exists:

```bash
sudo jitsi-invite-user list
```

Reset the password if necessary:

```bash
sudo jitsi-invite-user add USERNAME
```

Then close all browser windows or clear saved active logins before trying
again.

### The command reports an invalid username

Use only letters, digits, dot, underscore, and hyphen. Spaces and email
addresses containing unsupported characters are rejected.

### nginx validation fails

Do not bypass the validation. Run:

```bash
sudo nginx -t
```

Correct the reported nginx configuration problem first, then repeat the
account command.
