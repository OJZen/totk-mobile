# 构建与运行

[English](getting-started.md) | **简体中文**

以下命令都在项目根目录执行。环境准备好之后直接用启动入口即可，不需要重新解包、翻译或清空缓存。

## 环境要求

- Apple silicon Mac，安装完整 Xcode 与命令行工具。
- Homebrew 包：`cmake`、`pkg-config`、`opus`、`libvpx`、`python@3.14`。
- 你自己的本体与 **v1.4.3** 更新包（`.nsz`），以及匹配的 `prod.keys`。
- 项目所在磁盘预留约 60 GB 可用空间。

游戏文件、密钥、存档、着色器派生产物和已签名应用都不进入仓库。`local/` 和 `work/` 已被 Git 忽略，见 [仓库结构](repository.zh-CN.md)。

## Mac：首次准备

```sh
git clone https://github.com/OJZen/totk-mobile.git
cd totk-mobile
brew install cmake pkg-config opus libvpx python@3.14
mkdir -p local/games local/keys local/save-imports
```

把本体和更新包放进 `local/games/`（文件名带 `[UPD]` 的是更新包），`prod.keys` 放进 `local/keys/`。如果找到多个本体或更新包，脚本会停止，不会自行猜测。

```sh
./script/prepare.sh
./script/prepare_resources.sh local/keys/prod.keys
./script/prepare_shaders.sh
./script/build_and_run.sh --load-only
```

| 步骤 | 作用 |
| --- | --- |
| `prepare.sh` | 建立 `work/venv`，提取 ExeFS，核对模块版本与原字节，把改写后的模块写入 `work/patched/` |
| `prepare_resources.sh` | 构建固定版本的 hactool，校验 NCA / BKTR / IVFC，生成 `work/romfs.bin`。密钥只传文件路径，不要把内容贴到任何地方 |
| `prepare_shaders.sh` | 获取固定版本的着色器工具（.NET SDK、SPIRV-Cross），再提取、配对、翻译并构建游戏着色器到 `work/shaders/` |
| `build_and_run.sh --load-only` | 构建 `work/build/Zelda-Mobile.app` 并检查游戏能否装载，不进入标题画面 |

目前仍有部分客体着色器请求不受支持。着色器准备失败时请查看 `work/shaders/build-failures.json`，不要忽略退出码。

构建后持续运行游戏：

```sh
DM_PRESENT_LINK=0 ZELDA_TIMEOUT_SECONDS=0 ./script/build_and_run.sh
```

不设置 `ZELDA_TIMEOUT_SECONDS` 时，构建脚本会在 120 秒后结束游戏；启动入口不限时。重新构建前务必先退出游戏。

## Mac：日常启动

```sh
./launch.command
# 或自动走游戏菜单，读取存档列表第一项：
./quick-load.command
```

两者都可以在 Finder 中双击。游戏已在运行时，启动入口只会把窗口切到前台。`quick-load.command` 走的是正常读档菜单（不是内存快照），存档列表变化后目的地也会变化。

`work/build/Zelda-Mobile.app` 依赖项目内的资源，不能单独移到别处。共享系统字体默认使用 macOS 自带的 AppleGothic.ttf；可用 `ZELDA_SHARED_FONT` 指定其他带 `glyf`/`loca` 表的 TrueType 字体（小于 17 MiB，不支持 CFF）。字体不随源码分发。

## Mac：可选的离线着色器库

Mac 和 iOS 构建默认在离线 Metal IR 库存在且校验通过时加载它们；缺失、过期或损坏时回退到 MSL 源码。如需生成，先在 Xcode 中安装 Metal Toolchain 组件（用 `xcrun --sdk macosx metal --version` 检查），退出游戏后运行：

```sh
mkdir -p work/tmp work/logs
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/work/tmp/" \
  work/venv/bin/python script/run_guarded.py --seconds 3600 --max-gib 1.25 \
  --report work/logs/shader-ir-build-guard.json -- \
  work/venv/bin/python script/build_shader_ir.py work/shaders/b-<chain> --jobs 2
```

