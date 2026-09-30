"""核心配置生成器的单元测试（纯逻辑，不需要核心二进制）。"""

import json
import os
from pathlib import Path

import pytest

from conftest import requires_symlinks
from fairwind import core_config
from fairwind.domain import NodeSecret, ProxyNode
from fairwind.errors import SafeError

pytestmark = pytest.mark.unit

UUID = "11111111-1111-4111-8111-111111111111"
PASSWORD = "synthetic-core-password"
METHOD = "aes-128-gcm"
CREDENTIALS = {
    "vless": {"uuid": UUID},
    "vmess": {"uuid": UUID, "alterId": 0, "security": "auto"},
    "trojan": {"password": PASSWORD},
    "ss": {"password": PASSWORD, "method": METHOD},
}


def node(protocol="vless", transport="tcp", tls=True, credentials=None, options=None, **kwargs):
    return ProxyNode(
        kwargs.pop("id", "node-1"),
        protocol,
        transport,
        tls,
        NodeSecret(
            "node.example",
            443,
            credentials or CREDENTIALS.get(protocol, {}),
            options or {},
            "raw name",
        ),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("protocol", "uses_users"),
    [("vless", True), ("vmess", True), ("trojan", False), ("ss", False)],
)
def test_generate_per_protocol_and_validate(protocol, uses_users):
    config = core_config.generate(node(protocol), 10808)
    core_config.validate(config)
    outbound = config["outbounds"][0]
    assert outbound["protocol"] == core_config.PROTOCOL_MAP[protocol]
    assert outbound["streamSettings"]["network"] == "tcp"
    assert outbound["streamSettings"]["security"] == "tls"
    assert outbound["streamSettings"]["tlsSettings"]["serverName"] == "node.example"
    settings = outbound["settings"]
    entry = (settings["vnext"] if uses_users else settings["servers"])[0]
    assert entry["address"] == "node.example" and entry["port"] == 443
    if uses_users:
        assert entry["users"][0]["id"] == UUID
    else:
        assert entry["password"] == PASSWORD
    if protocol == "ss":
        assert settings["servers"][0]["method"] == METHOD
    if protocol == "vless":
        assert settings["vnext"][0]["users"][0]["encryption"] == "none"


def test_inbounds_are_loopback_only_and_udp_disabled():
    config = core_config.generate(node(), 10809, http_port=10810)
    socks, http = config["inbounds"]
    assert socks["listen"] == http["listen"] == core_config.LOOPBACK
    assert socks["port"] == 10809 and http["port"] == 10810
    assert socks["settings"] == {"auth": "noauth", "udp": False}
    assert config["log"]["access"] == "none"
    broken = json.loads(json.dumps(config))
    broken["inbounds"][0]["settings"]["udp"] = True
    with pytest.raises(SafeError, match="CORE_CONFIG_INVALID"):
        core_config.validate(broken)
    widened = json.loads(json.dumps(config))
    widened["inbounds"][0]["listen"] = "0.0.0.0"
    with pytest.raises(SafeError, match="CORE_CONFIG_INVALID"):
        core_config.validate(widened)


def test_ws_transport_carries_path_and_host():
    config = core_config.generate(
        node(transport="ws", options={"path": "/tunnel", "host": "cdn.example"}), 10808
    )
    stream = config["outbounds"][0]["streamSettings"]
    assert stream["network"] == "ws"
    assert stream["wsSettings"] == {"path": "/tunnel", "headers": {"Host": "cdn.example"}}


def test_unsupported_inputs_are_refused_not_guessed():
    for bad in (
        node(protocol="hysteria2"),
        node(transport="grpc"),
        node(options={"security": "reality"}),
    ):
        with pytest.raises(SafeError, match="CORE_CONFIG_UNSUPPORTED"):
            core_config.generate(bad, 10808)
    with pytest.raises(SafeError, match="CORE_CONFIG_INVALID"):
        core_config.generate(node(credentials={"uuid": ""}), 10808)
    with pytest.raises(SafeError, match="CORE_CONFIG_INVALID"):
        core_config.generate(node("ss", credentials={"password": PASSWORD}), 10808)
    for bad_port in (0, 70000):
        with pytest.raises(SafeError, match="CONFIG_REJECTED"):
            core_config.generate(node(), bad_port)
    with pytest.raises(SafeError, match="CONFIG_REJECTED"):
        core_config.generate(node(), 10808, log_level="trace")


