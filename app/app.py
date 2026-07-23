#!/usr/bin/python3

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import signal
import socketserver
import sqlite3
import sys
import threading
import time
import unicodedata
import uuid

from collections import defaultdict, deque
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit


JWT_ENV_PATH = Path("/etc/jitsi-invite/jwt.env")
PORTAL_ENV_PATH = Path("/etc/jitsi-invite/portal.env")

SOCKET_PATH = Path(
    os.environ.get(
        "JITSI_INVITE_SOCKET",
        "/run/jitsi-invite/jitsi-invite.sock",
    )
)

MAX_FORM_BYTES = 8192
ALLOWED_DURATIONS = {1, 2, 4, 8, 12, 24}

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

RATE_LIMIT_COUNT = 10
RATE_LIMIT_WINDOW = 60

RATE_LOCK = threading.Lock()
RATE_BUCKETS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def load_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            raise RuntimeError(f"Invalid configuration line in {path}")

        key, value = line.split("=", 1)
        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]

        result[key.strip()] = value

    return result


JWT_CONFIG = load_env_file(JWT_ENV_PATH)
PORTAL_CONFIG = load_env_file(PORTAL_ENV_PATH)

APP_ID = JWT_CONFIG["JITSI_APP_ID"]
APP_SECRET = JWT_CONFIG["JITSI_APP_SECRET"]
JITSI_DOMAIN = JWT_CONFIG["JITSI_DOMAIN"]

BASE_URL = PORTAL_CONFIG.get(
    "JITSI_INVITE_BASE_URL",
    f"https://{JITSI_DOMAIN}",
).rstrip("/")

DB_PATH = Path(
    PORTAL_CONFIG.get(
        "JITSI_INVITE_DB",
        "/var/lib/jitsi-invite/invite.db",
    )
)

CSRF_SECRET = PORTAL_CONFIG["JITSI_INVITE_CSRF_SECRET"].encode(
    "utf-8"
)

GUEST_TOKEN_TTL = int(
    PORTAL_CONFIG.get(
        "JITSI_INVITE_GUEST_TOKEN_TTL",
        "14400",
    )
)

MODERATOR_TOKEN_TTL = int(
    PORTAL_CONFIG.get(
        "JITSI_INVITE_MODERATOR_TOKEN_TTL",
        "28800",
    )
)

if not BASE_URL.startswith("https://"):
    raise RuntimeError("JITSI_INVITE_BASE_URL must use HTTPS")

if not APP_ID or not APP_SECRET or not JITSI_DOMAIN:
    raise RuntimeError("Incomplete JWT configuration")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

LOG = logging.getLogger("jitsi-invite")


