"""Game Profile 远程更新的签名校验、防回滚与 LKG 测试。"""

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from fairwind.domain import Capabilities
from fairwind.errors import SafeError
from fairwind.profile_update import (
    PUBLIC_KEY_VARIABLE,
    ProfileRegistry,
    load_public_key,
    verify_envelope,
)
from fairwind.profiles import MAX_PROFILE_BYTES
from fairwind.security import canonical_json

pytestmark = pytest.mark.security

ALL_CAPABILITIES = Capabilities(
    protocols=frozenset({"socks"}), tun=True, udp=True, ipv6=True, process_rules=True
)


def profile(**overrides):
    base = {
        "id": "steam",
        "name": "Steam",
        "platform": ["windows"],
        "process_names": ["steam.exe"],
        "domains": ["steam.example"],
        "cidrs": [],
        "ports": [],
        "protocols": [],
    }
    base.update(overrides)
    return base


def document(version: int = 1, entries=None):
    return {
        "schema_version": 1,
        "version": version,
        "profiles": [profile()] if entries is None else entries,
    }


def encoded_key(key: Ed25519PrivateKey) -> str:
    return base64.urlsafe_b64encode(key.public_key().public_bytes_raw()).decode()


def envelope(key: Ed25519PrivateKey, payload: dict) -> bytes:
    signature = base64.urlsafe_b64encode(key.sign(canonical_json(payload))).decode()
    return canonical_json({"document": payload, "signature": signature})


def test_public_key_required_and_must_be_valid(monkeypatch):
    monkeypatch.delenv(PUBLIC_KEY_VARIABLE, raising=False)
    with pytest.raises(SafeError, match="PROFILE_PUBKEY_REQUIRED"):
        load_public_key()
    with pytest.raises(SafeError, match="PROFILE_PUBKEY_REQUIRED"):
        load_public_key("not-base64!!")
    with pytest.raises(SafeError, match="PROFILE_PUBKEY_REQUIRED"):
        load_public_key(base64.urlsafe_b64encode(b"too-short").decode())


def test_environment_key_is_used(monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setenv(PUBLIC_KEY_VARIABLE, encoded_key(key))
    verified = verify_envelope(envelope(key, document(3)), load_public_key())
    assert verified.version == 3 and len(verified.profiles) == 1
    assert b'"version":3' in verified.document


def test_apply_records_lkg_and_generated_rules(tmp_path):
    key = Ed25519PrivateKey.generate()
    registry = ProfileRegistry(tmp_path / "profiles")
    assert registry.current() == (0, [])
    with pytest.raises(SafeError, match="NO_PREVIOUS_REGISTRY"):
        registry.previous()
    first = registry.apply(
        envelope(key, document(1)), key.public_key(), ALL_CAPABILITIES, "windows"
    )
    assert first == {"version": 1, "previous_version": 0, "profiles": 1, "rules": 2}
    second = registry.apply(
        envelope(key, document(2)), key.public_key(), ALL_CAPABILITIES, "windows"
    )
    assert second["version"] == 2 and second["previous_version"] == 1
    assert registry.current()[0] == 2 and registry.previous()[0] == 1
    assert registry.restore_previous() == {"version": 1, "profiles": 1}
    assert registry.current()[0] == 1
    assert registry.previous()[0] == 2


def test_signature_tampering_rejected_and_registry_untouched(tmp_path):
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    registry = ProfileRegistry(tmp_path / "profiles")
    registry.apply(envelope(key, document(1)), key.public_key())
    with pytest.raises(SafeError, match="PROFILE_SIGNATURE_INVALID"):
        registry.apply(envelope(other, document(2)), key.public_key())
    tampered = json.loads(envelope(key, document(2)))
    tampered["document"]["version"] = 99
    with pytest.raises(SafeError, match="PROFILE_SIGNATURE_INVALID"):
        registry.apply(canonical_json(tampered), key.public_key())
    with pytest.raises(SafeError, match="PROFILE_SIGNATURE_INVALID"):
        registry.apply(
            canonical_json({"document": document(2), "signature": "!!!"}), key.public_key()
        )
    assert registry.current()[0] == 1


def test_rollback_rejected(tmp_path):
    key = Ed25519PrivateKey.generate()
    registry = ProfileRegistry(tmp_path / "profiles")
    registry.apply(envelope(key, document(5)), key.public_key())
    for stale in (5, 4, 1):
        with pytest.raises(SafeError, match="ROLLBACK_REJECTED"):
            registry.apply(envelope(key, document(stale)), key.public_key())
    assert registry.current()[0] == 5


def test_envelope_structure_and_size_limits():
    key = Ed25519PrivateKey.generate()
    public = key.public_key()
    with pytest.raises(SafeError, match="PARSE_FAILED"):
        verify_envelope(b"[]", public)
    with pytest.raises(SafeError, match="PARSE_FAILED"):
        verify_envelope(canonical_json({"document": document(), "extra": 1}), public)
    with pytest.raises(SafeError, match="PARSE_FAILED"):
        verify_envelope(canonical_json({"document": document(), "signature": 5}), public)
    with pytest.raises(SafeError, match="PARSE_FAILED"):
        verify_envelope(canonical_json({"signature": "x"}), public)
    with pytest.raises(SafeError, match="UPDATE_LIMIT"):
        verify_envelope(b" " * (MAX_PROFILE_BYTES + 1), public)
    with pytest.raises(SafeError, match="SCHEMA_UNSUPPORTED"):
        verify_envelope(envelope(key, {"schema_version": 9, "version": 1, "profiles": []}), public)


def test_capability_gate_blocks_before_replacement(tmp_path):
    key = Ed25519PrivateKey.generate()
    registry = ProfileRegistry(tmp_path / "profiles")
    registry.apply(envelope(key, document(1)), key.public_key(), ALL_CAPABILITIES, "windows")
    limited = Capabilities(protocols=frozenset())
    with pytest.raises(SafeError, match="UNSUPPORTED_SELECTOR"):
        registry.apply(envelope(key, document(2)), key.public_key(), limited, "windows")
    assert registry.current()[0] == 1
    with pytest.raises(SafeError, match="NO_PREVIOUS_REGISTRY"):
        registry.previous()