def test_redaction_removes_credentials_without_mutating_the_original():
    for protocol in ("vless", "vmess", "trojan", "ss"):
        target = node(protocol)
        config = core_config.generate(target, 10808)
        assert core_config.contains_secrets(config, target) is True
        redacted = core_config.redact(config)
        assert core_config.contains_secrets(redacted, target) is False
        assert core_config.contains_secrets(config, target) is True, "原配置不能被就地改写"
        blob = json.dumps(redacted)
        assert core_config.REDACTED in blob
        for secret in (UUID, PASSWORD):
            assert secret not in blob


def test_config_file_is_atomic_private_and_removable(tmp_path):
    target = node()
    config = core_config.generate(target, 10808)
    path = core_config.write_config(tmp_path / "core", config)
    assert path.name == core_config.CONFIG_NAME
    assert json.loads(path.read_text(encoding="utf-8")) == config
    assert not list(path.parent.glob(".*tmp")), "原子写入不能留下临时文件"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    core_config.remove_config(path)
    assert not path.exists()
    core_config.remove_config(path)  # 幂等


@requires_symlinks
def test_config_writer_refuses_symlinked_paths(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(elsewhere)
    with pytest.raises(SafeError, match="UNSAFE_STORAGE_PATH"):
        core_config.write_config(link, core_config.generate(node(), 10808))


def test_generation_is_deterministic_and_drops_raw_names():
    first = json.dumps(core_config.generate(node(), 10808), sort_keys=True)
    second = json.dumps(core_config.generate(node(), 10808), sort_keys=True)
    assert first == second
    assert "raw name" not in first, "节点原始名称不得进入核心配置"


def test_core_binaries_stay_out_of_git():
    root = Path(__file__).resolve().parents[1]
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "third_party/" in ignore
    assert "evidence/" in ignore


def test_default_config_has_no_stats_api():
    """缺省不打开统计 API：没有要求测量，就不该多开一个监听端口。"""
    config = core_config.generate(node(), 10808)
    assert "api" not in config and "stats" not in config
    assert [i["tag"] for i in config["inbounds"]] == ["socks-in"]
    assert len(config["outbounds"]) == 1


def test_stats_api_is_loopback_only_and_keeps_one_proxy_outbound():
    """真实流量字节的唯一来源：只回环的统计入站；出站仍然只有一个代理。"""
    config = core_config.generate(node(), 10808, api_port=10809)
    core_config.validate(config)
    api_inbound = [i for i in config["inbounds"] if i["tag"] == core_config.API_INBOUND_TAG]
    assert len(api_inbound) == 1
    assert api_inbound[0]["listen"] == core_config.LOOPBACK
    assert api_inbound[0]["port"] == 10809
    assert api_inbound[0]["protocol"] == core_config.API_INBOUND_PROTOCOL
    assert config["api"] == {"tag": core_config.API_HANDLER_TAG, "services": ["StatsService"]}
    assert config["policy"]["system"]["statsInboundUplink"] is True
    assert config["routing"]["rules"] == [
        {
            "type": "field",
            "inboundTag": [core_config.API_INBOUND_TAG],
            "outboundTag": core_config.API_HANDLER_TAG,
        }
    ]
    assert [o["tag"] for o in config["outbounds"]] == ["proxy"]
    # 凭据脱敏在带统计入站时也必须照旧
    redacted = core_config.redact(config)
    assert UUID not in json.dumps(redacted)


@pytest.mark.parametrize(
    "kwargs",
    [{"api_port": 0}, {"api_port": 70000}, {"api_port": 10808}, {"http_port": 0}],
)
def test_generate_rejects_impossible_ports(kwargs):
    with pytest.raises(SafeError, match="CONFIG_REJECTED"):
        core_config.generate(node(), 10808, **kwargs)


@pytest.mark.parametrize("broken", ["no_api_handler", "two_api_inbounds", "foreign_rule"])
def test_validate_rejects_a_malformed_stats_api(broken):
    config = core_config.generate(node(), 10808, api_port=10809)
    if broken == "no_api_handler":
        del config["api"]
    elif broken == "two_api_inbounds":
        extra = dict(config["inbounds"][-1])
        extra["port"] = 10810
        config["inbounds"].append(extra)
    else:
        config["routing"]["rules"][0]["outboundTag"] = "proxy"
    with pytest.raises(SafeError, match="CORE_CONFIG_INVALID"):
        core_config.validate(config)
