# 源码地图

[English](architecture.md) | **简体中文**

## 工作原理

游戏的 ARM64 模块不经模拟执行。它们会被预先改写，使系统调用和少数平台相关指令跳入宿主代码，再装入宿主进程原生运行。宿主实现游戏所需的内核、IPC 服务、文件系统、音频、输入与视频。图形经过共享的 NVN → Metal 后端；客体着色器会预先翻译为 Metal Shading Language。

Mac 与 iOS 共用客体服务和 Metal 后端。平台前端分别是 `host/graphics.m`（AppKit）与 `ios/main.m`（UIKit）。

```text
你的 .nsz ─ tools/extract_exefs.py ─▶ work/exefs
                         │ tools/prepare_modules.py（ARM64 改写、绑定版本的补丁）
                         ▼
                    work/patched
core/ + script/prepare_graphics.py + host/nvn_* ─▶ work/build
                         │ Mac 构建（script/build_and_run.sh）/ iOS CMake（script/ios.sh）
                         ▼
          Zelda-Mobile.app（Mac）/ ZeldaMobile.app（iOS）

RomFS ─ script/extract_shaders.py → 索引 → 配对 → 翻译 → 构建 ─▶ work/shaders（MSL 与反射）
```

`core/` 收录本项目所基于的装载器、ARM64 改写器、NVN → Metal 后端与着色器翻译前端。它们来自参考项目 [d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile)，与导入时的版本保持逐字节一致；Zelda 专属改动在构建时通过生成文件和 Clang VFS overlay 应用。见 [`core/README.zh-CN.md`](../core/README.zh-CN.md)。

## 修改入口

表中不带目录的 `nvn_*` 位于 `host/`，`test_*` 位于 `tests/`。

| 领域 | 入口 | 测试 |
| --- | --- | --- |
| ARM64 改写、装载与版本绑定 | `tools/patch_module.py`、`tools/prepare_modules.py`、`host/probe.c` | `test_calls.py`、`test_exclusive.py`、`test_local_images.py` |
| 时间步与渲染尺寸补丁 | `tools/game_timing.py`、`tools/render_scale.py`、`host/game_timing.c` | `test_game_timing.py`、`test_render_scale.py` |
| 内核、线程、IPC、文件系统 | `host/kernel.c`、`threads.c`、`services.c`、`filesystem.c` | 对应的 `test_*.c` |
| Mac 窗口、设置、控制面板 | `host/graphics.m`、`control_panel.inc`、`settings.m`、`display.c` | `test_settings.m`、`test_display.c`、`test_launcher.py` |
| iOS 前端与签名构建 | `ios/main.m`、`ios/CMakeLists.txt`、`script/ios.sh` | 真机检查 |
| 键盘、手柄与震动 | `host/input.c`、`gamepad.m`、`vibration.c` | `test_vibration.c`、`test_gamepad_haptics.m` |
| 音频与电影 | `host/audio*.c`、`codec.c`、`movie.c`、`movie_video.c` | `test_audio.c`、`test_movie*.c`、真实 SDK 测试 |
| 图形生成与接线 | `script/prepare_graphics.py`、`host/nvn_*.inc` / `.h` | `test_graphics.py` 与 Metal 回读测试 |
| 条件绘制、通道保留与依赖 | `nvn_conditional_draw.inc`、`nvn_inline_uniform.inc`、`nvn_submit.inc`、`nvn_query_gpu.inc` | 对应的 `test_nvn_*.m` |
| 纹理、驻留、常量与快照 | `nvn_texture_*.inc`、`nvn_residency.inc`、`nvn_uniform_snapshot.inc`、`nvn_buffer_cache.inc` | 别名、失效、栅栏与 GPU 回读测试 |
| 着色器提取、配对、翻译与预热 | `script/{extract_shaders,index_shaders,shader_pairs,translate_shaders,build_shaders}.py`、`host/shader_prep.m`、`nvn_shader_warm.inc` | `test_shader*.py`、`test_shader_prep.m` |
| Mac 离线 Metal 库 | `script/build_shader_ir.py`、`host/shader_ir.inc` | `test_shader_ir.py` / `.m` |
| 预热状态与性能面板 | `host/shader_status.h`、`performance.c`、`performance_overlay.inc` | `test_performance.c` |
| 资源准备与 USB 传输 | `script/extract_romfs.py`、`prepare_render_resources.py`、`ios_transfer.py` | `test_resources.py`、`test_render_resources.py`、`test_ios_transfer.py` |
| 统计、脚本输入与资源守卫 | `host/performance.c`、`timeline.c`、`input_script.c`、`script/run_guarded.py` | `test_performance.c`、`test_scene_tools.py`、`test_run_guarded.py` |

## 生成文件规则

- `work/build/{hle_nv.c,hle_nvn.c,nvn_mtl.m,graphics-overlay.json}` 是生成物，手工修改会在下一次准备时丢失。请修改 `script/prepare_graphics.py` 或 `host/nvn_*` 源码。
- `prepare_graphics.py` 的每个替换锚点必须恰好命中一次。保留重复键检查，并核对运行时命中计数。
- 修改着色器翻译规则会改变 chain 指纹。需检查反射、宿主绑定与 GPU 回读，不要混用旧缓存和新规则。`work/shaders/b-*/*.metallib` 实际是 MSL 文本，真正的二进制库在 `ir-macos/` 下。
- 离线 IR 编译契约覆盖目标、语言与严格浮点选项以及共享源码变换；`prepare_graphics.py` 生成 `shader_ir_identity.h`。每次加载都校验 MSL 与二进制的 SHA-256，不一致时回退。
- `script/shader-chain-aliases.json` 只收录已证明不改变生成库的精确指纹别名，不能用它跳过真实的着色器变更。
- iOS 复用 MSL、反射与管线描述，在设备上构建 Metal 库。新的着色器请求由 `ios/shaders/` 在设备上翻译：同一个翻译器以 NativeAOT 编译，链接 SPIRV-Cross 的命令行，并把 Python 的库检查移植为 C#。`tests/runtime_shaders/` 用它重建全部已准备的着色器并与 Python 管线的结果比对；修改任一侧后都要运行。
