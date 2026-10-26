# 操作与设置

[English](controls.md) | **简体中文**

## Mac 键盘

| 操作 | 按键 |
| --- | --- |
| 移动 / 镜头 | W A S D / I J K L |
| A / B / X / Y | X / Z / R / F（X 确认，Z 返回） |
| 十字键 | 方向键 |
| L / R、ZL / ZR | Q / E、Shift / 空格 |
| 道具菜单（+）/ 地图（−） | 回车 / Tab |
| 左 / 右摇杆按下 | C / V |
| 控制面板 / 性能面板 | ⌘, / ⌘P |
| 全屏 | ⌃⌘F |

## Xbox 手柄

游戏窗口在前台时，Xbox 手柄经蓝牙或 USB 连接后自动接入。Menu（≡）为 +，View 为 −。摇杆有偏转时优先于按住的键盘按键。震动使用针对 Xbox 手柄校准的曲线。

## 控制面板

用 ⌘, 或菜单 **View → Controls** 打开。设置保存在 `work/settings.json`（可用 `ZELDA_SETTINGS` 指定其他路径）。

| 设置 | 选项 | 说明 |
| --- | --- | --- |
| Frame rate | 30 / 60 / 90 FPS | 默认 60。目标不代表一定达到；预渲染电影仍按 30 播放 |
| Resolution | Original / 1080 / 720 / 640 | **Original**（原版）保留游戏自己的动态分辨率；固定档把场景渲染为 1920×1080、1280×720 或 1136×640 |
| MetalFX | Off / Window / 4K | 只对最终画面做空间放大：**Window** 放大到窗口像素尺寸，**4K** 放大到 3840×2160 后再适配窗口。两者都只在窗口像素多于游戏画面时生效 |
| Fullscreen、Sound、Performance | Off / On | Performance 显示性能面板 |
| Rumble | Off / On | 关闭后立即停止震动，再开启不会重放旧的震动；**Test** 只检查宿主输出 |

旧的设置文件会自动迁移：`896p`/`balanced` 变为原版，`performance` 变为 720，已删除的 `480p` 变为 640，旧的 MetalFX 开启变为 Window，旧的震动强度都视为开。未知字段会保留。

## 启动画面

在游戏能够不卡顿地绘制之前，启动画面会覆盖窗口，显示一条只前进不后退的进度条和当前阶段：检查着色器库、编译缺少的库（首次启动时较久）、启动游戏，然后加载着色器并构建以往运行记录下的管线。有计数的阶段会显示计数，例如 `Building pipelines 4,000 / 8,452`。预热结束后画面淡出，进入游戏。

## 性能面板

性能面板（⌘P）显示 FPS 与目标、渲染尺寸、CPU、GPU 耗时和内存。右上角显示着色器预热状态：`Checking shaders`、`Shaders N%`、`Pipelines N%`；总数尚未确定时显示 `Warming up`；游戏中新建着色器库或管线时显示 `Compiling shaders`。百分比只对应当前阶段。

## iPad

未连接实体手柄时显示系统触控手柄，顶部的 + 和 − 打开对应菜单。性能面板显示 FPS 与目标、渲染尺寸、应用 CPU、GPU 耗时和物理内存，点面板顶部可以收起。游戏在前台时暂停自动锁屏。设置保存在应用的 `Documents/work/settings.json`，与 Mac 分开；iOS 上暂时没有完整的设置面板。

## amiibo（Mac，实验功能）

本功能目前仅提供 macOS 操作入口。iPhone 和 iPad 尚无 amiibo 文件选择或扫描控件；共用底层服务代码并不代表 iOS 已支持扫描。

在游戏选项中启用 amiibo，然后按住 Q（手柄 L）打开能力轮盘，用 I/J/K/L（右摇杆）选择 amiibo，松开 Q 后再按一次发动能力。游戏等待扫描时，打开菜单 **amiibo > Scan amiibo File...**（Command-Shift-A），选择原始 NTAG215 `.bin` 文件（532、540 或 572 字节）。在游戏开始检测前选中的文件会等待下一次扫描。使用 **Remove amiibo** 移除标签；再次扫描需重新选择文件。

虚拟读卡器只导入公开的 UID 和型号信息，保留原始 UID，每日限制仍由游戏处理。不会写入原文件，也不会在下次启动时自动加载上次选择。程序检查文件长度、UID 校验字节和公开头部，不验证加密数据的真实性。暂不支持加密的注册信息、应用数据、写入或实体 NFC 读卡器。仅支持 ROM 挂载，请求 RAM 会返回错误。已在准备好的 v1.4.3 游戏中确认可以扫描；尚未验证所有 amiibo 的兼容性。

若选中文件后提示“waiting for the game”，表示文件已加载，但游戏尚未开始扫描。关闭提示并在游戏内实际发动 amiibo 能力。NFC 请求与结果会记录到 `work/logs/native-probe.log`；设置 `ZELDA_AMIIBO_TRACE=1` 可额外记录状态轮询。