把 `<chain>` 换成 `work/build/dmsh_chain.h` 中的值。生成过程可以中断后继续，不会覆盖 MSL 源码。缓存位于 `work/shaders/ir-macos/`；传给 iOS 的统一 Metal 库也沿用这个历史目录名。对照源码路径可用 `ZELDA_SHADER_IR=0` 启动；强制完整准备一轮可用 `ZELDA_SHADER_PREP_ALL=1`。

## iPhone / iPad：构建与安装

同一个应用同时支持 iPhone 和 iPad，架构为 arm64，最低部署版本为 iOS / iPadOS 26。构建时不需要连接设备；安装时开发签名的描述文件必须包含目标设备，新设备需要先在 Xcode 注册并更新签名。

先在 Mac 上完成准备（资源、模块、MSL、渲染堆，以及 `./script/prepare_shader_tools.sh`）。构建会链接设备端着色器编译器（构建过程中运行 `script/prepare_runtime_shaders.sh`），新的着色器请求在设备上翻译，不需要 Mac。`ios.sh transfer shaders` 也会传送翻译所需的着色器索引与代码。

```sh
# 使用你自己的团队和设备标识，不要写进源码。
export ZELDA_IOS_TEAM=YOUR_TEAM_ID
export ZELDA_IOS_DEVICE=YOUR_DEVICE_ID
work/venv/bin/python script/prepare_render_resources.py
work/venv/bin/python script/run_guarded.py --seconds 180 --max-gib 2 \
  --report work/logs/ios-build-guard.json -- ./script/ios.sh build
work/venv/bin/python script/run_guarded.py --seconds 90 --max-gib 0.75 \
  --report work/logs/ios-install-guard.json -- ./script/ios.sh install
```

`ZELDA_IOS_BUNDLE` 可覆盖开发用的 Bundle ID。资源传输使用独立的 Python 环境（已在 Python 3.15 验证）：

```sh
python3 -m venv work/ios/transfer-venv
TMPDIR="$PWD/work/tmp/" PIP_CACHE_DIR="$PWD/work/ios/pip-cache" \
  work/ios/transfer-venv/bin/python -m pip install -r requirements/ios-transfer.txt
./script/ios.sh transfer probe
./script/ios.sh transfer romfs
./script/ios.sh transfer shaders
./script/ios.sh transfer seed
./script/ios.sh transfer ready
./script/ios.sh launch
./script/ios.sh logs
```

如果已构建上面的离线 IR 缓存，在启动前执行 `./script/ios.sh transfer ir`。它只传对应缓存，保留源码和存档。完整缓存可让 iOS 跳过启动前的全库准备，实际加载每个库时仍检查身份和内容；缺失、过期或平台拒绝时回退源码。首次 IR 启动仍可能花时间创建设备管线。

每一步成功后再进行下一步；`ready` 必须在两类资源传输完成之后执行。默认逐个文件做完整内容校验；如需跳过内容哈希和回读，在 `romfs`、`shaders` 或 `ir` 命令后添加 `--no-verify`。此模式保留文件大小检查、按长度续传，并把回执标为未校验；它不能发现大小相同的数据损坏，也不适用于存档。设备上至少保留约 25 GB 可用空间。传输器会保留 2 GiB 安全余量，并拒绝覆盖内容不同的已有存档。

> [!WARNING]
> 不要用 `devicectl` 直接推送大型资源文件，这可能耗尽 Mac 的内存和内置磁盘。请始终使用 `script/ios.sh transfer`：它通过 AFC 分块传输、支持断点续传，默认逐个文件回读校验。

iOS 普通启动不会自动读档。每台设备保存独立的存档副本，不会回写 Mac。设备上首次准备着色器可能接近一小时；已测试的 M1 iPad 在缓存就绪后通常不到一分钟。iPhone 的准备耗时、内存余量和游戏性能仍需单独实测。

## 故障排查

| 现象 | 检查 |
| --- | --- |
| 模块指纹或版本不匹配 | 更新包必须是 v1.4.3；`local/games/` 里本体和更新包各保留一个 |
| 构建失败 | 完整日志在 `work/logs/build.log`，脚本只显示末尾 |
| 长时间黑屏 | 首次准备着色器；查看性能面板（⌘P）和 `work/logs/native-probe.log` |
| 磁盘空间不足 | 生成数据都在 `work/`。只删除可以重建的内容，不要删除 `work/save*` |
