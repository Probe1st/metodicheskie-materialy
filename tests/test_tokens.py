"""Tests for AI agent API tokens store and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.database import initialize
from app.tokens import (
    TokenConflictError,
    TokenNotFoundError,
    TokenStore,
    TokenValidationError,
    hash_token,
    parse_env_tokens,
)
from tools.tokens import main as tokens_cli_main


def test_token_lifecycle_and_authentication(tmp_path: Path) -> None:
    db_path = tmp_path / "tokens.sqlite3"
    initialize(db_path)
    store = TokenStore(db_path)

    # Validation
    with pytest.raises(TokenValidationError):
        store.create_token("   ")

    tok, raw = store.create_token("Grader Agent")
    assert raw.startswith("ag_")
    assert tok.token_prefix == raw[:12]
    assert tok.revoked_at is None

    # Duplicate active name (case/whitespace insensitive) rejected
    with pytest.raises(TokenConflictError):
        store.create_token("  grader agent ")

    # Authenticate valid token
    actor = store.authenticate(raw)
    assert actor is not None
    assert actor.name == "Grader Agent"
    assert actor.source == "db"
    assert actor.actor_label == "agent:Grader Agent"

    # last_used_at updated
    listed = store.list_tokens()
    assert len(listed) == 1
    assert listed[0].last_used_at is not None

    # Invalid token rejected
    assert store.authenticate("ag_wrong_token") is None
    assert store.authenticate("") is None

    # Revoke token
    revoked = store.revoke_token(tok.id)
    assert revoked.revoked_at is not None
    assert store.authenticate(raw) is None

    # Recreating same name after revocation is permitted
    tok2, raw2 = store.create_token("Grader Agent")
    assert tok2.id != tok.id
    assert raw2 != raw
    assert store.authenticate(raw2) is not None

    # Unknown revoke
    with pytest.raises(TokenNotFoundError):
        store.revoke_token("00000000-0000-0000-0000-000000000000")

    # Plaintext is never stored in DB
    assert hash_token(raw2) != raw2


def test_env_tokens_parsing_and_auth(tmp_path: Path) -> None:
    db_path = tmp_path / "env_tokens.sqlite3"
    initialize(db_path)

    env_tokens = parse_env_tokens(
        single_token="static-secret-1",
        multi_tokens="bot-alpha:secret-a, secret-b",
    )
    store = TokenStore(db_path, env_tokens=env_tokens)

    a1 = store.authenticate("static-secret-1")
    assert a1 is not None
    assert a1.source == "env"
    assert a1.name == "env-agent"

    a2 = store.authenticate("secret-a")
    assert a2 is not None
    assert a2.name == "bot-alpha"

    a3 = store.authenticate("secret-b")
    assert a3 is not None
    assert a3.name == "env-agent-2"


def test_tokens_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "cli_tokens.sqlite3"
    initialize(db_path)

    assert tokens_cli_main(["create", "--database", str(db_path), "--name", "cli-bot"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["name"] == "cli-bot"
    assert created["token"].startswith("ag_")

    assert tokens_cli_main(["list", "--database", str(db_path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1
    assert "token" not in listed[0]

    assert tokens_cli_main(["revoke", "--database", str(db_path), "--id", created["id"]]) == 0
    revoked = json.loads(capsys.readouterr().out)
    assert revoked["revoked_at"] is not None


def test_token_scopes_validation_and_last_used_throttling(tmp_path: Path) -> None:
    db_path = tmp_path / "throttle.sqlite3"
    initialize(db_path)
    store = TokenStore(db_path, last_used_interval_seconds=60)

    with pytest.raises(TokenValidationError):
        store.create_token("bad-scope", scopes="superuser")
    with pytest.raises(TokenValidationError):
        store.create_token("empty-scope", scopes=" , ")

    tok, raw = store.create_token("reader", scopes="READ, grade")
    assert tok.scopes == "read,grade"

    # First auth sets last_used_at
    assert store.authenticate(raw) is not None
    t1 = store.get_token(tok.id).last_used_at
    assert t1 is not None

    # Second auth within 60s does not rewrite last_used_at
    assert store.authenticate(raw) is not None
    t2 = store.get_token(tok.id).last_used_at
    assert t2 == t1

