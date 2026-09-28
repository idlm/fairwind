import base64
import json
import random

import pytest

from accelerator.errors import SafeError
from accelerator.parser import MAX_SUBSCRIPTION_BYTES, safe_json, safe_yaml
from conftest import FIXTURES

pytestmark = pytest.mark.unit

UUID = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize(
    "name,count",
    [
        ("valid-uri.txt", 2),
        ("base64.txt", 1),
        ("clash.yaml", 2),
        ("singbox.json", 2),
        ("duplicate.txt", 1),
        ("mixed-nodes.txt", 1),
        ("huge.txt", 600),
    ],
)
def test_fixture_formats(parser, name, count):
    result = parser.parse((FIXTURES / name).read_bytes())
    assert len(result.nodes) == count


@pytest.mark.parametrize("name", ["invalid-uri.txt", "empty.txt", "malformed.yaml"])
def test_rejected_fixtures(parser, name):
    with pytest.raises(SafeError):
        parser.parse((FIXTURES / name).read_bytes())


def test_cross_format_dedupe(parser):
    results = [
        parser.parse((FIXTURES / name).read_bytes())
        for name in (
            "valid-uri.txt",
            "clash.yaml",
            "singbox.json",
        )
    ]
    assert len({result.nodes[0].id for result in results}) == 1
    assert len({result.nodes[1].id for result in results}) == 1


@pytest.mark.parametrize(
    "uri,protocol",
    [
        (f"vless://{UUID}@node.example:443?security=tls", "vless"),
        ("trojan://synthetic@node.example:443", "trojan"),
        ("ss://YWVzLTEyOC1nY206c3ludGhldGlj@node.example:443", "ss"),
        ("ss://aes-128-gcm:synthetic@node.example:443", "ss"),
        ("socks5://user:synthetic@node.example:1080", "socks"),
        ("socks://node.example:1080", "socks"),
        ("http://user:synthetic@node.example:8080", "http"),
        ("https://node.example:443", "http"),
        (f"vless://{UUID}@[2606:4700:4700::1111]:443", "vless"),
    ],
)
def test_protocols(parser, uri, protocol):
    assert parser.parse(uri.encode()).nodes[0].protocol == protocol


def test_vmess(parser):
    document = {
        "v": "2",
        "ps": "Tokyo",
        "add": "node.example",
        "port": "443",
        "id": UUID,
        "aid": "0",
        "scy": "auto",
        "net": "ws",
        "type": "none",
        "host": "edge.example",
        "path": "/websocket",
        "tls": "tls",
    }
    uri = b"vmess://" + base64.b64encode(json.dumps(document).encode())
    node = parser.parse(uri).nodes[0]
    assert node.protocol == "vmess" and node.transport == "ws" and node.tls
    assert node.secret.options["path"] == "/websocket"


def test_ss_legacy(parser):
    uri = b"ss://" + base64.b64encode(b"aes-128-gcm:synthetic@node.example:443")
    assert parser.parse(uri).nodes[0].secret.credentials["password"] == "synthetic"


@pytest.mark.parametrize(
    "suffix",
    [
        "?security=tls&sni=edge.example",
        "?type=ws&path=%2Fone",
        "?type=ws&path=%2Ftwo",
        "?type=grpc&serviceName=test",
        "?security=tls",
        "?security=reality&pbk=synthetic-public-key&sid=1234&fp=chrome",
    ],
)
def test_connection_options_participate_in_identity(parser, suffix):
    base = f"vless://{UUID}@node.example:443"
    assert (
        parser.parse(base.encode()).nodes[0].id
        != parser.parse((base + suffix).encode()).nodes[0].id
    )


def test_canonical_host_and_name_ignored(parser):
    first = f"vless://{UUID}@NODE.EXAMPLE.:443#JP"
    second = f"vless://{UUID}@node.example:443#HK"
    assert len(parser.parse((first + "\n" + second).encode()).nodes) == 1


@pytest.mark.parametrize(
    "uri",
    [
        "vless://bad@node.example:443",
        f"vless://{UUID}@node.example:65536",
        f"vless://{UUID}@node.example:0",
        f"vless://{UUID}@node.example:443?type=bogus",
        f"vless://{UUID}@node.example:443?allowInsecure=1",
        f"vless://{UUID}@node.example:443?type=ws&type=tcp",
        f"vless://{UUID}@node.example:443?security=reality",
        "trojan://@node.example:443",
        "trojan://synthetic@node.example:443?security=none",
        "http://node.example:8080/subscription",
        "http://node.example:8080?token=secret",
        "ss://aes-128-gcm:synthetic@node.example:443?plugin=exec",
        "file:///etc/passwd",
        "vmess://e30=",
        "vmess://W10=",
    ],
)
def test_bad_nodes_fail_closed(parser, uri):
    with pytest.raises(SafeError):
        parser.parse(uri.encode())


@pytest.mark.parametrize(
    "text",
    [
        'proxies: !!python/object/apply:os.system ["echo forbidden"]',
        "proxies: &ref [*ref]",
        "proxies: []\nproxies: []",
        "proxies: " + "[" * 30 + "0" + "]" * 30,
        "proxies: [{port: .nan}]",
    ],
)
def test_yaml_attacks(text):
    with pytest.raises(SafeError):
        safe_yaml(text)


@pytest.mark.parametrize(
    "text",
    [
        '{"outbounds":[],"outbounds":[]}',
        '{"outbounds":NaN}',
        "[" * 30 + "0" + "]" * 30,
        '{"outbounds":1e1000}',
    ],
)
def test_json_attacks(text):
    with pytest.raises(SafeError):
        safe_json(text)


def test_resource_bounds(parser):
    with pytest.raises(SafeError, match="DOWNLOAD_TOO_LARGE"):
        parser.parse(b"x" * (MAX_SUBSCRIPTION_BYTES + 1))
    line = f"vless://{UUID}@node.example:443\n".encode()
    with pytest.raises(SafeError, match="PARSE_LIMIT"):
        parser.parse(line * 10001)


@pytest.mark.parametrize(
    "name,country",
    [
        ("HK 01", "HK"),
        ("香港", "HK"),
        ("🇭🇰", "HK"),
        ("Taiwan", "TW"),
        ("东京", "JP"),
        ("🇯🇵", "JP"),
        ("Seoul", "KR"),
        ("Singapore", "SG"),
        ("USA", "US"),
        ("London", "GB"),
        ("Germany", "DE"),
        ("Canada", "CA"),
        ("Australia", "AU"),
        ("unknown", "OTHER"),
    ],
)
def test_classification(parser, name, country):
    from urllib.parse import quote

    uri = f"vless://{UUID}@node.example:443#{quote(name)}"
    assert parser.parse(uri.encode()).nodes[0].country == country


def test_fuzz_does_not_raise_raw_exception(parser):
    generator = random.Random(1234)
    for _ in range(200):
        data = generator.randbytes(generator.randrange(0, 200))
        try:
            parser.parse(data)
        except SafeError:
            pass


def test_structured_unsupported_field_not_silently_dropped(parser):
    document = json.loads((FIXTURES / "singbox.json").read_text())
    document["outbounds"][0]["detour"] = "untrusted-hop"
    result = parser.parse(json.dumps(document).encode())
    assert len(result.nodes) == 1 and result.rejected == 2
