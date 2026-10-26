# Zelda-Mobile

[English](README.md) | **简体中文**

Zelda-Mobile 让 Nintendo Switch 版《塞尔达传说：王国之泪》**v1.4.3** 在 Apple silicon Mac 与 iPad 上原生运行，iPhone 版处于早期阶段。游戏的 ARM64 代码直接在 Apple 芯片上执行，系统服务由宿主重新实现，NVN 图形命令翻译为 Metal。Mac 与 iOS 前端共用同一套核心。

> [!IMPORTANT]
> 本项目与任天堂无关，也未获其授权或认可。仓库**只包含源码**：不含游戏本体、更新包、密钥、存档、游戏着色器或任何由游戏派生的数据，也不说明获取途径。请只使用你自己拥有、并从自己主机导出的游戏与密钥。

## 项目状态

项目仍在开发中。可以读档游玩，但尚未验证完整通关。

| 平台 | 可用 | 已知限制 |
| --- | --- | --- |
| Apple silicon Mac | 读档与场景运行、键盘和 Xbox 手柄（含震动）、声音、过场动画、amiibo 文件扫描（实验功能）、30 / 60 / 90 FPS 目标 | 首次进入新区域或新视角时可能因着色器编译短暂卡顿；并非所有区域都能保持 60 FPS |
| iPad（M1 及以上） | 开发签名安装、资源导入、读档、触控手柄、镜头与声音 | 原版分辨率下明显低于 60 FPS；首次准备着色器耗时较长；实体手柄未完整验证 |
| iPhone 15 Pro | 开发签名安装、资源导入、经校验的离线着色器库、缓存热启动进入标题画面 | 尚未测量实际游玩、帧率、内存和发热；未试过其他 iPhone，不支持 BC 贴图压缩的芯片（A17 Pro 之前）未经测试 |

只支持 v1.4.3。准备脚本会校验模块指纹，拒绝其他版本。

## 环境要求

- **Apple silicon Mac**（M1 或更新），安装完整 Xcode、命令行工具与 Homebrew。
- **约 60 GB 可用空间**（可以放在外置盘）。解出的 RomFS 约 16 GB，着色器缓存和构建产物另需空间。
- 你自己的《王国之泪》本体与 **v1.4.3 更新包**（`.nsz`），以及匹配的 `prod.keys`。
- iPhone / iPad 版另需 Apple 开发者签名团队、已开启开发者模式的设备和 USB 连接。

## 快速开始（Mac）

完整步骤与故障排查见 [构建与运行](docs/getting-started.zh-CN.md)。概要：

```sh
git clone https://github.com/OJZen/totk-mobile.git
cd totk-mobile
brew install cmake pkg-config opus libvpx python@3.14
mkdir -p local/games local/keys
# 把本体和 v1.4.3 更新包放进 local/games/，prod.keys 放进 local/keys/，然后：
./script/prepare.sh
./script/prepare_resources.sh local/keys/prod.keys
./script/prepare_shaders.sh
./script/build_and_run.sh --load-only
./launch.command
```

之后双击 `launch.command`（或在终端运行）即可启动。`quick-load.command` 还会自动走游戏菜单，读取存档列表第一项。

首次启动要准备几万个着色器，可能需要数分钟或更久，启动画面会显示各阶段与进度；之后的启动会复用缓存。

amiibo 文件扫描目前仅支持 macOS。iPhone 和 iPad 尚未提供 amiibo 文件选择与扫描入口，也未验证 iOS 扫描功能。不支持实体 NFC 读卡器。详见 [amiibo 操作与限制](docs/controls.zh-CN.md#amiibomac实验功能)。

## 操作

| 操作 | 键盘 |
| --- | --- |
| 移动 / 镜头 | W A S D / I J K L |
| A / B / X / Y | X（确认）/ Z（返回）/ R / F |
| L / R、ZL / ZR | Q / E、Shift / 空格 |
| 道具菜单（+）/ 地图（−） | 回车 / Tab |
| 控制面板 / 性能面板 / 全屏 | ⌘, / ⌘P / ⌃⌘F |

Xbox 手柄经蓝牙或 USB 连接后自动接入。帧率、分辨率、MetalFX、声音和震动在控制面板（⌘,）中设置。详见 [操作与设置](docs/controls.zh-CN.md)。

## 存档

存档位于项目内的 `work/save/`，格式与主机相同。导入与备份见 [存档](docs/saves.zh-CN.md)。**游戏运行时不要修改这个目录。**

## 常见问题

**启动时间很长？** 首次准备着色器较慢，启动画面会显示当前阶段和进度；游戏中可在性能面板（⌘P）查看着色器活动。日志在 `work/logs/native-probe.log`。

**准备脚本报模块或版本不匹配？** 确认更新包是 v1.4.3，并且 `local/games/` 里本体和更新各只有一个。

**画面卡顿？** 首次看到某个场景时会现场编译管线，之后会缓存。如果持续低帧率，可以在控制面板切到 1080 或 720 分辨率对照。

**能在 Intel Mac 上运行吗？** 不能。游戏代码以 ARM64 原生执行，需要 Apple 芯片。

## 开发者

- [文档索引](docs/README.zh-CN.md)
- [构建与运行](docs/getting-started.zh-CN.md)：Mac、iPhone 与 iPad 的完整准备流程
- [源码地图](docs/architecture.zh-CN.md)：组件、生成文件和修改入口
- [开发指南](docs/development.zh-CN.md)：测试、诊断开关与贡献约定
- [仓库结构](docs/repository.zh-CN.md)：目录职责与不进入 Git 的本地数据

## 许可证

本仓库的原创代码采用 [Apache License 2.0](LICENSE) 授权。复用的第三方组件保留各自的许可，见 [第三方声明](THIRD_PARTY_NOTICES.zh-CN.md)。

## 致谢

Zelda-Mobile 以 [d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile) 为参考项目：装载器、ARM64 模块改写器和 NVN → Metal 后端沿用了它的设计，本项目所基于的那部分文件按其 MIT 许可收录在 `core/`。此外还参考了 Ryujinx、libnx、Switchbrew、SPIRV-Cross、hactool 等公开项目，详见 [第三方声明](THIRD_PARTY_NOTICES.zh-CN.md)。

“塞尔达传说”“王国之泪”“Nintendo Switch”等名称是任天堂的商标。
