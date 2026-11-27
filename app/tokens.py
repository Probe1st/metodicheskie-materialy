"""API token management and authentication for AI agents."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .database import connect, normalize_name_key


class TokenError(ValueError):
    """Base exception for API token operations."""


class TokenValidationError(TokenError):
    """Raised when token parameters fail validation."""


class TokenConflictError(TokenError):
    """Raised when an active token with the same name already exists."""


class TokenNotFoundError(TokenError):
    """Raised when an API token ID is not found."""


@dataclass(frozen=True, slots=True)
class ApiToken:
    id: UUID
    name: str
    token_prefix: str
    scopes: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class ApiActor:
    token_id: UUID | None
    name: str
    scopes: tuple[str, ...]
    source: str  # "db" | "env"

    @property
    def actor_label(self) -> str:
        return f"agent:{self.name}"


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def hash_token(raw_token: str) -> str:
    """Return SHA-256 hex digest of a raw API token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def parse_env_tokens(
    single_token: str | None = None,
    multi_tokens: str | Sequence[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Parse static API tokens from environment/config values.

    Supports:
    - single_token: raw token string (assigned name 'env-agent')
    - multi_tokens: comma-separated 'name:token' or 'token' entries
    """
    parsed: list[tuple[str, str]] = []
    if isinstance(single_token, str) and single_token.strip():
        parsed.append(("env-agent", single_token.strip()))

    entries: list[str] = []
    if isinstance(multi_tokens, str):
        entries = [part.strip() for part in multi_tokens.split(",") if part.strip()]
    elif isinstance(multi_tokens, Sequence):
        entries = [str(part).strip() for part in multi_tokens if str(part).strip()]

    for idx, entry in enumerate(entries, start=1):
        if ":" in entry:
            raw_name, raw_secret = entry.split(":", 1)
            name = raw_name.strip()
            secret = raw_secret.strip()
            if name and secret:
                parsed.append((name, secret))
                continue
        parsed.append((f"env-agent-{idx}", entry))

    return tuple(parsed)


VALID_TOKEN_SCOPES: frozenset[str] = frozenset({"agent", "read", "grade"})
LAST_USED_UPDATE_INTERVAL_SECONDS = 60


def normalize_token_scopes(scopes: str | Sequence[str] | None) -> str:
    """Validate and normalize comma-separated API token scopes."""
    if scopes is None:
        return "agent"
    if isinstance(scopes, str):
        raw_items = scopes.split(",")
    elif isinstance(scopes, Sequence):
        raw_items = [str(item) for item in scopes]
    else:
        raise TokenValidationError("scopes must be a string or sequence")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        part = item.strip().casefold()
        if not part:
            continue
        if part not in VALID_TOKEN_SCOPES:
            allowed = ", ".join(sorted(VALID_TOKEN_SCOPES))
            raise TokenValidationError(f"Invalid scope {part!r}. Allowed: {allowed}")
        if part not in seen:
            seen.add(part)
            normalized.append(part)

    if not normalized:
        raise TokenValidationError("Token must have at least one scope")
    return ",".join(normalized)


class TokenStore:
    """Persistence and authentication for AI agent API tokens."""

    def __init__(
        self,
        db_path: Path | str,
        env_tokens: Sequence[tuple[str, str]] = (),
        *,
        last_used_interval_seconds: int = LAST_USED_UPDATE_INTERVAL_SECONDS,
    ):
        self.db_path = Path(db_path).resolve()
        self.env_tokens = tuple(
            (str(name).strip(), str(secret).strip())
            for name, secret in env_tokens
            if str(name).strip() and str(secret).strip()
        )
        self.last_used_interval_seconds = max(0, int(last_used_interval_seconds))

    @staticmethod
    def _row_to_token(row: sqlite3.Row) -> ApiToken:
        return ApiToken(
            id=UUID(row["id"]),
            name=row["name"],
            token_prefix=row["token_prefix"],
            scopes=row["scopes"],
            created_at=_parse_iso_utc(row["created_at"]) or datetime.now(timezone.utc),
            last_used_at=_parse_iso_utc(row["last_used_at"]),
            revoked_at=_parse_iso_utc(row["revoked_at"]),
        )

    def create_token(self, name: str, scopes: str = "agent") -> tuple[ApiToken, str]:
        if not isinstance(name, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TokenValidationError("Token name must be a string")
        cleaned_name = unicodedata.normalize("NFC", name.strip())
        if not cleaned_name:
            raise TokenValidationError("Token name cannot be empty")
        if len(cleaned_name) > 100:
            raise TokenValidationError("Token name must be at most 100 characters")

        try:
            name_key = normalize_name_key(cleaned_name)
        except (ValueError, TypeError) as err:
            raise TokenValidationError(str(err)) from err

        cleaned_scopes = normalize_token_scopes(scopes)
        token_id = uuid4()
        raw_token = f"ag_{secrets.token_urlsafe(32)}"
        token_prefix = raw_token[:12]
        token_digest = hash_token(raw_token)
        now_iso = datetime.now(timezone.utc).isoformat()

        with connect(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO api_tokens (
                        id, name, name_key, token_prefix, token_hash, scopes,
                        created_at, last_used_at, revoked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL);
                    """,
                    (
                        str(token_id),
                        cleaned_name,
                        name_key,
                        token_prefix,
                        token_digest,
                        cleaned_scopes,
                        now_iso,
                    ),
                )
            except sqlite3.IntegrityError as err:
                raise TokenConflictError(
                    f"Active token named '{cleaned_name}' already exists"
                ) from err

        token_obj = ApiToken(
            id=token_id,
            name=cleaned_name,
            token_prefix=token_prefix,
            scopes=cleaned_scopes,
            created_at=_parse_iso_utc(now_iso) or datetime.now(timezone.utc),
            last_used_at=None,
            revoked_at=None,
        )
        return token_obj, raw_token

    def list_tokens(self, include_revoked: bool = True) -> list[ApiToken]:
        sql = "SELECT id, name, token_prefix, scopes, created_at, last_used_at, revoked_at FROM api_tokens"
        if not include_revoked:
            sql += " WHERE revoked_at IS NULL"
        sql += " ORDER BY (revoked_at IS NOT NULL) ASC, created_at DESC, id DESC;"

        with connect(self.db_path) as conn:
            rows = conn.execute(sql).fetchall()
            return [self._row_to_token(r) for r in rows]

    def get_token(self, token_id: UUID | str) -> ApiToken | None:
        uid = UUID(str(token_id))
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, name, token_prefix, scopes, created_at, last_used_at, revoked_at
                FROM api_tokens WHERE id = ?;
                """,
                (str(uid),),
            ).fetchone()
            return self._row_to_token(row) if row else None

    def revoke_token(self, token_id: UUID | str) -> ApiToken:
        uid = UUID(str(token_id))
        now_iso = datetime.now(timezone.utc).isoformat()
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, name, token_prefix, scopes, created_at, last_used_at, revoked_at
                FROM api_tokens WHERE id = ?;
                """,
                (str(uid),),
            ).fetchone()
            if not row:
                raise TokenNotFoundError(f"API token {uid} not found")
            if row["revoked_at"] is None:
                conn.execute(
                    "UPDATE api_tokens SET revoked_at = ? WHERE id = ?;",
                    (now_iso, str(uid)),
                )
                row = conn.execute(
                    """
                    SELECT id, name, token_prefix, scopes, created_at, last_used_at, revoked_at
                    FROM api_tokens WHERE id = ?;
                    """,
                    (str(uid),),
                ).fetchone()
            return self._row_to_token(row)

    def authenticate(self, raw_token: str) -> ApiActor | None:
        if not isinstance(raw_token, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            return None
        candidate = raw_token.strip()
        if not candidate:
            return None

        digest = hash_token(candidate)
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, name, token_hash, scopes, last_used_at
                FROM api_tokens
                WHERE token_hash = ? AND revoked_at IS NULL;
                """,
                (digest,),
            ).fetchone()
            if row and hmac.compare_digest(row["token_hash"], digest):
                prev_used = _parse_iso_utc(row["last_used_at"])
                should_update = (
                    prev_used is None
                    or (now_dt - prev_used).total_seconds() >= self.last_used_interval_seconds
                )
                if should_update:
                    conn.execute(
                        "UPDATE api_tokens SET last_used_at = ? WHERE id = ?;",
                        (now_iso, row["id"]),
                    )
                scopes = tuple(s.strip() for s in str(row["scopes"]).split(",") if s.strip()) or ("agent",)
                return ApiActor(
                    token_id=UUID(row["id"]),
                    name=row["name"],
                    scopes=scopes,
                    source="db",
                )

        matched_env: ApiActor | None = None
        for env_name, env_secret in self.env_tokens:
            if hmac.compare_digest(candidate, env_secret) and matched_env is None:
                matched_env = ApiActor(
                    token_id=None,
                    name=env_name,
                    scopes=("agent",),
                    source="env",
                )
        return matched_env
