# 第三方来源与许可

[English](THIRD_PARTY_NOTICES.md) | **简体中文**

## d2r-mobile

d2r-mobile 是 Zelda-Mobile 的参考项目。本项目所基于的文件已从其提交 `b94670c795e28742abe1a9cadd64a234a77260ab` 原样收录到 `core/`（见 `core/README.zh-CN.md`）：`core/tools/patch_nso.py`、`core/host/loader.c`、`core/host/entry.S` 及共享头文件。`host/threads.c` 直接包含并适配 `core/host/hle_thread.c` 的 pthread、事件、等待与同步实现。适配器和测试复用其 ARM64 编码器与线程槽约定；`host/kernel.c` 的地址空间划分及栈映射参考其 `hle_svc.c`。改写流程的输入指纹保存在本机 `work/patched/build.json`。iOS 另使用 `core/ios/tools/gen_embed.py` 生成签名模块段。下文以 `dm/` 开头的路径指 d2r-mobile 项目中仅作参考、未收录进本仓库的文件。

来源：[jhaizhou-ops/d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile)。许可如下，适用于这些复用部分：

Opus 硬件请求转到 SDK 软件解码器的做法参考 `dm/host/hle_codec.c`；本项目按当前 SDK 的 C 接口适配。

CoreAudio 默认输出和无锁音频队列参考 `dm/host/hle_audio.c`，沿用 acquire/release 发布顺序及回调不承担游戏 IPC 的做法；Zelda 的 AudioRenderer 解码、混音和回执按其协议单独适配。

预渲染过场的 `movie::Decoder` 接管参考 `dm/host/hle_movie.c`：沿用其虚表槽位、状态码（暂无缓冲 0x1008）以及经 SDK `nn::os::CreateThread` 建客体线程发事件的做法；D2R 只按时长空播，本项目改为实际解码 VP9 并按游戏协议交付 NV12 帧。

Xbox/Nintendo 面键映射复用 `dm/host/dm_padmap.h`；手柄接入参考 `dm/ios/Sources/dm_gamepad.m`。性能统计复用图形后端的实际呈现回执、GPU 与提交 CPU 计时，调优沿用其逐项对照和 Metal 校验流程。

