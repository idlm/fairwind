"""Game Profile 远程更新：ed25519 签名校验、防回滚、原子替换与 LKG。

信任根由外部注入（`FAIRWIND_PROFILE_PUBKEY`，Base64 编码的 ed25519 公钥）；没有公钥时
拒绝一切远程规则，仓库不内置任何密钥。
"""

import base64
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from fairwind.domain import Capabilities
from fairwind.errors import SafeError
from fairwind.parser import safe_json
from fairwind.profiles import MAX_PROFILE_BYTES, GameProfile, parse_profiles
from fairwind.routing import RouteRule, generate_rules
from fairwind.security import canonical_json, private_directory

PUBLIC_KEY_VARIABLE = "FAIRWIND_PROFILE_PUBKEY"
ENVELOPE_FIELDS = ("document", "signature")
REGISTRY_NAME = "game_profiles.json"
PREVIOUS_NAME = "game_profiles.previous.json"


def load_public_key(encoded: str | None = None) -> Ed25519PublicKey:
    """从显式参数或环境变量读取 ed25519 公钥；缺失或非法一律拒绝。"""
    value = encoded if encoded is not None else os.environ.get(PUBLIC_KEY_VARIABLE)
    if not value:
        raise SafeError("PROFILE_PUBKEY_REQUIRED")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError):
        raise SafeError("PROFILE_PUBKEY_REQUIRED") from None


def _decoded_signature(value: object) -> bytes:
    if not isinstance(value, str):
        raise SafeError("PARSE_FAILED")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise SafeError("PROFILE_SIGNATURE_INVALID") from None


@dataclass(frozen=True)
class VerifiedEnvelope:
    version: int
    profiles: list[GameProfile]
    document: bytes


def verify_envelope(data: bytes, public_key: Ed25519PublicKey) -> VerifiedEnvelope:
    """校验信封：大小限额 → 严格结构 → ed25519 签名 → 文档 schema。

    签名覆盖文档的规范 JSON（排序键、紧凑分隔），因此文档字节的等价表示不影响校验结果。
    """
    if len(data) > MAX_PROFILE_BYTES:
        raise SafeError("UPDATE_LIMIT")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise SafeError("PARSE_FAILED") from None
    if not text.strip():
        raise SafeError("PARSE_FAILED")
    envelope = safe_json(text)
    if not isinstance(envelope, dict) or set(envelope) != set(ENVELOPE_FIELDS):
        raise SafeError("PARSE_FAILED")
    document = canonical_json(envelope["document"])
    try:
        public_key.verify(_decoded_signature(envelope["signature"]), document)
    except InvalidSignature:
        raise SafeError("PROFILE_SIGNATURE_INVALID") from None
    version, profiles = parse_profiles(document)
    return VerifiedEnvelope(version, profiles, document)


class ProfileRegistry:
    """文件型注册表：只有在完整校验通过后才原子替换，并保留上一个有效版本（LKG）。"""

    def __init__(self, root: Path):
        self.root = root
        self.path = root / REGISTRY_NAME
        self.previous_path = root / PREVIOUS_NAME

    def current(self) -> tuple[int, list[GameProfile]]:
        if not self.path.is_file():
            return 0, []
        return parse_profiles(self.path.read_bytes())

    def previous(self) -> tuple[int, list[GameProfile]]:
        if not self.previous_path.is_file():
            raise SafeError("NO_PREVIOUS_REGISTRY")
        return parse_profiles(self.previous_path.read_bytes())

    def apply(
        self,
        data: bytes,
        public_key: Ed25519PublicKey,
        capabilities: Capabilities | None = None,
        platform: str | None = None,
    ) -> dict:
        verified = verify_envelope(data, public_key)
        current_version, _ = self.current()
        if verified.version <= current_version:
            raise SafeError("ROLLBACK_REJECTED")
        rules: list[RouteRule] = []
        if capabilities is not None:
            rules = generate_rules(verified.profiles, capabilities, platform=platform)
        self._write(verified.document)
        return {
            "version": verified.version,
            "previous_version": current_version,
            "profiles": len(verified.profiles),
            "rules": len(rules),
        }

    def restore_previous(self) -> dict:
        """用 LKG 覆盖当前注册表；被替换的版本随后成为新的 LKG（相当于互换）。"""
        version, profiles = self.previous()
        self._write(self.previous_path.read_bytes())
        return {"version": version, "profiles": len(profiles)}

    def _write(self, document: bytes) -> None:
        private_directory(self.root)
        if self.path.exists():
            shutil.copyfile(self.path, self.previous_path)
        temporary = self.root / ("." + secrets.token_hex(16))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(document)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
