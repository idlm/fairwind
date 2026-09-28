# Windows 7 Legacy Compatibility Layer

独立 target、依赖锁、CI、签名与安装包；Modern 的 runtime 或核心升级不受 Legacy 限制。

必须在干净 Win7 SP1 VM/真机验证可运行核心、CPU 指令、TLS 根证书、SHA-2 签名、驱动、DNS、系统代理恢复、安装卸载与断电恢复。不得根据 Windows 最新 runner 的成功推断 Win7 兼容。

共享接口 schema、解析测试向量和路由语义。具体 runtime 与核心版本必须实测后锁定；本仓库 Python >=3.11 参考 CLI 不支持 Win7。Legacy 默认功能可少于 Modern，能力通过 adapter 显式声明。

状态：BLOCKED_EXTERNAL_REQUIREMENT。
