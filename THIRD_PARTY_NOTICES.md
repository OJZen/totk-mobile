# Third-party notices

**English** | [简体中文](THIRD_PARTY_NOTICES.zh-CN.md)

## d2r-mobile

d2r-mobile is the reference project of Zelda-Mobile. The files this project builds on were imported unmodified into `core/` from commit `b94670c795e28742abe1a9cadd64a234a77260ab` (see `core/README.md`): `core/tools/patch_nso.py`, `core/host/loader.c`, `core/host/entry.S` and the shared headers. `host/threads.c` directly includes and adapts the pthread, event, wait and synchronization implementation of `core/host/hle_thread.c`. The adapter and tests reuse its ARM64 encoder and thread-slot conventions; the address-space layout and stack mapping in `host/kernel.c` follow its `hle_svc.c`. The input fingerprints of the rewrite are stored locally in `work/patched/build.json`. iOS also uses `core/ios/tools/gen_embed.py` to generate the signed module segments. Paths below starting with `dm/` name files of the d2r-mobile project that served as references only and are not part of this repository.

Source: [jhaizhou-ops/d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile). The following license applies to these reused parts:

Routing Opus hardware requests to the SDK software decoder follows `dm/host/hle_codec.c`, adapted to the C interface of the current SDK.

The CoreAudio default output and lock-free audio queue follow `dm/host/hle_audio.c`, keeping its acquire/release publication order and keeping game IPC out of the callback; Zelda's AudioRenderer decoding, mixing and replies are adapted separately to its protocol.

Taking over the pre-rendered movie `movie::Decoder` follows `dm/host/hle_movie.c`: its vtable slots, status codes (0x1008, no buffer yet) and creating a guest thread through the SDK's `nn::os::CreateThread` to signal events. The reference only waits out the movie length without decoding; this project actually decodes VP9 and delivers NV12 frames according to the game's protocol.

The Xbox/Nintendo face-button mapping reuses `dm/host/dm_padmap.h`; controller support follows `dm/ios/Sources/dm_gamepad.m`. Performance statistics reuse the graphics backend's presentation receipts and GPU and submission CPU timing, and tuning follows its item-by-item comparison and Metal validation workflow.

The graphics backend also reuses the NVN-to-Metal adaptation, command execution and reflection in `core/gfx/nvn/`, and the texture decoding and layout conversion in `core/gfx/tex/`; the dirty-page management in `host/nvn_dirty.c` follows `dm/gfx/nvn/nvn_dirty.c`. This project's adaptations are applied at build time through a Clang file overlay; the files in `core/` are not rewritten. The NVN headers keep the MIT notice `Copyright (c) 2020 h1k421`; the BC decoder is `core/third_party/bcdec/` under its MIT option, `Copyright (c) 2022 Sergii Kudlai`. Both MIT license texts match the one below, and the original notices stay with the referenced sources. The public Tegra block-linear layout format follows deko3d and Ryujinx. The Depth16 load-clear correction in `host/nvn_depth16_clear.h` is ported from `nvn_depth16_clear_value` of [seedds/nxnative](https://github.com/seedds/nxnative) (commit `a5513d4`), MIT licensed, `Copyright (c) 2026 seedds`; the license text is the same as below.

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

## Resource preparation tools

