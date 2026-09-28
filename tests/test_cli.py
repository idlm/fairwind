import json
import os
import subprocess
import sys

import pytest

from accelerator.cli import execute, make_parser, udp_target
from accelerator.errors import SafeError
from accelerator.probing import UDP_TARGET_DEFAULT
from accelerator.subscription import SubscriptionEngine
from conftest import MASTER

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("command", [["status"], ["nodes", "list"], ["nodes", "best"]])
def test_cli_read_commands_without_key(tmp_path, command):
    environment = {
        key: value for key, value in os.environ.items() if key != "ACCELERATOR_SECRET_KEY"
    }
    result = subprocess.run(
        [sys.executable, "-m", "accelerator.cli", "--data-dir", str(tmp_path), *command],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and json.loads(result.stdout)
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
    import base64

    from test_node_engine import FakeProbe

    from accelerator import cli

    class OfflineFetcher:
        async def __aenter__(self):
            return fetcher

        async def __aexit__(self, *args):
            return None

    monkeypatch.setenv("ACCELERATOR_SECRET_KEY", base64.urlsafe_b64encode(b"a" * 32).decode())
    monkeypatch.setattr(cli, "HttpFetcher", OfflineFetcher)
    monkeypatch.setattr(cli, "ReferenceProbe", lambda target, udp_target=None: FakeProbe())
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
