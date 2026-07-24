#!/bin/bash
set -euo pipefail

SCRIPT="$(
    cd "$(dirname "$0")/.." &&
    pwd
)/scripts/jitsi-invite-user"

TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

AUTH_FILE="${TEST_ROOT}/jitsi-invite.htpasswd"
CURRENT_USER="$(id -un)"
CURRENT_GROUP="$(id -gn)"

run_tool() {
    env \
        JITSI_INVITE_AUTH_FILE="$AUTH_FILE" \
        JITSI_INVITE_AUTH_OWNER="$CURRENT_USER" \
        JITSI_INVITE_AUTH_GROUP="$CURRENT_GROUP" \
        JITSI_INVITE_AUTH_READER="$CURRENT_USER" \
        JITSI_INVITE_NGINX_BIN=/bin/true \
        JITSI_INVITE_SYSTEMCTL_BIN=/bin/true \
        "$SCRIPT" "$@"
}

echo "Test: mismatched confirmation is rejected"

set +e
printf '%s\n%s\n' \
    'Mismatch-Password-A' \
    'Mismatch-Password-B' |
    run_tool add test.user \
        >"${TEST_ROOT}/mismatch.out" 2>&1
rc=$?
set -e

if [ "$rc" -eq 0 ]; then
    echo "FAIL: mismatched passwords were accepted"
    exit 1
fi

grep -Fq \
    'ERROR: passwords do not match' \
    "${TEST_ROOT}/mismatch.out"

if [ -e "$AUTH_FILE" ]; then
    echo "FAIL: password file was created after mismatch"
    exit 1
fi

echo "PASS: mismatched confirmation rejected"

echo "Test: matching visible password creates APR1 record"

PASSWORD='Aa9-Visible-Password-Test'

printf '%s\n%s\n' \
    "$PASSWORD" \
    "$PASSWORD" |
    run_tool add test.user \
        >"${TEST_ROOT}/success.out" 2>&1

test -s "$AUTH_FILE"

RECORD="$(cat "$AUTH_FILE")"
USERNAME="${RECORD%%:*}"
PASSWORD_HASH="${RECORD#*:}"

test "$USERNAME" = "test.user"

case "$PASSWORD_HASH" in
    (\$apr1\$*)
        ;;
    (*)
        echo "FAIL: generated hash is not APR1"
        exit 1
        ;;
esac

SALT="$(
    printf '%s' "$PASSWORD_HASH" |
    cut -d'$' -f3
)"

CALCULATED_HASH="$(
    printf '%s' "$PASSWORD" |
    openssl passwd \
        -apr1 \
        -salt "$SALT" \
        -stdin
)"

test "$CALCULATED_HASH" = "$PASSWORD_HASH"

grep -Fq \
    "Assigned plaintext password for 'test.user': ${PASSWORD}" \
    "${TEST_ROOT}/success.out"

grep -Fq \
    "APR1 hash for 'test.user': ${PASSWORD_HASH}" \
    "${TEST_ROOT}/success.out"

MODE="$(stat -c '%a' "$AUTH_FILE")"
test "$MODE" = "640"

echo "PASS: APR1 record and sensitive output verified"

echo "Test: updating an existing username keeps one record"

PASSWORD_2='Bb8-Updated-Password-Test'

printf '%s\n%s\n' \
    "$PASSWORD_2" \
    "$PASSWORD_2" |
    run_tool add test.user \
        >"${TEST_ROOT}/update.out" 2>&1

RECORD_COUNT="$(
    grep -c '^test\.user:' "$AUTH_FILE"
)"

test "$RECORD_COUNT" -eq 1

LIST_OUTPUT="$(run_tool list)"
test "$LIST_OUTPUT" = "test.user"

echo "PASS: existing account updated atomically"

echo "Test: credentials remain available if nginx validation fails"

FAIL_AUTH_FILE="${TEST_ROOT}/failure.htpasswd"
FAIL_PASSWORD='Cc7-Validation-Failure-Test'

set +e
printf '%s\n%s\n' \
    "$FAIL_PASSWORD" \
    "$FAIL_PASSWORD" |
    env \
        JITSI_INVITE_AUTH_FILE="$FAIL_AUTH_FILE" \
        JITSI_INVITE_AUTH_OWNER="$CURRENT_USER" \
        JITSI_INVITE_AUTH_GROUP="$CURRENT_GROUP" \
        JITSI_INVITE_AUTH_READER="$CURRENT_USER" \
        JITSI_INVITE_NGINX_BIN=/bin/false \
        JITSI_INVITE_SYSTEMCTL_BIN=/bin/true \
        "$SCRIPT" add failed.user \
        >"${TEST_ROOT}/nginx-failure.out" 2>&1
rc=$?
set -e

if [ "$rc" -eq 0 ]; then
    echo "FAIL: nginx validation failure returned success"
    exit 1
fi

test -s "$FAIL_AUTH_FILE"

FAIL_HASH="$(
    cut -d: -f2 "$FAIL_AUTH_FILE"
)"

case "$FAIL_HASH" in
    (\$apr1\$*)
        ;;
    (*)
        echo "FAIL: failure-path hash is not APR1"
        exit 1
        ;;
esac

grep -Fq \
    "Assigned plaintext password for 'failed.user': ${FAIL_PASSWORD}" \
    "${TEST_ROOT}/nginx-failure.out"

grep -Fq \
    "APR1 hash for 'failed.user': ${FAIL_HASH}" \
    "${TEST_ROOT}/nginx-failure.out"

grep -Fq \
    "ERROR: account file was updated, but nginx configuration validation failed." \
    "${TEST_ROOT}/nginx-failure.out"

if grep -Fq \
    "Portal user 'failed.user' created or updated." \
    "${TEST_ROOT}/nginx-failure.out"
then
    echo "FAIL: command reported complete success after nginx failure"
    exit 1
fi

echo "PASS: partial-success state is reported without losing credentials"

echo "Test: deleting an existing account removes its record"

run_tool delete test.user >"${TEST_ROOT}/delete-success.out" 2>&1

if grep -q "^test\.user:" "$AUTH_FILE"; then
    echo "FAIL: deleted account remains in password file"
    exit 1
fi

LIST_OUTPUT="$(run_tool list)"
test "$LIST_OUTPUT" = "No portal users."

grep -Fq "test.user" "${TEST_ROOT}/delete-success.out"
grep -Fq "deleted." "${TEST_ROOT}/delete-success.out"

echo "PASS: existing account deleted"
echo "All jitsi-invite-user tests passed."
