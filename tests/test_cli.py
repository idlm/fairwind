"""CLI 端到端测试：命令形态、退出码与输出脱敏。"""

import base64
import json
import os
import subprocess
import sys

import pytest

from accelerator.cli import execute, make_parser, optional_vault, udp_target
from accelerator.errors import SafeError
from accelerator.network import FetchResult
from accelerator.probing import UDP_TARGET_DEFAULT
from accelerator.storage import Database
from accelerator.subscription import SubscriptionEngine
from conftest import MASTER

pytestmark = pytest.mark.e2e

SUBSCRIPTION_SECRETS = (
    "synthetic-managed-token",
    "synthetic-managed-password",
    "managed.example",
)


@pytest.mark.parametrize(
    "command",
    [
        ["status"],
        ["nodes", "list"],
        ["nodes", "best"],
        ["route", "explain", "example.com"],
        ["diagnose"],
    ],
)
def test_cli_read_commands_without_key(tmp_path, command):
    environment = {
        key: value for key, value in os.environ.items() if key != "ACCELERATOR_SECRET_KEY"
    }
    result = subprocess.run(
        [sys.executable, "-m", "accelerator.cli", "--data-dir", str(tmp_path), *command],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0 and json.loads(result.stdout)
    assert result.stderr == ""


@pytest.mark.parametrize("command", [["route", "explain", "example.com"], ["diagnose"]])
def test_cli_json_output_survives_a_non_utf8_console(tmp_path, command):
    """非 UTF-8 控制台不得让命令失败（回归）。

    Windows CI（en-US）默认 cp1252：`emit` 用 `ensure_ascii=False`，中文写进 stdout 时
    `print` 抛 `UnicodeEncodeError`，被 `main` 的兜底当成 `INTERNAL_ERROR`——`diagnose`
    与 `route explain` 两条命令整条变成错误。这里显式用 cp1252 模拟那个控制台。
    """
    environment = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run(
        [sys.executable, "-m", "accelerator.cli", "--data-dir", str(tmp_path), *command],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout
    document = json.loads(result.stdout)
    assert "error" not in document, document
    assert result.stderr == ""


def test_cli_sensitive_command_missing_key(tmp_path):
    environment = {
        key: value for key, value in os.environ.items() if key != "ACCELERATOR_SECRET_KEY"
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "accelerator.cli",
            "--data-dir",
            str(tmp_path),
            "subscriptions",
            "update",
            "--master-url",
            MASTER,
        ],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout) == {"error": "SECRET_KEY_REQUIRED"}
    assert "synthetic-master-token" not in result.stdout + result.stderr


def test_udp_target_parsing():
    assert udp_target(None) == UDP_TARGET_DEFAULT
    assert udp_target("1.1.1.1:53") == ("1.1.1.1", 53)
    assert udp_target("example.com:5353") == ("example.com", 5353)
    assert udp_target("[2606:4700:4700::1111]:53") == ("2606:4700:4700::1111", 53)
    for invalid in (
        "1.1.1.1",
        "1.1.1.1:",
        "1.1.1.1:0",
        "1.1.1.1:99999",
        "127.0.0.1:53",
        "example.com:abc",
    ):
        with pytest.raises(SafeError, match="ARGUMENT_INVALID"):
            udp_target(invalid)


def test_optional_vault_keeps_serve_startable(tmp_path, monkeypatch):
    from accelerator.security import SecretVault

    monkeypatch.delenv("ACCELERATOR_SECRET_KEY", raising=False)
    assert optional_vault(tmp_path) is None
    monkeypatch.setenv("ACCELERATOR_SECRET_KEY", base64.urlsafe_b64encode(b"a" * 32).decode())
    assert isinstance(optional_vault(tmp_path), SecretVault)


def test_cli_diagnose_on_unsupported_schema(tmp_path, vault):
    database = Database(tmp_path, vault)
    with database.connection:
        database.connection.execute("PRAGMA user_version=99")
    database.close()
    environment = {
        key: value for key, value in os.environ.items() if key != "ACCELERATOR_SECRET_KEY"
    }
    result = subprocess.run(
        [sys.executable, "-m", "accelerator.cli", "--data-dir", str(tmp_path), "diagnose"],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout) == {"error": "SCHEMA_UNSUPPORTED"}


def test_invalid_cli_argument_not_echoed(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "accelerator.cli",
            "--data-dir",
            str(tmp_path),
            "nodes",
            "test",
            "--concurrency",
            "synthetic-secret-token",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 2
    assert "synthetic-secret-token" not in result.stdout + result.stderr


async def test_cli_output_redaction(database, vault, fetcher, tmp_path, capsys):
    await SubscriptionEngine(database, vault, fetcher).update(MASTER)
    for command in (["status"], ["nodes", "list"], ["nodes", "best"]):
        args = make_parser().parse_args(["--data-dir", str(tmp_path), *command])
        assert await execute(args) == 0
        captured = capsys.readouterr()
        output = captured.out + captured.err
        for forbidden in (
            "synthetic-password",
            "synthetic-master-token",
            "synthetic-source-token",
            "source-a.example",
            "hk.example",
            "11111111-1111-4111",
        ):
            assert forbidden not in output
        json.loads(captured.out)


async def test_all_five_cli_commands(tmp_path, monkeypatch, fetcher, capsys):
    from test_node_engine import FakeProbe

    from accelerator import host

    class OfflineFetcher:
        async def __aenter__(self):
            return fetcher

        async def __aexit__(self, *args):
            return None

    monkeypatch.setenv("ACCELERATOR_SECRET_KEY", base64.urlsafe_b64encode(b"a" * 32).decode())
    monkeypatch.setattr(host, "HttpFetcher", OfflineFetcher)
    monkeypatch.setattr(host, "ReferenceProbe", lambda target, udp_target=None: FakeProbe())
    commands = [
        ["subscriptions", "update", "--master-url", MASTER],
        ["nodes", "list"],
        ["nodes", "test"],
        ["nodes", "best"],
        ["status"],
    ]
    outputs = []
    for command in commands:
        args = make_parser().parse_args(["--data-dir", str(tmp_path), *command])
        assert await execute(args) == 0
        outputs.append(json.loads(capsys.readouterr().out))
    assert outputs[0]["nodes"] == 2
    assert outputs[1]["count"] == 2
    assert outputs[2]["states"]["AVAILABLE"] == 2
    assert len(outputs[3]["best"]) == 2
    assert outputs[4]["state"] == "DISCONNECTED"


async def test_explain_cli_commands(tmp_path, monkeypatch, fetcher, capsys):
    from test_node_engine import FakeProbe

    from accelerator import host

    class OfflineFetcher:
        async def __aenter__(self):
            return fetcher

        async def __aexit__(self, *args):
            return None

    monkeypatch.setenv("ACCELERATOR_SECRET_KEY", base64.urlsafe_b64encode(b"a" * 32).decode())
    monkeypatch.setattr(host, "HttpFetcher", OfflineFetcher)
    monkeypatch.setattr(host, "ReferenceProbe", lambda target, udp_target=None: FakeProbe())
    for command in (
        ["subscriptions", "update", "--master-url", MASTER],
        ["nodes", "test"],
        ["nodes", "list"],
    ):
        args = make_parser().parse_args(["--data-dir", str(tmp_path), *command])
        assert await execute(args) == 0
        captured = capsys.readouterr()
        if command[0] == "nodes" and command[1] == "list":
            node_id = json.loads(captured.out)["nodes"][0]["id"]

    args = make_parser().parse_args(["--data-dir", str(tmp_path), "nodes", "explain", node_id])
    assert await execute(args) == 0
    captured = capsys.readouterr()
    detail = json.loads(captured.out)
    assert detail["node"]["id"] == node_id and detail["state"] == "AVAILABLE"
    assert detail["score_explanation"]["score"] == detail["score"]
    assert detail["eligibility"]["status"] == "SELECTABLE"
    assert len(detail["history"]) == 3
    assert detail["note"] == "SENSITIVE_FIELDS_EXCLUDED"
    for forbidden in (
        "synthetic-password",
        "synthetic-master-token",
        "synthetic-source-token",
        "hk.example",
        "jp.example",
        "source-a.example",
        "11111111-1111-4111",
    ):
        assert forbidden not in captured.out + captured.err

    route = make_parser().parse_args(
        ["--data-dir", str(tmp_path), "route", "explain", "unknown.example", "--port", "443"]
    )
    assert await execute(route) == 0
    explained = json.loads(capsys.readouterr().out)
    assert explained["decision"] == "DEFAULT" and explained["matched_rule"] is None
    assert explained["query"] == {"host": "unknown.example", "port": 443}
    assert "未接入核心" in explained["note"]

    invalid = make_parser().parse_args(["--data-dir", str(tmp_path), "nodes", "explain", "zz"])
    with pytest.raises(SafeError, match="NODE_ID_INVALID"):
        await execute(invalid)


async def test_subscription_management_cli(tmp_path, monkeypatch, fetcher, capsys):
    from accelerator import host

    class OfflineFetcher:
        async def __aenter__(self):
            return fetcher

        async def __aexit__(self, *args):
            return None

    monkeypatch.setenv("ACCELERATOR_SECRET_KEY", base64.urlsafe_b64encode(b"a" * 32).decode())
    monkeypatch.setattr(host, "HttpFetcher", OfflineFetcher)
    manual = "https://source-c.example/sub?token=synthetic-managed-token"
    fetcher.responses[manual] = FetchResult(
        200, b"trojan://synthetic-managed-password@managed.example:443#M"
    )

    args = make_parser().parse_args(["--data-dir", str(tmp_path), "subscriptions", "list"])
    assert await execute(args) == 0
    listing = json.loads(capsys.readouterr().out)
    assert (
        listing["count"] == 0 and listing["note"] == "HANDLE_IS_PREFIX_OF_IRREVERSIBLE_URL_DIGEST"
    )

    args = make_parser().parse_args(["--data-dir", str(tmp_path), "subscriptions", "add", manual])
    assert await execute(args) == 0
    captured = capsys.readouterr()
    added = json.loads(captured.out)
    handle = added["subscription"]["handle"]
    assert len(handle) == 12 and added["subscription"]["origin"] == "MANUAL"
    assert added["note"] == "URL_ENCRYPTED_AND_NOT_ECHOED"
    for forbidden in SUBSCRIPTION_SECRETS:
        assert forbidden not in captured.out + captured.err

    args = make_parser().parse_args(["--data-dir", str(tmp_path), "subscriptions", "pause", handle])
    assert await execute(args) == 0
    paused = json.loads(capsys.readouterr().out)
    assert paused["subscription"]["user_state"] == "PAUSED"
    args = make_parser().parse_args(
        ["--data-dir", str(tmp_path), "subscriptions", "resume", handle]
    )
    assert await execute(args) == 0
    assert json.loads(capsys.readouterr().out)["subscription"]["user_state"] == "ACTIVE"

    args = make_parser().parse_args(
        ["--data-dir", str(tmp_path), "subscriptions", "remove", handle]
    )
    assert await execute(args) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["handle"] == handle and removed["removed_nodes"] == 0
    # 本测试从未跑过 Master 更新，因此"是否仍在 Master 列表"无从判断——如实回答未知
    assert removed["present_in_master"] is None
    assert removed["note"] == "MASTER_STATE_UNKNOWN_WITHOUT_KEY"
    assert all(value not in json.dumps(removed) for value in SUBSCRIPTION_SECRETS)

    bad = make_parser().parse_args(["--data-dir", str(tmp_path), "subscriptions", "pause", "zz"])
    with pytest.raises(SafeError, match="SUBSCRIPTION_ID_INVALID"):
        await execute(bad)
