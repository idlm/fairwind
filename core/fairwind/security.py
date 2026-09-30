import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from fairwind.errors import SafeError

REFERENCE_PATTERN = re.compile(r"[a-f0-9]{64}")
MAX_CIPHERTEXT_BYTES = 12 * 1024 * 1024


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def public_ip(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
            return public_ip(str(parsed.ipv4_mapped))
        return parsed.is_global and not parsed.is_multicast and not parsed.is_reserved
    except ValueError:
        return False


def canonical_host(host: str) -> str:
    if not host or len(host) > 253 or any(char.isspace() for char in host):
        raise SafeError("HOST_REJECTED")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        try:
            normalized = host.rstrip(".").encode("idna").decode().lower()
        except UnicodeError:
            raise SafeError("HOST_REJECTED") from None
        labels = normalized.split(".")
        if not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
        ):
            raise SafeError("HOST_REJECTED") from None
        return normalized


def validate_url(url: str, allow_private: bool = False) -> str:
    if len(url) > 8192 or re.search(r"[\x00-\x20\x7f\\]", url):
        raise SafeError("URL_REJECTED")
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError
        if parts.username is not None or parts.password is not None or parts.fragment:
            raise ValueError
        host = canonical_host(parts.hostname)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if not 1 <= port <= 65535 or parts.port == 0:
            raise ValueError
        if not allow_private:
            if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
                raise ValueError
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                if not public_ip(host):
                    raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port != (443 if parts.scheme == "https" else 80):
            authority += f":{port}"
        return urlunsplit((parts.scheme, authority, parts.path or "/", parts.query, ""))
    except (ValueError, SafeError):
        raise SafeError("URL_REJECTED") from None


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise SafeError("UNSAFE_STORAGE_PATH")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)


class SecretVault:
    def __init__(self, root: Path, key: bytes, max_bytes: int = 128 * 1024 * 1024):
        if len(key) != 32:
            raise SafeError("SECRET_KEY_REQUIRED")
        private_directory(root)
        self.root = root
        self.key = key
        self.max_bytes = max_bytes
        self.cipher = AESGCM(key)
        self.used_bytes = self._measure()

    def _measure(self) -> int:
        return sum(
            path.stat().st_size for path in self.root.glob("*.secret") if not path.is_symlink()
        )

    @classmethod
    def from_environment(cls, root: Path) -> "SecretVault":
        try:
            encoded = os.environ["FAIRWIND_SECRET_KEY"]
            key = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
        except (KeyError, ValueError):
            raise SafeError("SECRET_KEY_REQUIRED") from None
        return cls(root, key)

    def digest(self, value: bytes) -> str:
        return hmac.new(self.key, value, hashlib.sha256).hexdigest()

    def put(self, value: object) -> str:
        plain = canonical_json(value)
        reference = self.digest(b"vault:" + plain)
        target = self.root / (reference + ".secret")
        if target.is_symlink():
            raise SafeError("UNSAFE_STORAGE_PATH")
        if target.exists():
            self.get(reference)
            return reference
        nonce = secrets.token_bytes(12)
        encrypted = nonce + self.cipher.encrypt(nonce, plain, reference.encode())
        if self.used_bytes + len(encrypted) > self.max_bytes:
            raise SafeError("STORAGE_FULL")
        temporary = self.root / ("." + secrets.token_hex(16))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            self.used_bytes += len(encrypted)
            if os.name != "nt":
                directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return reference

    def missing(self, referenced: set[str]) -> tuple[str, ...]:
        """返回被引用但密文文件缺失的引用（备份/恢复一致性校验用）。"""
        return tuple(
            sorted(
                reference
                for reference in referenced
                if REFERENCE_PATTERN.fullmatch(reference)
                and not (self.root / (reference + ".secret")).is_file()
            )
        )

    def snapshot(self, destination: Path, referenced: set[str]) -> dict:
        """把被引用的密文复制到目标目录（内容寻址、不可变，复制即一致性快照）。

        未被引用的密文不复制；任一引用缺少密文时拒绝生成快照，避免产出无法恢复的备份。
        """
        missing = self.missing(referenced)
        if missing:
            raise SafeError("SECRET_SNAPSHOT_INCOMPLETE")
        private_directory(destination)
        files = []
        total = 0
        for reference in sorted(referenced):
            if not REFERENCE_PATTERN.fullmatch(reference):
                continue
            data = self._ciphertext(reference)
            target = destination / (reference + ".secret")
            if target.is_symlink():
                raise SafeError("UNSAFE_STORAGE_PATH")
            if not target.exists():
                self._write_file(target, data)
            files.append(
                {
                    "reference": reference,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            total += len(data)
        return {"files": files, "count": len(files), "bytes": total}

    def restore_snapshot(self, source: Path) -> dict:
        """把快照中的密文补回本地：只新增缺失文件，绝不覆盖或删除现有密文。

        写入前用当前密钥试解一次（AAD 为引用名），因此错误密钥或损坏文件的快照会被拒绝。
        """
        if source.is_symlink() or not source.is_dir():
            raise SafeError("BACKUP_INVALID")
        added = 0
        skipped = 0
        for path in sorted(source.glob("*.secret")):
            if path.is_symlink():
                raise SafeError("UNSAFE_STORAGE_PATH")
            reference = path.name[: -len(".secret")]
            if not REFERENCE_PATTERN.fullmatch(reference):
                continue
            target = self.root / path.name
            if target.is_symlink():
                raise SafeError("UNSAFE_STORAGE_PATH")
            if target.exists():
                skipped += 1
                continue
            data = path.read_bytes()
            if len(data) > MAX_CIPHERTEXT_BYTES:
                raise SafeError("SECRET_CORRUPT")
            try:
                self.cipher.decrypt(data[:12], data[12:], reference.encode())
            except Exception:
                raise SafeError("SECRET_CORRUPT") from None
            self._write_file(target, data)
            added += 1
        return {"added": added, "skipped": skipped}

    def _ciphertext(self, reference: str) -> bytes:
        if not REFERENCE_PATTERN.fullmatch(reference):
            raise SafeError("SECRET_CORRUPT")
        path = self.root / (reference + ".secret")
        if path.is_symlink() or not path.is_file():
            raise SafeError("SECRET_CORRUPT")
        if path.stat().st_size > MAX_CIPHERTEXT_BYTES:
            raise SafeError("SECRET_CORRUPT")
        return path.read_bytes()

    def _write_file(self, target: Path, data: bytes) -> None:
        temporary = target.parent / ("." + secrets.token_hex(16))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            if os.name != "nt":
                directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def collect(self, referenced: set[str]) -> int:
        """删除不再被引用的密文，返回删除的文件数。

        调用方必须传入完整引用集合（Database.referenced_secrets()）；集合缺项会导致
        有效密文被删除，因此该操作只能在持 operation_lock 的维护入口调用，不自动执行。
        """
        removed = 0
        for path in self.root.glob("*.secret"):
            if path.is_symlink():
                continue
            reference = path.name[: -len(".secret")]
            if not REFERENCE_PATTERN.fullmatch(reference) or reference in referenced:
                continue
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            self.used_bytes = max(0, self.used_bytes - size)
            removed += 1
        return removed

    def get(self, reference: str) -> object:
        try:
            data = self._ciphertext(reference)
            plain = self.cipher.decrypt(data[:12], data[12:], reference.encode())
            return json.loads(plain)
        except Exception:
            raise SafeError("SECRET_CORRUPT") from None
