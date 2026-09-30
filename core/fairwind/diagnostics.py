"""离线自检诊断：只读取本机真实状态并复述固定错误码，**不联网、不含凭据**。

每个检查给出 `name` / `status`（PASS / WARN / FAIL / SKIP）/ `detail` / `error_code`：
- FAIL 表示"必须处理"，对应 `TROUBLESHOOTING.md` 里的固定错误码；
- WARN 表示"可用但退化"（例如达到失败退避上限、权限过宽），不会阻止只读使用；
- SKIP 表示"当前无法或无需判断"（例如没有密钥、Windows 无 POSIX 权限位），**不伪装成通过**。
"""

import os
from pathlib import Path

from fairwind import __version__, system_proxy
from fairwind.errors import SafeError
from fairwind.profile_update import ProfileRegistry, load_public_key
from fairwind.scoring import SmartSelector
from fairwind.security import SecretVault
from fairwind.storage import SCHEMA_VERSION, Database, required_references
from fairwind.subscription import BACKOFF

DIAGNOSTIC_NOTE = "NO_CREDENTIALS_OR_URLS_INCLUDED"
NETWORK_NOTE = "NOT_CONTACTED"
STATUS_ORDER = ("PASS", "WARN", "FAIL", "SKIP")


def _check(name: str, status: str, detail: str, error_code: str | None = None) -> dict:
    return {"name": name, "status": status, "detail": detail, "error_code": error_code}


def _permission_check(name: str, path: Path, label: str | None = None) -> dict:
    """只报告固定名称与权限位，不回显数据目录名等本机信息。"""
    shown = label or path.name
    if os.name == "nt":
        return _check(name, "SKIP", "Windows 不使用 POSIX 权限位", None)
    if not path.exists():
        return _check(name, "SKIP", f"{shown}尚未创建（按需生成，本项不评估权限）", None)
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        return _check(
            name,
            "FAIL",
            f"{shown}权限为 {oct(mode)}：同组或其他用户可访问",
            "UNSAFE_STORAGE_PATH",
        )
    return _check(name, "PASS", f"{shown}权限为 {oct(mode)}", None)


def _schema_check(database: Database) -> dict:
    version = database.connection.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        return _check(
            "database_schema",
            "FAIL",
            f"user_version={version}，本程序只接受 {SCHEMA_VERSION}",
            "SCHEMA_UNSUPPORTED",
        )
    return _check("database_schema", "PASS", f"user_version={version}", None)


def _integrity_check(database: Database) -> dict:
    result = database.connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        return _check(
            "database_integrity",
            "FAIL",
            f"integrity_check 返回 {result!r}",
            "STORAGE_INTEGRITY_FAILED",
        )
    return _check("database_integrity", "PASS", "integrity_check = ok", None)


def _secret_key_check(vault: SecretVault | None) -> dict:
    if vault is None:
        return _check(
            "secret_key", "SKIP", "未提供 FAIRWIND_SECRET_KEY：只读功能可用，密文检查跳过", None
        )
    return _check("secret_key", "PASS", "已加载密钥（内容不回显）", None)


def _secret_coverage_check(database: Database, vault: SecretVault | None) -> dict:
    if vault is None:
        return _check("secret_coverage", "SKIP", "需要密钥才能校验密文完整性", None)
    references = required_references(database.connection)
    missing = vault.missing(references)
    if missing:
        return _check(
            "secret_coverage",
            "FAIL",
            f"{len(missing)}/{len(references)} 个被引用密文缺失，相关节点将无法解密",
            "SECRET_SNAPSHOT_INCOMPLETE",
        )
    return _check("secret_coverage", "PASS", f"{len(references)} 个被引用密文全部存在", None)


def _key_check_check(database: Database, vault: SecretVault | None) -> dict:
    if vault is None:
        return _check("key_check", "SKIP", "需要密钥才能校验数据库与密钥是否匹配", None)
    stored = database.get_setting("key_check")
    if stored and stored != vault.digest(b"database-key-check-v1"):
        return _check(
            "key_check", "FAIL", "数据库记录的 key_check 与当前密钥不一致", "SECRET_KEY_MISMATCH"
        )
    return _check("key_check", "PASS", "数据库与当前密钥匹配", None)


def _nodes_check(database: Database) -> dict:
    rows = database.nodes()
    if not rows:
        return _check("nodes", "SKIP", "尚无可见节点：先更新订阅并执行 nodes test", None)
    candidates = [(row, database.history(row["id"])) for row in rows]
    states: dict[str, int] = {}
    for _row, history in candidates:
        state = history[0]["state"] if history else "UNTESTED"
        states[state] = states.get(state, 0) + 1
    eligible = SmartSelector().select(candidates)
    return _check(
        "nodes",
        "PASS",
        f"{len(rows)} 个可见节点；合格候选 {len(eligible)}；状态分布 {states}",
        None,
    )


def _subscriptions_check(database: Database) -> dict:
    rows = database.subscriptions()
    state = database.subscription_state()
    paused = [row["display_name"] for row in rows if row["id"] in state["paused"]]
    stopped = [row["display_name"] for row in rows if row["failure_count"] >= len(BACKOFF)]
    detail = (
        f"{len(rows)} 个来源；手动 {len(state['manual'])}；暂停 {len(paused)}；"
        f"达到失败上限 {len(stopped)}"
    )
    if stopped:
        return _check(
            "subscriptions",
            "WARN",
            f"{detail}（退避暂停：{'、'.join(sorted(stopped))}）",
            "RETRY_PAUSED",
        )
    return _check("subscriptions", "PASS", detail, None)