The time fields, cutscene call sites and menu conditions used by the 60 FPS adaptation were identified from [masagrator's FPSLocker v1.4.3 configuration](https://github.com/masagrator/FPSLocker-Warehouse/blob/v4/SaltySD/plugins/FPSLocker/patches/0100F2C0115B6000/277178B7DBA1B6D4.yaml) and checked against the original bytes of the local modules. This project wrote its own stubs for native loading, the 19.2 MHz clock and the Apple ABI, and does not distribute that repository's game programs or NPDM; the configuration's dynamic-resolution changes were not adopted.

`script/prepare_resources.sh` builds [SciresM/hactool](https://github.com/SciresM/hactool) at the pinned commit `1d64a83450e025622f3468c28fc4164dad2c5ef6`, using `tools/hactool.patch` to add an explicit local title-key file path and BKTR support with a metadata verification region. The patch reuses hactool's own AES, SHA-256 and IVFC verification functions; the format follows [Atmosphère's NCA definitions](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libstratosphere/include/stratosphere/fssystem/fssystem_nca_header.hpp) without copying its implementation. The build directory is cleaned up automatically; the binary, source commit and file hashes are stored in `work/tools/`, together with hactool's ISC license and the Apache 2.0 license of its bundled mbedTLS. The upstream code touched by the patch is under the following license:

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

NCZ section restoration follows the [NSZ format description](https://github.com/nicoboss/nsz/blob/master/docs/formats.md) without copying the NSZ implementation. AES is used through the Python dependency `cryptography`; its version is pinned in `requirements/resources.txt` and its license ships with the installed package. Game resources and keys are not part of this project's source and are not distributed with it.

## Shader and audio tools

The shader converter reuses the `dmsh_xlate` front end in `core/gfx/tools/dmsh_xlate/` and the Ryujinx shader translation subset in `core/third_party/ryujinx/`. Ryujinx is MIT licensed, `Copyright (c) Ryujinx Team and Contributors`; the license text is the same as above, and the original file is `core/third_party/ryujinx/LICENSE.txt`. Its `Spv.Generator/LICENSE` is also kept with the source.

MSL conversion uses [SPIRV-Cross](https://github.com/KhronosGroup/SPIRV-Cross) at commit `aa217aeb6c9f0ace7a0ab233b28807edf45eb165`; its Apache 2.0 license is saved as `work/tools/spirv-cross.LICENSE`. The license and third-party notices of the local .NET SDK stay in its install directory `work/tools/dotnet/`. These tools are prepared by `script/prepare_shader_tools.sh`; no binaries are bundled with this project's source.

`tests/fixtures/invariant_math.metal` keeps floating-point helper functions generated by SPIRV-Cross for real GPU numeric comparisons. The generating logic comes from `spirv_msl.cpp`, `Copyright (c) 2016-2021 The Brenwill Workshop Ltd.`, under its MIT option; the license text is the same as above.

The audio adaptation links the locally installed [libopus](https://opus-codec.org/) (currently validated with 1.6.1); its BSD license is the `COPYING` file in the Homebrew install directory. The audio service message layouts follow the public format definitions of libnx, Switchbrew and Ryujinx. The Reverb3D delay lines, parameters and reflection tables, and the limiter and auxiliary buffer algorithms follow and adapt the implementation in [Ryujinx.Audio](https://github.com/NextendoNetwork/Ryujinx-Nextendo/tree/main/src/Ryujinx.Audio/Renderer), under the MIT license, `Copyright (c) Ryujinx Team and Contributors`; the license text is the same as above.

## libvpx

`host/movie_video.c` uses libvpx to decode the VP8/VP9 pre-rendered movies (the macOS build links the local library through `pkg-config vpx`; iOS and other targets build the same library for the target platform). Source: [webmproject/libvpx](https://chromium.googlesource.com/webm/libvpx), BSD 3-Clause license:

Copyright (c) 2010, The WebM Project authors. All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

- Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
- Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
- Neither the name of Google, nor the WebM Project, nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## iOS build and device transfer

`script/build_ios_deps.sh` cross-compiles Opus 1.6.1 and libvpx 1.17.0 for devices from source archives with pinned SHA-256; the sources and licenses are kept in `work/ios/deps/`, and the macOS Homebrew binaries are not linked.

On the Mac, `script/ios_transfer.py` uses the USB / House Arrest / AFC interfaces of [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) 11.34.1, whose package metadata declares `GPL-3.0-or-later`; its license ships with the package in the separate virtual environment. It is used only for transfers from the development machine and is not linked into the iOS app.
