import base64
import hashlib
import json
import re
import uuid
from collections.abc import Callable
from urllib.parse import parse_qsl, unquote, urlsplit

import yaml

from accelerator.classifier import classify
from accelerator.domain import NodeSecret, ParseResult, ProxyNode
from accelerator.errors import SafeError
from accelerator.security import canonical_host, canonical_json

MAX_SUBSCRIPTION_BYTES = 10 * 1024 * 1024
MAX_NODES = 10_000
PROTOCOLS = {"vless", "vmess", "trojan", "ss", "socks", "http"}
TRANSPORTS = {"tcp", "ws", "grpc", "http", "h2", "httpupgrade"}


def decode_base64(value: str) -> str:
    compact = "".join(value.split())
    try:
        return base64.b64decode(
            compact + "=" * (-len(compact) % 4), altchars=b"-_", validate=True
        ).decode("utf-8")
    except (ValueError, UnicodeError):
        raise SafeError("PARSE_FAILED") from None


def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SafeError("PARSE_FAILED")
        result[key] = value
    return result


def check_tree(value: object, depth: int = 0) -> None:
    if depth > 24:
        raise SafeError("PARSE_LIMIT")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise SafeError("PARSE_FAILED")
            check_tree(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            check_tree(child, depth + 1)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise SafeError("PARSE_FAILED")


def safe_json(text: str) -> object:
    try:
        value = json.loads(
            text,
            object_pairs_hook=unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(SafeError("PARSE_FAILED")),
        )
        check_tree(value)
        canonical_json(value)
        return value
    except (ValueError, RecursionError, TypeError):
        raise SafeError("PARSE_FAILED") from None


class StrictLoader(yaml.SafeLoader):
    pass


def construct_mapping(loader: StrictLoader, node: yaml.MappingNode) -> dict:
    pairs = []
    for key, value in node.value:
        decoded_key = loader.construct_object(key, deep=True)
        if not isinstance(decoded_key, str):
            raise SafeError("PARSE_FAILED")
        pairs.append((decoded_key, loader.construct_object(value, deep=True)))
    return unique_pairs(pairs)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)


def safe_yaml(text: str) -> object:
    try:
        depth = 0
        for index, token in enumerate(yaml.scan(text)):
            if index > 250_000:
                raise SafeError("PARSE_LIMIT")
            if isinstance(token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)):
                raise SafeError("PARSE_FAILED")
            if isinstance(
                token,
                (
                    yaml.BlockMappingStartToken,
                    yaml.BlockSequenceStartToken,
                    yaml.FlowSequenceStartToken,
                    yaml.FlowMappingStartToken,
                ),
            ):
                depth += 1
                if depth > 24:
                    raise SafeError("PARSE_LIMIT")
            elif isinstance(
                token, (yaml.BlockEndToken, yaml.FlowSequenceEndToken, yaml.FlowMappingEndToken)
            ):
                depth -= 1
        value = yaml.load(text, Loader=StrictLoader)
        check_tree(value)
        canonical_json(value)
        return value
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        raise SafeError("PARSE_FAILED") from None


def require_keys(value: dict, supported: set[str]) -> None:
    if set(value) - supported:
        raise SafeError("UNSUPPORTED_OPTION")


def flag(value: object) -> bool:
    if value in (True, "true", "1", 1):
        return True
    if value in (False, "false", "0", 0, "", None):
        return False
    raise SafeError("PARSE_FAILED")


