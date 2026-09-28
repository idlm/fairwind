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

from accelerator.errors import SafeError


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

    @classmethod
    def from_environment(cls, root: Path) -> "SecretVault":
        try:
            encoded = os.environ["ACCELERATOR_SECRET_KEY"]
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
        used = sum(path.stat().st_size for path in self.root.glob("*.secret"))
        if used + len(encrypted) > self.max_bytes:
            raise SafeError("STORAGE_FULL")
        temporary = self.root / ("." + secrets.token_hex(16))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            if os.name != "nt":
                directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return reference

    def get(self, reference: str) -> object:
        if not re.fullmatch(r"[a-f0-9]{64}", reference):
            raise SafeError("SECRET_CORRUPT")
        try:
            path = self.root / (reference + ".secret")
            if path.is_symlink() or path.stat().st_size > 12 * 1024 * 1024:
                raise ValueError
            data = path.read_bytes()
            plain = self.cipher.decrypt(data[:12], data[12:], reference.encode())
            return json.loads(plain)
        except Exception:
            raise SafeError("SECRET_CORRUPT") from None