def _master_check(database: Database, vault: SecretVault | None) -> dict:
    if vault is None:
        return _check("master", "SKIP", "需要密钥才能读取 Master 快照", None)
    reference = database.get_setting("master")
    if not reference:
        return _check("master", "SKIP", "尚未加载过 Master", None)
    payload = vault.get(reference)
    if not isinstance(payload, dict):
        return _check("master", "FAIL", "Master 快照结构异常", "SECRET_CORRUPT")
    urls = payload.get("urls", [])
    failures = int(payload.get("failure_count", 0))
    if failures >= len(BACKOFF):
        return _check(
            "master", "WARN", f"Master 连续失败 {failures} 次，已停止重试", "RETRY_PAUSED"
        )
    return _check("master", "PASS", f"Master 快照含 {len(urls)} 个来源，失败计数 {failures}", None)


def _profiles_check(data_dir: Path) -> dict:
    registry = ProfileRegistry(data_dir / "profiles")
    version, profiles = registry.current()
    try:
        previous_version = registry.previous()[0]
    except SafeError:
        previous_version = None
    try:
        load_public_key()
        trust_root = True
    except SafeError:
        trust_root = False
    detail = (
        f"注册表版本 {version}（{len(profiles)} 个 profile），LKG {previous_version}，"
        f"信任根{'已注入' if trust_root else '未注入'}"
    )
    if version == 0 and not trust_root:
        return _check("profiles", "SKIP", f"{detail}：尚未使用远程更新", None)
    if not trust_root:
        return _check("profiles", "WARN", f"{detail}：远程更新会被拒绝", "PROFILE_PUBKEY_REQUIRED")
    return _check("profiles", "PASS", detail, None)


def _routing_rules_check(database: Database) -> dict:
    return _check(
        "routing_rules", "PASS", f"{len(database.routing_rules())} 条规则（0 条正常）", None
    )


def _core_check(integrated: bool = False) -> dict:
    """核心检查：缺二进制标 SKIP（不是"通过"），就位才说就位。

    这里只检查"固定核心是否已在本机就位"，**不**把它写成"节点可用"——节点可用只能由真实握手证明。
    """
    if integrated:
        return _check(
            "core",
            "PASS",
            "CORE_INTEGRATED：固定核心已按清单就位（版本与摘要由 scripts/fetch_core.py 校验）",
            None,
        )
    return _check("core", "SKIP", "CORE_NOT_INTEGRATED：未接入任何核心，连接类操作固定拒绝", None)


def _system_proxy_check(data_dir: Path) -> dict:
    """系统代理检查（只读）：有接管记录未还原时给 WARN，非 Windows 给 SKIP。

    本项**只读**：诊断不允许修改用户的系统设置，所以这里只报告状态与固定错误码。
    """
    if not system_proxy.platform_supported():
        return _check("system_proxy", "SKIP", "当前平台未实现系统代理（不宣称）", None)
    current = system_proxy.SystemProxyController(data_dir).current()
    if not current.get("supported"):
        return _check("system_proxy", "WARN", "无法读取系统代理设置", current.get("error") or None)
    if current["recovery_pending"]:
        return _check(
            "system_proxy",
            "WARN",
            "存在未还原的接管记录，且当前设置已被改动（不会自动覆盖，交由用户决定）",
            "SYSTEM_PROXY_RECOVERY_PENDING",
        )
    if current["owned_by_fairwind"]:
        return _check("system_proxy", "PASS", "系统代理由本程序接管中", None)
    if current["enabled"]:
        return _check(
            "system_proxy",
            "PASS",
            "系统代理由其它软件设置（本程序不会覆盖）",
            None,
        )
    return _check("system_proxy", "PASS", "系统代理当前未启用", None)


def diagnose(
    data_dir: Path,
    database: Database,
    vault: SecretVault | None = None,
    core_available: bool = False,
) -> dict:
    """运行全部离线检查；不联网、不修改状态、输出里不含凭据或订阅 URL。"""
    checks = [
        _permission_check("data_directory", data_dir, "数据目录"),
        _permission_check("database_file", data_dir / "fairwind.sqlite3", "数据库文件"),
        _permission_check("secrets_directory", data_dir / "secrets", "密文目录"),
        _permission_check("control_token", data_dir / "control.token", "控制面令牌文件"),
        _schema_check(database),
        _integrity_check(database),
        _secret_key_check(vault),
        _key_check_check(database, vault),
        _secret_coverage_check(database, vault),
        _master_check(database, vault),
        _subscriptions_check(database),
        _nodes_check(database),
        _routing_rules_check(database),
        _profiles_check(data_dir),
        _system_proxy_check(data_dir),
        _core_check(core_available),
    ]
    counts = {
        status: sum(1 for check in checks if check["status"] == status) for status in STATUS_ORDER
    }
    if counts["FAIL"]:
        overall = "FAILED"
    elif counts["WARN"]:
        overall = "DEGRADED"
    else:
        overall = "OK"
    return {
        "version": __version__,
        "status": overall,
        "counts": counts,
        "checks": checks,
        "core": "INTEGRATED" if core_available else "NOT_INTEGRATED",
        "note": DIAGNOSTIC_NOTE,
        "network": NETWORK_NOTE,
    }