class SubscriptionParser:
    def __init__(self, credential_digest: Callable[[bytes], str]):
        self.credential_digest = credential_digest

    def detect_format(self, text: str) -> str:
        stripped = text.lstrip("\ufeff \t\r\n")
        if stripped.startswith(("{", "[")):
            return "json"
        if re.search(r"(?m)^\s*proxies\s*:", stripped):
            return "clash"
        if re.search(r"(?m)^\s*[a-zA-Z][a-zA-Z0-9+.-]*://", stripped):
            return "uri"
        return "base64"

    def parse(self, data: bytes) -> ParseResult:
        if len(data) > MAX_SUBSCRIPTION_BYTES:
            raise SafeError("DOWNLOAD_TOO_LARGE")
        try:
            text = data.decode("utf-8-sig").strip()
        except UnicodeError:
            raise SafeError("PARSE_FAILED") from None
        if not text:
            raise SafeError("EMPTY_UPDATE")
        format_name = self.detect_format(text)
        if format_name == "base64":
            text = decode_base64(text)
            if self.detect_format(text) != "uri":
                raise SafeError("PARSE_FAILED")
        if format_name in {"json", "clash"}:
            document = safe_json(text) if format_name == "json" else safe_yaml(text)
            if not isinstance(document, dict):
                raise SafeError("PARSE_FAILED")
            if "proxies" in document:
                entries = document["proxies"]
                convert = self.from_clash
            elif "outbounds" in document:
                entries = document["outbounds"]
                convert = self.from_singbox
            else:
                raise SafeError("UNSUPPORTED_FORMAT")
            if not isinstance(entries, list):
                raise SafeError("PARSE_FAILED")
        else:
            entries = [
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            convert = self.from_uri
        if len(entries) > MAX_NODES:
            raise SafeError("PARSE_LIMIT")
        nodes = {}
        rejected = 0
        for entry in entries:
            try:
                node = convert(entry)
                nodes.setdefault(node.id, classify(node))
            except (SafeError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
                rejected += 1
        if not nodes:
            raise SafeError("EMPTY_UPDATE")
        return ParseResult(list(nodes.values()), rejected, format_name)

    def normalize(
        self,
        protocol: str,
        server: str,
        port: object,
        credentials: dict,
        name: str = "",
        transport: str = "tcp",
        tls: bool = False,
        options: dict | None = None,
    ) -> ProxyNode:
        if protocol not in PROTOCOLS or transport not in TRANSPORTS:
            raise SafeError("UNSUPPORTED_PROTOCOL")
        if not isinstance(name, str) or len(name) > 1024:
            raise SafeError("PARSE_FAILED")
        if isinstance(port, bool) or not re.fullmatch(r"[0-9]{1,5}", str(port)):
            raise SafeError("PARSE_FAILED")
        port = int(port)
        if not 1 <= port <= 65535:
            raise SafeError("PARSE_FAILED")
        server = canonical_host(server)
        options = dict(options or {})
        if protocol == "trojan" and not tls:
            raise SafeError("INSECURE_TLS")
        for key, value in credentials.items():
            if key == "alter_id":
                if type(value) is not int or not 0 <= value <= 65535:
                    raise SafeError("PARSE_FAILED")
            elif not isinstance(value, str) or not value or len(value) > 8192:
                raise SafeError("PARSE_FAILED")
        for key, value in options.items():
            if key == "alpn":
                if (
                    not isinstance(value, list)
                    or not all(isinstance(item, str) and 0 < len(item) <= 255 for item in value)
                    or len(value) > 16
                ):
                    raise SafeError("PARSE_FAILED")
            elif key == "udp":
                if type(value) is not bool:
                    raise SafeError("PARSE_FAILED")
            elif not isinstance(value, str) or len(value) > 8192:
                raise SafeError("PARSE_FAILED")
        if protocol in {"vless", "vmess"}:
            credentials["uuid"] = str(uuid.UUID(credentials["uuid"]))
        if protocol in {"trojan", "ss"} and not credentials.get("password"):
            raise SafeError("PARSE_FAILED")
        if protocol == "ss":
            if credentials.get("method") not in {
                "aes-128-gcm",
                "aes-256-gcm",
                "chacha20-ietf-poly1305",
                "2022-blake3-aes-128-gcm",
                "2022-blake3-aes-256-gcm",
                "2022-blake3-chacha20-poly1305",
            }:
                raise SafeError("UNSUPPORTED_OPTION")
        for field in ("sni", "host"):
            if options.get(field):
                options[field] = canonical_host(options[field])
        if transport in {"ws", "http", "h2", "httpupgrade"}:
            options.setdefault("path", "/")
        if options.get("security") == "reality":
            if not options.get("public_key") or not tls:
                raise SafeError("PARSE_FAILED")
        if tls:
            options.setdefault("security", "tls")
        if protocol == "vmess":
            credentials.setdefault("alter_id", 0)
            credentials.setdefault("security", "auto")
            if credentials["security"] not in {
                "auto",
                "aes-128-gcm",
                "chacha20-poly1305",
                "none",
                "zero",
            }:
                raise SafeError("UNSUPPORTED_OPTION")
        if protocol == "socks":
            options.setdefault("version", "5")
        secret = NodeSecret(server, port, credentials, options, name)
        identity = {
            "protocol": protocol,
            "server": server,
            "port": port,
            "transport": transport,
            "tls": tls,
            "credential_fingerprint": self.credential_digest(canonical_json(credentials)),
            "options_fingerprint": self.credential_digest(canonical_json(options)),
        }
        fingerprint = hashlib.sha256(canonical_json(identity)).hexdigest()
        return ProxyNode(fingerprint, protocol, transport, tls, secret)

    def from_uri(self, value: str) -> ProxyNode:
        if not isinstance(value, str) or len(value) > 32_768 or re.search(r"[\x00-\x20]", value):
            raise SafeError("PARSE_FAILED")
        scheme = value.split(":", 1)[0].lower()
        if scheme == "vmess":
            document = safe_json(decode_base64(value.removeprefix("vmess://")))
            require_keys(
                document,
                {
                    "v",
                    "ps",
                    "add",
                    "port",
                    "id",
                    "aid",
                    "scy",
                    "net",
                    "type",
                    "host",
                    "path",
                    "tls",
                    "sni",
                    "alpn",
                    "fp",
                },
            )
            if document.get("type", "none") not in {"none", ""}:
                raise SafeError("UNSUPPORTED_OPTION")
            options = {}
            for source, target in (
                ("sni", "sni"),
                ("host", "host"),
                ("path", "path"),
                ("alpn", "alpn"),
                ("fp", "fingerprint"),
            ):
                if document.get(source):
                    options[target] = document[source]
            if "alpn" in options:
                options["alpn"] = options["alpn"].split(",")
            tls_value = document.get("tls", "")
            if tls_value not in {"", "tls", "none"}:
                raise SafeError("UNSUPPORTED_OPTION")
            return self.normalize(
                "vmess",
                document["add"],
                document["port"],
                {
                    "uuid": document["id"],
                    "alter_id": int(document.get("aid", 0)),
                    "security": document.get("scy", "auto"),
                },
                document.get("ps", ""),
                document.get("net", "tcp"),
                tls_value == "tls",
                options,
            )
        if scheme == "ss" and "@" not in value.split("#", 1)[0]:
            body, _, name = value[5:].partition("#")
            value = "ss://" + decode_base64(body) + ("#" + name if name else "")
        parts = urlsplit(value)
        protocol = {"shadowsocks": "ss", "socks5": "socks", "https": "http"}.get(scheme, scheme)
        if protocol not in PROTOCOLS or not parts.hostname or not parts.port:
            raise SafeError("UNSUPPORTED_PROTOCOL")
        query = unique_pairs(parse_qsl(parts.query, keep_blank_values=True, max_num_fields=32))
        require_keys(
            query,
            {
                "type",
                "security",
                "sni",
                "peer",
                "host",
                "path",
                "serviceName",
                "mode",
                "flow",
                "fp",
                "pbk",
                "sid",
                "spx",
                "alpn",
                "encryption",
                "allowInsecure",
                "insecure",
                "headerType",
            },
        )
        if flag(query.get("allowInsecure")) or flag(query.get("insecure")):
            raise SafeError("INSECURE_TLS")
        if query.get("headerType", "none") != "none":
            raise SafeError("UNSUPPORTED_OPTION")
        if query.get("encryption", "none") != "none":
            raise SafeError("UNSUPPORTED_OPTION")
        if parts.path not in {"", "/"}:
            raise SafeError("UNSUPPORTED_OPTION")
        credentials = {}
        username = unquote(parts.username or "")
        password = unquote(parts.password or "")
        if protocol in {"vless", "vmess"}:
            if password:
                raise SafeError("PARSE_FAILED")
            credentials["uuid"] = username
        elif protocol == "trojan":
            if parts.password is not None:
                raise SafeError("PARSE_FAILED")
            credentials["password"] = username
        elif protocol == "ss":
            if not parts.password:
                username, password = decode_base64(username).split(":", 1)
            credentials.update(method=username, password=password)
        elif username or password:
            if not username or not password:
                raise SafeError("PARSE_FAILED")
            credentials.update(username=username, password=password)
        security = query.get(
            "security", "tls" if protocol == "trojan" or scheme == "https" else "none"
        )
        if security not in {"none", "tls", "reality"} or (
            protocol == "trojan" and security == "none"
        ):
            raise SafeError("UNSUPPORTED_OPTION")
        if protocol in {"http", "socks", "ss"} and query:
            raise SafeError("UNSUPPORTED_OPTION")
        options = {}
        for source, target in (
            ("sni", "sni"),
            ("peer", "sni"),
            ("host", "host"),
            ("path", "path"),
            ("serviceName", "service_name"),
            ("mode", "mode"),
            ("flow", "flow"),
            ("fp", "fingerprint"),
            ("pbk", "public_key"),
            ("sid", "short_id"),
            ("spx", "spider_x"),
        ):
            if query.get(source):
                if target in options and options[target] != query[source]:
                    raise SafeError("PARSE_FAILED")
                options[target] = query[source]
        if query.get("alpn"):
            options["alpn"] = query["alpn"].split(",")
        if security != "none":
            options["security"] = security
        return self.normalize(
            protocol,
            parts.hostname,
            parts.port,
            credentials,
            unquote(parts.fragment),
            query.get("type", "tcp"),
            security != "none",
            options,
        )

    def from_clash(self, entry: dict) -> ProxyNode:
        require_keys(
            entry,
            {
                "name",
                "type",
                "server",
                "port",
                "uuid",
                "password",
                "username",
                "cipher",
                "alterId",
                "network",
                "tls",
                "servername",
                "sni",
                "skip-cert-verify",
                "udp",
                "ws-opts",
                "grpc-opts",
                "flow",
                "client-fingerprint",
                "reality-opts",
                "alpn",
            },
        )
        protocol = {"socks5": "socks"}.get(entry["type"], entry["type"])
        if flag(entry.get("skip-cert-verify")):
            raise SafeError("INSECURE_TLS")
        credentials = {}
        if protocol in {"vless", "vmess"}:
            credentials["uuid"] = entry["uuid"]
        if protocol in {"trojan", "ss"}:
            credentials["password"] = entry["password"]
        if protocol == "ss":
            credentials["method"] = entry["cipher"]
        if protocol == "vmess":
            credentials.update(
                alter_id=int(entry.get("alterId", 0)), security=entry.get("cipher", "auto")
            )
        if protocol in {"http", "socks"} and (entry.get("username") or entry.get("password")):
            credentials.update(username=entry["username"], password=entry["password"])
        options = {}
        for source, target in (
            ("servername", "sni"),
            ("sni", "sni"),
            ("flow", "flow"),
            ("client-fingerprint", "fingerprint"),
            ("alpn", "alpn"),
        ):
            if entry.get(source):
                options[target] = entry[source]
        if "reality-opts" in entry:
            reality = entry["reality-opts"]
            require_keys(reality, {"public-key", "short-id"})
            options.update(
                security="reality",
                public_key=reality["public-key"],
                short_id=reality.get("short-id", ""),
            )
        if "ws-opts" in entry:
            if entry.get("network") != "ws":
                raise SafeError("UNSUPPORTED_OPTION")
            ws = entry["ws-opts"]
            require_keys(ws, {"path", "headers"})
            headers = ws.get("headers", {})
            require_keys(headers, {"Host"})
            options["path"] = ws.get("path", "/")
            if headers.get("Host"):
                options["host"] = headers["Host"]
        if "grpc-opts" in entry:
            if entry.get("network") != "grpc":
                raise SafeError("UNSUPPORTED_OPTION")
            grpc = entry["grpc-opts"]
            require_keys(grpc, {"grpc-service-name"})
            options["service_name"] = grpc.get("grpc-service-name", "")
        tls = flag(entry.get("tls", protocol == "trojan"))
        if "udp" in entry:
            options["udp"] = flag(entry["udp"])
        return self.normalize(
            protocol,
            entry["server"],
            entry["port"],
            credentials,
            entry.get("name", ""),
            entry.get("network", "tcp"),
            tls,
            options,
        )

    def from_singbox(self, entry: dict) -> ProxyNode:
        require_keys(
            entry,
            {
                "tag",
                "type",
                "server",
                "server_port",
                "uuid",
                "password",
                "username",
                "method",
                "security",
                "alter_id",
                "tls",
                "transport",
                "flow",
                "version",
            },
        )
        protocol = {"shadowsocks": "ss"}.get(entry["type"], entry["type"])
        credentials = {}
        if protocol in {"vless", "vmess"}:
            credentials["uuid"] = entry["uuid"]
        if protocol in {"trojan", "ss"}:
            credentials["password"] = entry["password"]
        if protocol == "ss":
            credentials["method"] = entry["method"]
        if protocol == "vmess":
            credentials.update(
                alter_id=int(entry.get("alter_id", 0)), security=entry.get("security", "auto")
            )
        if protocol in {"http", "socks"} and (entry.get("username") or entry.get("password")):
            credentials.update(username=entry["username"], password=entry["password"])
        tls_options = entry.get("tls", {})
        require_keys(tls_options, {"enabled", "server_name", "insecure", "alpn", "utls", "reality"})
        if flag(tls_options.get("insecure")):
            raise SafeError("INSECURE_TLS")
        options = {}
        if entry.get("flow"):
            options["flow"] = entry["flow"]
        if protocol == "socks":
            options["version"] = str(entry.get("version", "5"))
            if options["version"] != "5":
                raise SafeError("UNSUPPORTED_OPTION")
        if tls_options.get("server_name"):
            options["sni"] = tls_options["server_name"]
        if tls_options.get("alpn"):
            options["alpn"] = tls_options["alpn"]
        if "utls" in tls_options:
            utls = tls_options["utls"]
            require_keys(utls, {"enabled", "fingerprint"})
            if flag(utls.get("enabled")):
                options["fingerprint"] = utls["fingerprint"]
        if "reality" in tls_options:
            reality = tls_options["reality"]
            require_keys(reality, {"enabled", "public_key", "short_id"})
            if flag(reality.get("enabled")):
                options.update(
                    security="reality",
                    public_key=reality["public_key"],
                    short_id=reality.get("short_id", ""),
                )
        transport_options = entry.get("transport", {})
        require_keys(transport_options, {"type", "path", "headers", "service_name", "host"})
        transport = transport_options.get("type", "tcp")
        for key in ("path", "service_name"):
            if key in transport_options:
                options[key] = transport_options[key]
        headers = transport_options.get("headers", {})
        require_keys(headers, {"Host"})
        if headers.get("Host"):
            options["host"] = headers["Host"]
        if "host" in transport_options:
            host = transport_options["host"]
            if isinstance(host, list):
                if len(host) != 1:
                    raise SafeError("UNSUPPORTED_OPTION")
                host = host[0]
            options["host"] = host
        return self.normalize(
            protocol,
            entry["server"],
            entry["server_port"],
            credentials,
            entry.get("tag", ""),
            transport,
            flag(tls_options.get("enabled", False)),
            options,
        )
