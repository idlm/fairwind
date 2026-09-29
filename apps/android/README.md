# Android

后续使用 Kotlin、VpnService、TUN FD 和允许应用列表。用户授权、服务前台通知、切网恢复、按应用路由必须在真机验证。当前没有 APK；不创建空壳 UI 冒充实现。

---

## 当前状态（2026-09-29）

上面的"当前没有 APK"已经不再成立：Kotlin 源码可以编译，`assembleDebug` 产出 debug APK。
但**真机结论不变**——没有设备、没有获批核心，所以客户端不声称"已连接"、不给任何流量或延迟数字，
无核心时界面显示未连接状态而不是假装成功。

| 项 | 状态 | 证据 |
|---|---|---|
| 源码 | 38 个 Kotlin 文件：`VpnService` + 前台服务、CoreHost 契约、SmartSelector、按应用、游戏模式、故障转移、诊断、Compose 六页 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 编译 | 全量强制重编译：0 error / 0 warning | [BUILD.md](BUILD.md) |
| 产物 | `app-debug.apk` 10,299,095 字节，sha256 `7c27e49b04e85f49d58de1c9d6952e945d6e41bbda961cf9baa68b891c3df83e`，APK Signature Scheme v2 校验通过，`zipalign -c 4` OK，15 个 dex（APK 不按位可复现：zip 时间戳与自动生成的调试密钥都会变，重建得到同尺寸、不同哈希） | [BUILD.md](BUILD.md) |
| 真机 | **未验证**：`adb devices` 为空，未安装、未启动、未渲染 | [BUILD.md](BUILD.md) |
| 连接 | **未实现**：没有获批核心，连接返回 `CORE_NOT_AVAILABLE`；流量如实标注未测量 | [ARCHITECTURE.md](ARCHITECTURE.md) 第 8 节 |
| 发布 | **BLOCKED_EXTERNAL_REQUIREMENT**：没有签名 keystore，release APK 无法签名（debug APK 用自动生成的调试密钥，不可分发） | [ARCHITECTURE.md](ARCHITECTURE.md) 第 8 节 |

### 构建

工具链是**可移植安装**：只写进一个目录，不改系统 `PATH`、不写注册表、不需要管理员权限。

```bash
bash scripts/android_toolchain.sh   # 一次性：Temurin JDK 17 + Android SDK 35 + Gradle 8.10.2
bash scripts/android_build.sh       # 产出 debug APK（三道内存受限 pass）
```

4 GB 内存的主机上，单次 Gradle 调用会死在 JIT 的**原生**内存 arena（`ChunkPool::allocate` /
`Failed to commit metaspace`），而不是干净的堆 OOM，且 `-Xmx` 无处可加；`scripts/android_build.sh`
因此把编译 / dex / 打包拆成三次调用。参数与两个构建陷阱（`--offline` 会缺两个依赖；堆太小表现为
`DexArchiveMergerException`）记在 [BUILD.md](BUILD.md)。

文档：[ARCHITECTURE.md](ARCHITECTURE.md)（分层、与宿主契约的边界、验收清单）·
[BUILD.md](BUILD.md)（工具链、构建记录、产物校验命令、未验证项）。本目录 `.gitignore` 排除构建产物、
`local.properties` 与签名材料。
