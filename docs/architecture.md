# Source map

**English** | [简体中文](architecture.zh-CN.md)

## How it works

The game's ARM64 modules are not emulated. They are rewritten ahead of time so that system calls and a few platform-specific instructions jump into host code, then loaded into the host process and run natively. The host implements the kernel, IPC services, file system, audio, input and video that the game expects. Graphics go through a shared NVN → Metal backend; guest shaders are translated to Metal Shading Language ahead of time.

The Mac and iOS builds share the guest services and the Metal backend. The platform front ends are `host/graphics.m` (AppKit) and `ios/main.m` (UIKit).

```text
your .nsz ─ tools/extract_exefs.py ─▶ work/exefs
                         │ tools/prepare_modules.py (ARM64 rewrite, version-bound patches)
                         ▼
                    work/patched
core/ + script/prepare_graphics.py + host/nvn_* ─▶ work/build
                         │ Mac build (script/build_and_run.sh) / iOS CMake (script/ios.sh)
                         ▼
          Zelda-Mobile.app (Mac)  /  ZeldaMobile.app (iOS)

RomFS ─ script/extract_shaders.py → index → pair → translate → build ─▶ work/shaders (MSL + reflection)
```

`core/` holds the loader, the ARM64 rewriter, the NVN → Metal backend and the shader translator front end that this project builds on. They come from the reference project [d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile) and are kept byte-identical to the imported revision; Zelda-specific changes are applied at build time through generated files and a Clang VFS overlay. See [`core/README.md`](../core/README.md).

## Where to make changes

Unprefixed `nvn_*` files live in `host/`; `test_*` files live in `tests/`.

| Area | Entry points | Tests |
| --- | --- | --- |
| ARM64 rewrite, loading, version binding | `tools/patch_module.py`, `tools/prepare_modules.py`, `host/probe.c` | `test_calls.py`, `test_exclusive.py`, `test_local_images.py` |
| Time step and render size patches | `tools/game_timing.py`, `tools/render_scale.py`, `host/game_timing.c` | `test_game_timing.py`, `test_render_scale.py` |
| Kernel, threads, IPC, file system | `host/kernel.c`, `threads.c`, `services.c`, `filesystem.c` | matching `test_*.c` |
| Mac window, settings, control panel | `host/graphics.m`, `control_panel.inc`, `settings.m`, `display.c` | `test_settings.m`, `test_display.c`, `test_launcher.py` |
| iOS front end and signed build | `ios/main.m`, `ios/CMakeLists.txt`, `script/ios.sh` | device checks |
| Keyboard, controllers, rumble | `host/input.c`, `gamepad.m`, `vibration.c` | `test_vibration.c`, `test_gamepad_haptics.m` |
| Audio and movies | `host/audio*.c`, `codec.c`, `movie.c`, `movie_video.c` | `test_audio.c`, `test_movie*.c`, SDK tests |
| Graphics generation and wiring | `script/prepare_graphics.py`, `host/nvn_*.inc` / `.h` | `test_graphics.py` and Metal read-back tests |
| Conditional draws, pass reuse, dependencies | `nvn_conditional_draw.inc`, `nvn_inline_uniform.inc`, `nvn_submit.inc`, `nvn_query_gpu.inc` | matching `test_nvn_*.m` |
| Textures, residency, constants, snapshots | `nvn_texture_*.inc`, `nvn_residency.inc`, `nvn_uniform_snapshot.inc`, `nvn_buffer_cache.inc` | alias, invalidation, fence and GPU read-back tests |
| Shader extraction, pairing, translation, warm-up | `script/{extract_shaders,index_shaders,shader_pairs,translate_shaders,build_shaders}.py`, `host/shader_prep.m`, `nvn_shader_warm.inc` | `test_shader*.py`, `test_shader_prep.m` |
| Mac offline Metal libraries | `script/build_shader_ir.py`, `host/shader_ir.inc` | `test_shader_ir.py` / `.m` |
| Warm-up status and overlay | `host/shader_status.h`, `performance.c`, `performance_overlay.inc` | `test_performance.c` |
| Resource preparation and USB transfer | `script/extract_romfs.py`, `prepare_render_resources.py`, `ios_transfer.py` | `test_resources.py`, `test_render_resources.py`, `test_ios_transfer.py` |
| Statistics, scripted input, resource guard | `host/performance.c`, `timeline.c`, `input_script.c`, `script/run_guarded.py` | `test_performance.c`, `test_scene_tools.py`, `test_run_guarded.py` |

## Rules for generated files

- `work/build/{hle_nv.c,hle_nvn.c,nvn_mtl.m,graphics-overlay.json}` are generated. Hand edits are lost the next time the build prepares them. Change `script/prepare_graphics.py` or the `host/nvn_*` sources instead.
- Every replacement anchor in `prepare_graphics.py` must match exactly once. Keep the duplicate-key check and verify the runtime hit counts.
- Shader translation changes alter the chain fingerprint. Check reflection, host bindings and GPU read-back, and never mix old caches with new rules. The `work/shaders/b-*/*.metallib` files are MSL text; real binary libraries live under `ir-macos/`.
- The offline IR contract covers the target, language and strict floating-point options and the shared source transforms; `prepare_graphics.py` generates `shader_ir_identity.h`. Every load verifies the SHA-256 of both the MSL and the binary and falls back on mismatch.
- `script/shader-chain-aliases.json` lists only exact fingerprint aliases proven not to change the generated libraries. Do not use it to skip a real shader change.
- iOS reuses the MSL, reflection and pipeline descriptions and builds the Metal libraries on the device. New shader requests are translated on the device by `ios/shaders/`: the same translator compiled with NativeAOT, SPIRV-Cross's command line linked in, and a C# port of the Python library checks. `tests/runtime_shaders/` rebuilds every prepared shader with it and compares the result with the Python pipeline; run it after changing either side.