STYLE = """
:root {
  color-scheme: light dark;
  font-family: system-ui, sans-serif;
}

body {
  margin: 0;
  background: #202124;
  color: #f1f3f4;
}

main {
  max-width: 820px;
  margin: 0 auto;
  padding: 32px 18px 64px;
}

section {
  background: #2d2f31;
  border: 1px solid #47494c;
  border-radius: 12px;
  margin: 18px 0;
  padding: 20px;
}

h1, h2 {
  margin-top: 0;
}

label {
  display: block;
  margin: 14px 0 6px;
}

input, select, button {
  box-sizing: border-box;
  font: inherit;
}

input, select {
  width: 100%;
  padding: 11px;
  border: 1px solid #666;
  border-radius: 7px;
  background: #18191a;
  color: #fff;
}

button, .button {
  display: inline-block;
  margin-top: 16px;
  padding: 11px 17px;
  border: 0;
  border-radius: 7px;
  background: #2f7cf6;
  color: #fff;
  cursor: pointer;
  text-decoration: none;
}

button.danger {
  background: #b3261e;
}

code, .link-box {
  display: block;
  overflow-wrap: anywhere;
  padding: 12px;
  border-radius: 7px;
  background: #18191a;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th, td {
  padding: 10px 6px;
  border-bottom: 1px solid #555;
  text-align: left;
  vertical-align: top;
}

small, .muted {
  color: #bdc1c6;
}

.error {
  color: #ffb4ab;
}

.success {
  color: #b7f397;
}

.inline-form {
  display: inline;
}

nav {
  margin-bottom: 20px;
}

nav a {
  color: #8ab4f8;
  margin-right: 14px;
}
"""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_jwt(
    room: str,
    name: str,
    moderator: bool,
    ttl: int,
) -> str:
    now = int(time.time())
    ttl = max(60, ttl)

    header = {
        "alg": "HS256",
        "typ": "JWT",
    }

    payload = {
        "iss": APP_ID,
        "aud": APP_ID,
        "sub": JITSI_DOMAIN,
        "room": room,
        "iat": now,
        "nbf": now - 5,
        "exp": now + ttl,
        "jti": str(uuid.uuid4()),
        "context": {
            "user": {
                "id": str(uuid.uuid4()),
                "name": name,
                "moderator": moderator,
            }
        },
    }

    encoded_header = b64url(
        json.dumps(
            header,
            separators=(",", ":"),
        ).encode("utf-8")
    )

    encoded_payload = b64url(
        json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )

    signing_input = (
        f"{encoded_header}.{encoded_payload}".encode("ascii")
    )

    signature = hmac.new(
        APP_SECRET.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()

    return (
        f"{encoded_header}.{encoded_payload}.{b64url(signature)}"
    )


def make_jitsi_url(
    room: str,
    name: str,
    moderator: bool,
    ttl: int,
) -> str:
    token = make_jwt(
        room=room,
        name=name,
        moderator=moderator,
        ttl=ttl,
    )

    return (
        f"{BASE_URL}/{room}"
        f"?jwt={quote(token, safe='')}"
    )


def clean_text(
    value: str,
    *,
    minimum: int,
    maximum: int,
    field_name: str,
) -> str:
    value = " ".join(value.strip().split())

    if not minimum <= len(value) <= maximum:
        raise ValueError(
            f"{field_name}: допустимо от {minimum} "
            f"до {maximum} символов"
        )

    for character in value:
        category = unicodedata.category(character)

        if category.startswith("C"):
            raise ValueError(
                f"{field_name}: недопустимые управляющие символы"
            )

    return value


def utc_text(timestamp: int) -> str:
    return datetime.fromtimestamp(
        timestamp,
        timezone.utc,
    ).strftime("%Y-%m-%d %H:%M UTC")


def expiry_text(timestamp: int | None) -> str:
    if timestamp is None:
        return "постоянно"

    return utc_text(timestamp)


def database() -> sqlite3.Connection:
    connection = sqlite3.connect(
        DB_PATH,
        timeout=5,
    )

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")

    return connection


def initialise_database() -> None:
    DB_PATH.parent.mkdir(
        mode=0o750,
        parents=True,
        exist_ok=True,
    )

    with database() as connection:
        connection.execute("PRAGMA journal_mode = WAL")

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS invitations (
                code TEXT PRIMARY KEY,
                room TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                organizer_name TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER,
                revoked_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS
                idx_invitations_created_by
            ON invitations(created_by, created_at DESC);

            CREATE INDEX IF NOT EXISTS
                idx_invitations_expires_at
            ON invitations(expires_at);

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_time INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                code TEXT,
                room TEXT,
                actor TEXT,
                remote_ip TEXT,
                detail TEXT
            );

            CREATE INDEX IF NOT EXISTS
                idx_audit_events_time
            ON audit_events(event_time DESC);
            """
        )

    try:
        os.chmod(DB_PATH, 0o640)
    except FileNotFoundError:
        pass


def audit_event(
    event_type: str,
    *,
    invitation: sqlite3.Row | None = None,
    actor: str = "",
    remote_ip: str = "",
    detail: str = "",
) -> None:
    with database() as connection:
        connection.execute(
            """
            INSERT INTO audit_events (
                event_time,
                event_type,
                code,
                room,
                actor,
                remote_ip,
                detail
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                event_type,
                invitation["code"] if invitation else None,
                invitation["room"] if invitation else None,
                actor[:128],
                remote_ip[:128],
                detail[:512],
            ),
        )


