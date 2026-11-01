# 开发指南

[English](development.md) | **简体中文**

先按 [构建与运行](getting-started.zh-CN.md) 准备环境，再通过 [源码地图](architecture.zh-CN.md) 找到要修改的代码。

## 开始一次修改

```sh
git status --short
mkdir -p work/tmp work/logs
export TMPDIR="$PWD/work/tmp/"
export PYTHONDONTWRITEBYTECODE=1
work/venv/bin/python tools/prepare_modules.py --check
```


构建前先退出游戏。构建、完整测试与运行时着色器编译共用生成目录，请依次执行。不要手工修改 `work/build`。

## 测试

| 目的 | 命令 |
| --- | --- |
| 仓库检查：文档链接、依赖清单、私有路径 | `python3 -m unittest discover -s tests -p test_repository.py -v` |
| 安装测试依赖 | `work/venv/bin/python -m pip install --no-cache-dir -r requirements/test.txt` |
| 完整的 Python、原生与 Metal 测试 | `./script/test.sh` |
| 只构建并检查装载 | `./script/build_and_run.sh --load-only` |
| 真实 SDK 的时钟、音频、字体与震动路径 | `./script/build_and_run.sh --time-test`（另有 `--codec-test`、`--font-test`、`--vibration-test`） |

先运行与修改相关的检查；改动共享图形、装载或线程时再跑原生与 Metal 测试。完整的 `test.sh` 需要准备好的本地环境和 Metal 设备，不是无需资源的 CI 任务。完整构建日志在 `work/logs/build.log`。

软件检查不能代替实际体验：屏幕上看到的画面、听到的声音和手柄震感都需要在真实设备上确认。

## 脚本输入

`ZELDA_INPUT_SCRIPT=<文件>` 会在着色器准备完成后执行输入脚本；`script/send_input.py` 可通过 FIFO 向运行中的游戏追加指令。脚本由逐行指令组成：

| 指令 | 含义 |
| --- | --- |
| `wait <毫秒>` | 等待 |
| `tap <按键> [毫秒]` / `down <按键>` / `up <按键>` | 轻按、按住或松开宿主键盘按键，名称与键盘映射一致 |
| `mark <文字>` | 在日志中写入标记 |
| `shot <路径>` | 保存截图 |
| `trace <秒> <路径>`、`dump <帧数>` | 记录帧时间线 / 转储图形命令 |
| `fps <30\|60\|90\|120>`、`render <50..200>` | 切换帧率目标 / 内部渲染倍率 |

`script/scenes/first-save.input` 和 `first-save-fast.input` 会走菜单读取第一个存档。脚本不知道你的存档列表，请核对实际到达的位置。不要用脚本推进剧情或存档。

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `ZELDA_TIMEOUT_SECONDS` | N 秒后结束游戏（0 为不限，最大 3600）。`build_and_run.sh` 默认 120，启动入口默认 0 |
| `ZELDA_SETTINGS` | 使用其他设置文件代替 `work/settings.json` |
| `ZELDA_TARGET_FPS`、`ZELDA_METALFX` | 本次运行覆盖已保存的帧率目标与 MetalFX 设置 |
| `ZELDA_SHARED_FONT` | 作为共享系统字体的 TrueType 字体 |
| `ZELDA_SHADER_IR=0`、`ZELDA_SHADER_PREP_ALL=1` | 忽略离线 Metal 库 / 强制完整准备一轮着色器 |
| `ZELDA_DAY_RATE` | 缩放游戏内昼夜时钟（0–16，0 为暂停），不影响帧率与物理 |
| `ZELDA_WINDOW_POINTS` | 窗口尺寸，例如 `1600x900` |
| `ZELDA_RENDER_SCALE` | 实验性渲染倍率（0.5–2），正常游玩时请清除 |
| `ZELDA_SHADER_ASYNC=0\|1`、`ZELDA_SHADER_ASYNC_BUDGET_MS` | 本次运行覆盖 Shaders 设置（1 = 后台，0 = 等待）；后台管线每帧最多等待的毫秒数，超过后先跳过对应绘制（默认 3） |
| `ZELDA_PSO_LOG=1` | 诊断：列出绘制时新建的每条管线与着色器库，以及超过 5 ms 的后台准备步骤 |
| `ZELDA_THREAD_CPU=1`、`ZELDA_IPC_LOG=1` | 诊断：逐线程 CPU 统计写入 `work/logs/threads.tsv`；IPC 日志 |

另有许多 `ZELDA_*` 和 `DM_*` 开关用于单项优化的 A/B 对照，默认值即已验证的行为；使用前请在 `host/` 中搜索 `getenv` 确认含义。

## 收录代码与依赖

- `core/` 是保持逐字节一致的收录代码：`script/prepare_graphics.py` 的替换锚点、着色器 chain 指纹和模块指纹都依赖它的原始字节。优先修改适配层。如必须修改 `core/` 文件，需同步更新锚点，在 `script/shader-chain-aliases.json` 中加入经证明的别名或重新生成着色器缓存，并重新运行 `prepare.sh`；见 [`core/README.zh-CN.md`](../core/README.zh-CN.md)。
- Python 依赖固定在 [`requirements/`](../requirements/)：`resources.txt` 引用 `runtime.txt`，`test.txt` 引用 `resources.txt`，`ios-transfer.txt` 是独立环境。
- 每处游戏补丁都必须绑定游戏版本、核对原字节，并保留清单校验。

## 基本原则

- **原画质、正常速度。** 优化不得降低分辨率、删减效果、跳过绘制或加速游戏。唯一的例外是玩家可选的 Shaders 设置（`host/shader_async.h`）：在 Background 模式下，绘制只在第一次用到的着色器或管线准备好之前被跳过。不能通过关闭校验或伪造成功值让测试通过。
- 源码、注释、界面文本、日志和文件名使用英文。文档以英文为主，并同步维护简体中文版本（`*.zh-CN.md`）。必要的非 ASCII 上游匹配串和 Unicode 测试字符用转义表示，`tests/test_repository.py` 会检查。
- 修改 `host/`、`ios/`、`script/`、`tools/` 中的源码，不要修改 `work/build` 中的生成文件。除非按上述步骤操作，否则保持 `core/` 不变。
- 不要提交游戏文件、密钥、存档、日志或构建产物。`local/` 和 `work/` 已被忽略，见 [仓库结构](repository.zh-CN.md)。不要在已准备好的检出里运行 `git clean`，它会删除你的本地数据。

## 参与贡献

1. 每个提交只做一件事，并写清楚提交说明。用 `git diff --cached --check` 和 `git diff --cached --stat` 检查暂存内容。
2. 新行为要附带测试。图形改动尽量提供 Metal 回读测试。
3. 说明验证了什么、怎样验证（单元测试、构建、画面检查、真机），以及哪些没有验证。
4. 同时更新英文和中文文档。

贡献的代码按本仓库的 [Apache License 2.0](../LICENSE) 授权接收。