图形后端还复用 `core/gfx/nvn/` 的 NVN 到 Metal 适配、命令执行与反射，以及 `core/gfx/tex/` 的纹理解码和布局转换；`host/nvn_dirty.c` 的脏页管理参考 `dm/gfx/nvn/nvn_dirty.c`。构建时通过 Clang 文件覆盖应用本项目的适配；不改写 `core/` 中的文件。NVN 头文件保留 `Copyright (c) 2020 h1k421` 的 MIT 声明；BC 解码器为 `core/third_party/bcdec/`，选用 MIT 许可，`Copyright (c) 2022 Sergii Kudlai`。上述两项 MIT 许可文本与下文一致，原始声明随被引用源码保留。Tegra 块线性布局的公开格式参考 deko3d 和 Ryujinx。`host/nvn_depth16_clear.h` 的 Depth16 加载清除修正移植自 [seedds/nxnative](https://github.com/seedds/nxnative)（提交 `a5513d4`）的 `nvn_depth16_clear_value`，MIT 许可，`Copyright (c) 2026 seedds`；许可文本与下文一致。

MIT License

Copyright (c) 2026 jhaizhou

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## 资源准备工具

60 FPS 适配的时间字段、过场调用点与菜单条件依据 [masagrator 的 FPSLocker v1.4.3 配置](https://github.com/masagrator/FPSLocker-Warehouse/blob/v4/SaltySD/plugins/FPSLocker/patches/0100F2C0115B6000/277178B7DBA1B6D4.yaml) 识别，并与本机模块原字节核对。本项目编写了适用于原生装载、19.2 MHz 时钟和 Apple ABI 的桩，不分发该仓库的游戏程序或 NPDM；配置中的动态分辨率参数修改未采用。

`script/prepare_resources.sh` 从 [SciresM/hactool](https://github.com/SciresM/hactool) 固定提交 `1d64a83450e025622f3468c28fc4164dad2c5ef6` 构建工具，使用 `tools/hactool.patch` 增加显式的本地 title-key 文件路径，以及带元数据校验区的 BKTR 支持。补丁复用 hactool 自身的 AES、SHA-256 和 IVFC 校验函数；格式依据 [Atmosphère 的 NCA 定义](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libstratosphere/include/stratosphere/fssystem/fssystem_nca_header.hpp)，未复制其实现。构建目录自动清理，二进制、源码提交和文件哈希保存在 `work/tools/`；其中同时保存 hactool 的 ISC 许可和其捆绑 mbedTLS 的 Apache 2.0 许可。补丁涉及的上游代码适用以下许可：

ISC License

Copyright (c) 2018, SciresM

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

NCZ 分段还原依据 [NSZ 格式说明](https://github.com/nicoboss/nsz/blob/master/docs/formats.md)，未复制 NSZ 实现。AES 通过 Python 依赖 `cryptography` 使用；依赖版本锁定在 `requirements/resources.txt`，其许可随安装包保留。游戏资源和密钥不属于本项目源码，也不随源码分发。

## 着色器与音频工具

着色器转换器复用 `core/gfx/tools/dmsh_xlate/` 的 `dmsh_xlate` 前端与 `core/third_party/ryujinx/` 的 Ryujinx 着色器翻译子集。Ryujinx 使用 MIT 许可，`Copyright (c) Ryujinx Team and Contributors`；许可文本同上，原文件为 `core/third_party/ryujinx/LICENSE.txt`。其 `Spv.Generator/LICENSE` 也随源码保留。

MSL 转换使用 [SPIRV-Cross](https://github.com/KhronosGroup/SPIRV-Cross) 提交 `aa217aeb6c9f0ace7a0ab233b28807edf45eb165`，Apache 2.0 许可保存在 `work/tools/spirv-cross.LICENSE`。本地 .NET SDK 的许可和第三方声明保留在安装目录 `work/tools/dotnet/`。这些工具由 `script/prepare_shader_tools.sh` 准备；不随本项目源码捆绑二进制。

`tests/fixtures/invariant_math.metal` 保留 SPIRV-Cross 生成的浮点辅助函数，用于真实 GPU 数值比对。对应生成逻辑来自 `spirv_msl.cpp`，`Copyright (c) 2016-2021 The Brenwill Workshop Ltd.`，选用其 MIT 许可，许可文本同上。

音频适配链接本机已安装的 [libopus](https://opus-codec.org/)（当前验证版本 1.6.1），其 BSD 许可保留在 Homebrew 安装目录的 `COPYING`。音频服务消息布局依据 libnx、Switchbrew 和 Ryujinx 的公开格式定义。Reverb3D 的延迟线、参数与反射表，以及限幅和辅助缓冲算法参考并适配 [Ryujinx.Audio](https://github.com/NextendoNetwork/Ryujinx-Nextendo/tree/main/src/Ryujinx.Audio/Renderer) 的实现，适用 MIT 许可，`Copyright (c) Ryujinx Team and Contributors`，许可文本同上。

## libvpx

`host/movie_video.c` 用 libvpx 解码预渲染过场的 VP8/VP9（macOS 构建经 `pkg-config vpx` 链接本机库，iOS / Android 需按目标平台编译同一库）。来源：[webmproject/libvpx](https://chromium.googlesource.com/webm/libvpx)，BSD 3-Clause 许可：

Copyright (c) 2010, The WebM Project authors. All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

- Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
- Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
- Neither the name of Google, nor the WebM Project, nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## iOS 构建与设备传输

`script/build_ios_deps.sh` 为真机交叉编译 Opus 1.6.1 和 libvpx 1.17.0，固定源码归档 SHA-256；源码与许可证保留在 `work/ios/deps/`，不链接 macOS 的 Homebrew 二进制。

Mac 端 `script/ios_transfer.py` 使用 [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) 11.34.1 的 USB / House Arrest / AFC 接口，包元数据声明 `GPL-3.0-or-later`；许可证随独立虚拟环境的安装包保留。它只用于开发机传输，不链接进 iOS 应用。