def create_invitation(
    *,
    title: str,
    organizer_name: str,
    created_by: str,
    duration_hours: int | None,
) -> sqlite3.Row:
    now = int(time.time())

    expires_at = (
        None
        if duration_hours is None
        else now + duration_hours * 3600
    )

    for _ in range(20):
        code = secrets.token_urlsafe(15)
        room = (
            "meeting-"
            + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            + "-"
            + secrets.token_hex(4)
        )

        try:
            with database() as connection:
                connection.execute(
                    """
                    INSERT INTO invitations (
                        code,
                        room,
                        title,
                        organizer_name,
                        created_by,
                        created_at,
                        expires_at,
                        revoked_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        code,
                        room,
                        title,
                        organizer_name,
                        created_by,
                        now,
                        expires_at,
                    ),
                )

                row = connection.execute(
                    """
                    SELECT *
                    FROM invitations
                    WHERE code = ?
                    """,
                    (code,),
                ).fetchone()

            if row is None:
                raise RuntimeError(
                    "Invitation was created but could not be read"
                )

            return row

        except sqlite3.IntegrityError:
            continue

    raise RuntimeError("Unable to allocate invitation identifier")


def get_invitation(code: str) -> sqlite3.Row | None:
    if not CODE_RE.fullmatch(code):
        return None

    with database() as connection:
        return connection.execute(
            """
            SELECT *
            FROM invitations
            WHERE code = ?
            """,
            (code,),
        ).fetchone()


def invitation_state(row: sqlite3.Row) -> str:
    if row["revoked_at"] is not None:
        return "revoked"

    if (
        row["expires_at"] is not None
        and row["expires_at"] <= int(time.time())
    ):
        return "expired"

    return "active"


def csrf_create(subject: str) -> str:
    expires = int(time.time()) + 3600
    nonce = secrets.token_urlsafe(18)

    payload = (
        f"{subject}\0{expires}\0{nonce}".encode("utf-8")
    )

    signature = hmac.new(
        CSRF_SECRET,
        payload,
        hashlib.sha256,
    ).digest()

    return f"{b64url(payload)}.{b64url(signature)}"


def csrf_verify(
    token: str,
    subject: str,
) -> bool:
    try:
        encoded_payload, encoded_signature = token.split(".", 1)

        payload = base64.urlsafe_b64decode(
            encoded_payload
            + "=" * (-len(encoded_payload) % 4)
        )

        signature = base64.urlsafe_b64decode(
            encoded_signature
            + "=" * (-len(encoded_signature) % 4)
        )

        expected_signature = hmac.new(
            CSRF_SECRET,
            payload,
            hashlib.sha256,
        ).digest()

        if not hmac.compare_digest(
            signature,
            expected_signature,
        ):
            return False

        decoded_subject, decoded_expiry, _ = (
            payload.decode("utf-8").split("\0", 2)
        )

        if decoded_subject != subject:
            return False

        if int(decoded_expiry) < int(time.time()):
            return False

        return True

    except (
        ValueError,
        UnicodeDecodeError,
        base64.binascii.Error,
    ):
        return False


def rate_limit_ok(
    remote_ip: str,
    code: str,
) -> bool:
    now = time.monotonic()
    key = (remote_ip, code)

    with RATE_LOCK:
        bucket = RATE_BUCKETS[key]

        while (
            bucket
            and bucket[0] <= now - RATE_LIMIT_WINDOW
        ):
            bucket.popleft()

        if len(bucket) >= RATE_LIMIT_COUNT:
            return False

        bucket.append(now)
        return True


def layout(
    title: str,
    content: str,
    *,
    navigation: bool = False,
) -> str:
    nav = ""

    if navigation:
        nav = """
        <nav>
          <a href="/invite/">Конференции</a>
          <a href="/invite/new">Создать новую</a>
        </nav>
        """

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta
    name="viewport"
    content="width=device-width, initial-scale=1"
  >
  <title>{html.escape(title)}</title>
  <style>{STYLE}</style>
</head>
<body>
  <main>
    {nav}
    {content}
  </main>
</body>
</html>
"""


class ThreadingUnixHTTPServer(
    socketserver.ThreadingMixIn,
    socketserver.UnixStreamServer,
):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        if os.path.lexists(SOCKET_PATH):
            SOCKET_PATH.unlink()

        super().server_bind()

        os.chmod(SOCKET_PATH, 0o660)

        self.server_name = "jitsi-invite"
        self.server_port = 0


class RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "JitsiInvite"
    sys_version = ""

    def remote_ip(self) -> str:
        forwarded = self.headers.get(
            "X-Real-IP",
            "",
        ).strip()

        return forwarded or "unix-socket"

    def remote_user(self) -> str:
        return self.headers.get(
            "X-Remote-User",
            "",
        ).strip()[:128]

    def address_string(self) -> str:
        return self.remote_ip()

    def log_message(
        self,
        fmt: str,
        *args: object,
    ) -> None:
        LOG.info(
            "%s %s",
            self.address_string(),
            fmt % args,
        )

    def send_body(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(status)

        self.send_header(
            "Content-Type",
            content_type,
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.send_header(
            "Cache-Control",
            "no-store",
        )
        self.send_header(
            "Pragma",
            "no-cache",
        )
        self.send_header(
            "X-Content-Type-Options",
            "nosniff",
        )
        self.send_header(
            "Referrer-Policy",
            "no-referrer",
        )
        self.send_header(
            "X-Frame-Options",
            "DENY",
        )
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; "
            "style-src 'unsafe-inline'; "
            "form-action 'self'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        self.send_header(
            "Connection",
            "close",
        )

        for name, value in headers or []:
            self.send_header(name, value)

        self.end_headers()

        if self.command != "HEAD":
            self.wfile.write(body)

    def send_html(
        self,
        status: int,
        document: str,
        *,
        headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_body(
            status,
            document.encode("utf-8"),
            "text/html; charset=utf-8",
            headers=headers,
        )

    def send_text(
        self,
        status: int,
        text: str,
    ) -> None:
        self.send_body(
            status,
            text.encode("utf-8"),
            "text/plain; charset=utf-8",
        )

    def redirect(
        self,
        location: str,
        status: int = 303,
    ) -> None:
        self.send_body(
            status,
            b"",
            "text/plain; charset=utf-8",
            headers=[
                ("Location", location),
            ],
        )

    def read_form(self) -> dict[str, str]:
        raw_length = self.headers.get(
            "Content-Length",
            "",
        )

        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError(
                "Invalid Content-Length"
            ) from error

        if not 0 <= length <= MAX_FORM_BYTES:
            raise ValueError("Form is too large")

        raw_body = self.rfile.read(length)

        try:
            decoded = raw_body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(
                "Form must use UTF-8"
            ) from error

        parsed = parse_qs(
            decoded,
            keep_blank_values=True,
            strict_parsing=False,
        )

        return {
            key: values[-1]
            for key, values in parsed.items()
        }

    def get_cookie(self, name: str) -> str:
        raw_cookie = self.headers.get("Cookie", "")

        parsed = cookies.SimpleCookie()

        try:
            parsed.load(raw_cookie)
        except cookies.CookieError:
            return ""

        morsel = parsed.get(name)

        return morsel.value if morsel else ""

    def csrf_headers(
        self,
        token: str,
    ) -> list[tuple[str, str]]:
        return [
            (
                "Set-Cookie",
                "jitsi_invite_csrf="
                + token
                + "; Path=/; Max-Age=3600; "
                + "Secure; HttpOnly; SameSite=Strict",
            )
        ]

    def csrf_valid(
        self,
        form: dict[str, str],
        subject: str,
    ) -> bool:
        form_token = form.get("csrf_token", "")
        cookie_token = self.get_cookie(
            "jitsi_invite_csrf"
        )

        if not form_token or not cookie_token:
            return False

        if not hmac.compare_digest(
            form_token,
            cookie_token,
        ):
            return False

        return csrf_verify(
            form_token,
            subject,
        )

    def require_admin(self) -> str | None:
        user = self.remote_user()

        if not user:
            self.send_html(
                403,
                layout(
                    "Доступ запрещён",
                    """
                    <section>
                      <h1>Доступ запрещён</h1>
                      <p>
                        Не получена идентификация организатора.
                      </p>
                    </section>
                    """,
                ),
            )
            return None

        return user

    def show_admin_index(self) -> None:
        user = self.require_admin()

        if user is None:
            return

        with database() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM invitations
                WHERE created_by = ?
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (user,),
            ).fetchall()

        table_rows: list[str] = []

        for row in rows:
            state = invitation_state(row)

            state_text = {
                "active": "активна",
                "expired": "истекла",
                "revoked": "отозвана",
            }[state]

            table_rows.append(
                "<tr>"
                f"<td><a href=\"/invite/{html.escape(row['code'])}\">"
                f"{html.escape(row['title'])}</a></td>"
                f"<td>{html.escape(state_text)}</td>"
                f"<td>{html.escape(expiry_text(row['expires_at']))}</td>"
                "</tr>"
            )

        if table_rows:
            listing = (
                "<table>"
                "<thead><tr>"
                "<th>Конференция</th>"
                "<th>Состояние</th>"
                "<th>Действует до</th>"
                "</tr></thead>"
                "<tbody>"
                + "".join(table_rows)
                + "</tbody></table>"
            )
        else:
            listing = (
                "<p class=\"muted\">"
                "Созданных конференций пока нет."
                "</p>"
            )

        content = f"""
        <section>
          <h1>Jitsi Invite</h1>
          <p>
            Организатор:
            <strong>{html.escape(user)}</strong>
          </p>
          <a class="button" href="/invite/new">
            Создать конференцию
          </a>
        </section>

        <section>
          <h2>Последние конференции</h2>
          {listing}
        </section>
        """

        self.send_html(
            200,
            layout(
                "Jitsi Invite",
                content,
                navigation=True,
            ),
        )

    def show_create_form(
        self,
        *,
        error: str = "",
    ) -> None:
        user = self.require_admin()

        if user is None:
            return

        csrf_token = csrf_create(
            f"admin-create:{user}"
        )

        error_html = ""

        if error:
            error_html = (
                f"<p class=\"error\">{html.escape(error)}</p>"
            )

        content = f"""
        <section>
          <h1>Новая конференция</h1>
          {error_html}

          <form method="post" action="/invite/create">
            <input
              type="hidden"
              name="csrf_token"
              value="{html.escape(csrf_token, quote=True)}"
            >

            <label for="title">
              Название конференции
            </label>
            <input
              id="title"
              name="title"
              type="text"
              minlength="1"
              maxlength="100"
              required
              autocomplete="off"
            >

            <label for="organizer_name">
              Имя модератора в конференции
            </label>
            <input
              id="organizer_name"
              name="organizer_name"
              type="text"
              minlength="1"
              maxlength="80"
              required
              value="{html.escape(user, quote=True)}"
            >

            <label for="duration_hours">
              Срок действия приглашения
            </label>
            <select
              id="duration_hours"
              name="duration_hours"
            >
              <option value="1">1 час</option>
              <option value="2">2 часа</option>
              <option value="4" selected>4 часа</option>
              <option value="8">8 часов</option>
              <option value="12">12 часов</option>
              <option value="24">24 часа</option>
              <option value="permanent">Постоянная</option>
            </select>

            <button type="submit">
              Создать конференцию
            </button>
          </form>
        </section>
        """

        self.send_html(
            200,
            layout(
                "Новая конференция",
                content,
                navigation=True,
            ),
            headers=self.csrf_headers(csrf_token),
        )

    def create_invitation_request(self) -> None:
        user = self.require_admin()

        if user is None:
            return

        try:
            form = self.read_form()

            if not self.csrf_valid(
                form,
                f"admin-create:{user}",
            ):
                self.send_text(
                    403,
                    "invalid csrf token\n",
                )
                return

            title = clean_text(
                form.get("title", ""),
                minimum=1,
                maximum=100,
                field_name="Название",
            )

            organizer_name = clean_text(
                form.get("organizer_name", ""),
                minimum=1,
                maximum=80,
                field_name="Имя модератора",
            )

            duration_value = form.get(
                "duration_hours",
                "",
            )

            if duration_value == "permanent":
                duration_hours = None
            else:
                duration_hours = int(duration_value)

                if duration_hours not in ALLOWED_DURATIONS:
                    raise ValueError(
                        "Недопустимый срок действия"
                    )

            invitation = create_invitation(
                title=title,
                organizer_name=organizer_name,
                created_by=user,
                duration_hours=duration_hours,
            )

            audit_event(
                "invitation-created",
                invitation=invitation,
                actor=user,
                remote_ip=self.remote_ip(),
            )

            self.redirect(
                f"/invite/{invitation['code']}"
            )

        except (ValueError, RuntimeError) as error:
            self.show_create_form(
                error=str(error),
            )

    def show_invitation(
        self,
        code: str,
    ) -> None:
        user = self.require_admin()

        if user is None:
            return

        invitation = get_invitation(code)

        if (
            invitation is None
            or invitation["created_by"] != user
        ):
            self.send_text(404, "not found\n")
            return

        state = invitation_state(invitation)

        guest_url = (
            f"{BASE_URL}/join/{invitation['code']}"
        )

        actions = ""

        if state == "active":
            csrf_token = csrf_create(
                f"admin-revoke:{user}:{code}"
            )

            actions = f"""
            <p>
              <a
                class="button"
                href="/invite/{html.escape(code)}/moderator"
              >
                Войти как модератор
              </a>
            </p>

            <form
              method="post"
              action="/invite/{html.escape(code)}/revoke"
            >
              <input
                type="hidden"
                name="csrf_token"
                value="{html.escape(csrf_token, quote=True)}"
              >
              <button
                type="submit"
                class="danger"
              >
                Отозвать приглашение
              </button>
            </form>
            """

            csrf_headers = self.csrf_headers(
                csrf_token
            )

        else:
            csrf_headers = []

        content = f"""
        <section>
          <h1>{html.escape(invitation['title'])}</h1>

          <p>
            Состояние:
            <strong>{html.escape(state)}</strong>
          </p>

          <p>
            Комната:
            <code>{html.escape(invitation['room'])}</code>
          </p>

          <p>
            Действует до:
            <strong>
              {html.escape(expiry_text(invitation['expires_at']))}
            </strong>
          </p>
        </section>

        <section>
          <h2>Ссылка для участников</h2>

          <p>
            Эту одну ссылку можно отправить всем гостям.
            Каждый гость введёт своё имя и получит
            отдельный короткоживущий JWT.
          </p>

          <div class="link-box">
            {html.escape(guest_url)}
          </div>
        </section>

        <section>
          <h2>Управление</h2>
          {actions or '<p class="muted">Приглашение неактивно.</p>'}
        </section>
        """

        self.send_html(
            200,
            layout(
                invitation["title"],
                content,
                navigation=True,
            ),
            headers=csrf_headers,
        )

    def moderator_redirect(
        self,
        code: str,
    ) -> None:
        user = self.require_admin()

        if user is None:
            return

        invitation = get_invitation(code)

        if (
            invitation is None
            or invitation["created_by"] != user
        ):
            self.send_text(404, "not found\n")
            return

        if invitation_state(invitation) != "active":
            self.send_text(
                410,
                "invitation is not active\n",
            )
            return

        expires_at = invitation["expires_at"]

        if expires_at is None:
            ttl = MODERATOR_TOKEN_TTL
        else:
            remaining = expires_at - int(time.time())

            if remaining < 60:
                self.send_text(
                    410,
                    "invitation expires too soon\n",
                )
                return

            ttl = min(
                remaining,
                MODERATOR_TOKEN_TTL,
            )

        audit_event(
            "moderator-token-issued",
            invitation=invitation,
            actor=user,
            remote_ip=self.remote_ip(),
        )

        self.redirect(
            make_jitsi_url(
                room=invitation["room"],
                name=invitation["organizer_name"],
                moderator=True,
                ttl=ttl,
            )
        )

    def revoke_invitation(
        self,
        code: str,
    ) -> None:
        user = self.require_admin()

        if user is None:
            return

        invitation = get_invitation(code)

        if (
            invitation is None
            or invitation["created_by"] != user
        ):
            self.send_text(404, "not found\n")
            return

        try:
            form = self.read_form()
        except ValueError:
            self.send_text(400, "invalid form\n")
            return

        if not self.csrf_valid(
            form,
            f"admin-revoke:{user}:{code}",
        ):
            self.send_text(
                403,
                "invalid csrf token\n",
            )
            return

        with database() as connection:
            connection.execute(
                """
                UPDATE invitations
                SET revoked_at = ?
                WHERE code = ?
                  AND created_by = ?
                  AND revoked_at IS NULL
                """,
                (
                    int(time.time()),
                    code,
                    user,
                ),
            )

        updated = get_invitation(code)

        audit_event(
            "invitation-revoked",
            invitation=updated,
            actor=user,
            remote_ip=self.remote_ip(),
        )

        self.redirect(f"/invite/{code}")

    def show_join_form(
        self,
        code: str,
        *,
        error: str = "",
    ) -> None:
        invitation = get_invitation(code)

        if invitation is None:
            self.send_text(404, "not found\n")
            return

        state = invitation_state(invitation)

        if state != "active":
            message = (
                "Приглашение отозвано."
                if state == "revoked"
                else "Срок действия приглашения истёк."
            )

            self.send_html(
                410,
                layout(
                    "Приглашение недействительно",
                    f"""
                    <section>
                      <h1>Приглашение недействительно</h1>
                      <p>{html.escape(message)}</p>
                    </section>
                    """,
                ),
            )
            return

        csrf_token = csrf_create(
            f"guest-join:{code}"
        )

        error_html = ""

        if error:
            error_html = (
                f"<p class=\"error\">{html.escape(error)}</p>"
            )

        content = f"""
        <section>
          <h1>{html.escape(invitation['title'])}</h1>

          <p>
            Организатор:
            <strong>
              {html.escape(invitation['organizer_name'])}
            </strong>
          </p>

          <p>
            Введите имя, которое будет показано
            другим участникам конференции.
          </p>

          {error_html}

          <form
            method="post"
            action="/join/{html.escape(code)}"
          >
            <input
              type="hidden"
              name="csrf_token"
              value="{html.escape(csrf_token, quote=True)}"
            >

            <label for="display_name">
              Ваше имя
            </label>
            <input
              id="display_name"
              name="display_name"
              type="text"
              minlength="1"
              maxlength="80"
              required
              autofocus
              autocomplete="name"
            >

            <button type="submit">
              Войти в конференцию
            </button>
          </form>
        </section>
        """

        self.send_html(
            200,
            layout(
                invitation["title"],
                content,
            ),
            headers=self.csrf_headers(csrf_token),
        )

    def guest_join(
        self,
        code: str,
    ) -> None:
        invitation = get_invitation(code)

        if invitation is None:
            self.send_text(404, "not found\n")
            return

        if invitation_state(invitation) != "active":
            self.send_text(
                410,
                "invitation is not active\n",
            )
            return

        if not rate_limit_ok(
            self.remote_ip(),
            code,
        ):
            self.send_body(
                429,
                b"too many requests\n",
                "text/plain; charset=utf-8",
                headers=[
                    ("Retry-After", "60"),
                ],
            )
            return

        try:
            form = self.read_form()

            if not self.csrf_valid(
                form,
                f"guest-join:{code}",
            ):
                self.send_text(
                    403,
                    "invalid csrf token\n",
                )
                return

            display_name = clean_text(
                form.get("display_name", ""),
                minimum=1,
                maximum=80,
                field_name="Имя",
            )

        except ValueError as error:
            self.show_join_form(
                code,
                error=str(error),
            )
            return

        expires_at = invitation["expires_at"]

        if expires_at is None:
            ttl = GUEST_TOKEN_TTL
        else:
            remaining = expires_at - int(time.time())

            if remaining < 60:
                self.send_text(
                    410,
                    "invitation expires too soon\n",
                )
                return

            ttl = min(
                remaining,
                GUEST_TOKEN_TTL,
            )

        audit_event(
            "guest-token-issued",
            invitation=invitation,
            actor=display_name,
            remote_ip=self.remote_ip(),
        )

        self.redirect(
            make_jitsi_url(
                room=invitation["room"],
                name=display_name,
                moderator=False,
                ttl=ttl,
            )
        )

    def do_GET(self) -> None:
        path = urlsplit(self.path).path

        if path == "/healthz":
            self.send_text(200, "ok\n")
            return

        if path == "/invite":
            self.redirect("/invite/", 308)
            return

        if path == "/invite/":
            self.show_admin_index()
            return

        if path == "/invite/new":
            self.show_create_form()
            return

        match = re.fullmatch(
            r"/invite/([A-Za-z0-9_-]{16,64})",
            path,
        )

        if match:
            self.show_invitation(match.group(1))
            return

        match = re.fullmatch(
            r"/invite/([A-Za-z0-9_-]{16,64})/moderator",
            path,
        )

        if match:
            self.moderator_redirect(match.group(1))
            return

        match = re.fullmatch(
            r"/join/([A-Za-z0-9_-]{16,64})",
            path,
        )

        if match:
            self.show_join_form(match.group(1))
            return

        self.send_text(404, "not found\n")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path

        if path == "/invite/create":
            self.create_invitation_request()
            return

        match = re.fullmatch(
            r"/invite/([A-Za-z0-9_-]{16,64})/revoke",
            path,
        )

        if match:
            self.revoke_invitation(match.group(1))
            return

        match = re.fullmatch(
            r"/join/([A-Za-z0-9_-]{16,64})",
            path,
        )

        if match:
            self.guest_join(match.group(1))
            return

        self.send_text(404, "not found\n")


def remove_socket() -> None:
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass


def main() -> None:
    os.umask(0o007)

    initialise_database()

    SOCKET_PATH.parent.mkdir(
        mode=0o750,
        parents=True,
        exist_ok=True,
    )

    server = ThreadingUnixHTTPServer(
        str(SOCKET_PATH),
        RequestHandler,
    )

    def stop_service(
        signum: int,
        frame: object,
    ) -> None:
        del signum, frame

        LOG.info("Stopping")

        threading.Thread(
            target=server.shutdown,
            daemon=True,
        ).start()

    signal.signal(signal.SIGTERM, stop_service)
    signal.signal(signal.SIGINT, stop_service)

    LOG.info(
        "Listening on Unix socket %s",
        SOCKET_PATH,
    )

    try:
        server.serve_forever(
            poll_interval=0.5,
        )
    finally:
        server.server_close()
        remove_socket()


if __name__ == "__main__":
    main()
