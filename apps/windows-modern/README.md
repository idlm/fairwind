# Windows Modern

Target: Windows 10/11。计划由原生 UI → Application Service → CoreAdapter，再由独立 VPN Controller 管理 System Proxy/TUN。

**当前没有原生客户端代码、驱动或可连接二进制**；但平台边界的第一段能力——当前用户系统代理（快照/接管/还原/异常退出恢复、外来代理保护）——已经在参考引擎里实现并在 Windows 11 真机上验证：`core/fairwind/system_proxy.py`、`platform/windows/README.md`。TUN、按进程分流、安装包与签名仍未实现。
