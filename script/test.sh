#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p work/tmp
export TMPDIR="$PWD/work/tmp/"
export PYTHONDONTWRITEBYTECODE=1
trap 'rm -f work/tmp/test_nvn_texture_swizzle work/tmp/test_nvn_point_params work/tmp/test_nvn_shader_warm work/tmp/test_trace_writer work/tmp/trace-writer-test.tsv work/tmp/test_nvn_encoder_state work/tmp/test_nvn_texture_writeback work/tmp/test_nvn_depth_output work/tmp/test_nvn_fullscreen_compute work/tmp/test_nvn_pass_writes work/tmp/test_settings work/tmp/test_settings.json work/tmp/test_font_watch work/tmp/test_vibration work/tmp/test_gamepad_haptics work/tmp/test_movie work/tmp/test_movie_video work/tmp/test_shader_prep work/tmp/test_relr work/tmp/test_kernel work/tmp/test_services work/tmp/test_audio work/tmp/test_threads work/tmp/test_display work/tmp/test_performance work/tmp/test_nvn_objects work/tmp/test_nvn_index work/tmp/test_nvn_index_cache work/tmp/test_nvn_uniform_snapshot work/tmp/test_nvn_inline_uniform work/tmp/test_nvn_cube_pass work/tmp/test_nvn_wide_line work/tmp/test_nvn_conditional_draw work/tmp/test_nvn_framebuffer_fetch work/tmp/test_nvn_color_clear work/tmp/test_nvn_pointer_storage work/tmp/test_nvn_query work/tmp/test_nvn_depth work/tmp/test_nvn_profile work/tmp/test_nvn_profile.tsv work/tmp/test_nvn_render_view work/tmp/test_nvn_math work/tmp/test_nvn_residency work/tmp/test_nvn_c1_storage work/tmp/test_nvn_small_texture work/tmp/test_nvn_dirty work/tmp/test_nvn_pool_index work/tmp/test_nvn_upload_pool work/tmp/test_nvn_texture_reuse work/tmp/test_nvn_texture_alias work/tmp/test_filesystem work/tmp/point-size-test.metal; rm -rf work/tmp/test_audio.dSYM work/tmp/test_nvn_inline_uniform.dSYM' EXIT
clang -O1 -Wall -Wextra -Werror -fsanitize=address,undefined tests/test_trace_writer.c -o work/tmp/test_trace_writer
work/tmp/test_trace_writer
rm -f work/tmp/test_trace_writer
clang -Wall -Wextra -Werror -fobjc-arc -fsanitize=address,undefined tests/test_nvn_encoder_state.m \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_encoder_state
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_encoder_state
rm -f work/tmp/test_nvn_encoder_state
work/venv/bin/python script/prepare_graphics.py
clang -O2 -Wall -Wextra -Werror -fobjc-arc -I "$PWD/core/gfx/tex" tests/test_nvn_texture_swizzle.m \
    "$PWD/core/gfx/tex/nvn_fmt_mtl.c" -framework Foundation -framework Metal -o work/tmp/test_nvn_texture_swizzle
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_texture_swizzle
rm -f work/tmp/test_nvn_texture_swizzle
work/venv/bin/python - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, 'script')
from build_shaders import point_size_uniform
source = '''#include <metal_stdlib>
using namespace metal;
struct Result { float4 position [[position]]; float gl_PointSize [[point_size]]; };
vertex Result vs() { Result out; out.position = float4(0, 0, 0.5, 1); out.gl_PointSize = 1.0; return out; }
fragment void fs(device atomic_uint *n [[buffer(0)]]) { atomic_fetch_add_explicit(n, 1u, memory_order_relaxed); }
'''
Path('work/tmp/point-size-test.metal').write_text(point_size_uniform(source))
PY
clang -Wall -Wextra -Werror -fsanitize=address,undefined -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx" -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_objects.c \
    "$PWD/core/gfx/nvn/nvn_tex.c" "$PWD/core/gfx/tex/nvn_deswizzle.c" -o work/tmp/test_nvn_objects
work/tmp/test_nvn_objects
clang -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_index.m -framework Foundation -framework Metal -o work/tmp/test_nvn_index
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_index
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_point_params.m -framework Foundation -framework Metal -o work/tmp/test_nvn_point_params
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_point_params
rm -f work/tmp/test_nvn_point_params
clang -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_index_cache.m host/performance.c \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_index_cache
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_index_cache
clang -O2 -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_uniform_snapshot.m \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_uniform_snapshot
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_uniform_snapshot
clang -O1 -g -Wall -Wextra -Werror -fobjc-arc -fsanitize=address,undefined -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_inline_uniform.m \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_inline_uniform
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_inline_uniform
clang -O2 -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_cube_pass.m \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_cube_pass
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_cube_pass
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_wide_line.m -framework Foundation -framework Metal -o work/tmp/test_nvn_wide_line
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_wide_line
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_framebuffer_fetch.m host/performance.c -framework Foundation -framework Metal \
    -o work/tmp/test_nvn_framebuffer_fetch
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_framebuffer_fetch
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_fullscreen_compute.m host/performance.c -framework Foundation -framework Metal \
    -o work/tmp/test_nvn_fullscreen_compute
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_fullscreen_compute > /dev/null
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_depth_output.m host/performance.c -framework Foundation -framework Metal \
    -o work/tmp/test_nvn_depth_output
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_depth_output > /dev/null
clang -O2 -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" -I "$PWD/core/gfx/tex" tests/test_nvn_texture_writeback.m host/performance.c \
    "$PWD/core/gfx/nvn/nvn_tex.c" "$PWD/core/gfx/tex/nvn_deswizzle.c" -framework Foundation -framework Metal -o work/tmp/test_nvn_texture_writeback
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_texture_writeback > /dev/null
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_pass_writes.m -framework Foundation -framework Metal -o work/tmp/test_nvn_pass_writes
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_pass_writes
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_color_clear.m -framework Foundation -framework Metal \
    -o work/tmp/test_nvn_color_clear
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_color_clear
clang -O2 -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_pointer_storage.m "$PWD/core/gfx/nvn/nvn_refl.m" \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_pointer_storage
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_pointer_storage
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_math.m -framework Foundation -framework Metal -o work/tmp/test_nvn_math
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_math tests/fixtures/invariant_math.metal
clang -O2 -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" tests/test_shader_prep.m host/performance.c host/shader_prep.m \
    -framework Cocoa -framework QuartzCore -framework Metal -o work/tmp/test_shader_prep
MTL_DEBUG_LAYER=1 work/tmp/test_shader_prep
clang -Wall -Wextra -Werror -fobjc-arc -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" \
    tests/test_nvn_shader_warm.m host/performance.c -framework Foundation -framework Metal -o work/tmp/test_nvn_shader_warm
MTL_DEBUG_LAYER=1 ZELDA_WARM_ATTACHMENT_VARIANTS=1 work/tmp/test_nvn_shader_warm
MTL_DEBUG_LAYER=1 ZELDA_WARM_ATTACHMENT_VARIANTS=2 work/tmp/test_nvn_shader_warm
rm -f work/tmp/test_nvn_shader_warm
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_residency.m -framework Foundation -framework Metal -o work/tmp/test_nvn_residency
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_residency
clang -O2 -Wall -Wextra -Werror -fobjc-arc -I "$PWD/core/gfx/nvn" tests/test_nvn_c1_storage.m \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_c1_storage
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_c1_storage
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_c1_storage off
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_upload_pool.m -framework Foundation -framework Metal -o work/tmp/test_nvn_upload_pool
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_upload_pool
clang -O2 -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_texture_reuse.m -framework Foundation -framework Metal -o work/tmp/test_nvn_texture_reuse
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_texture_reuse
clang -O2 -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_texture_alias.m \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_texture_alias
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_texture_alias
clang -O2 -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_small_texture.m \
    -I "$PWD/core/host" host/nvn_dirty.c -framework Foundation -o work/tmp/test_nvn_small_texture
work/tmp/test_nvn_small_texture
clang -Wall -Wextra -Werror -fsanitize=address,undefined -I "$PWD/core/host" -I "$PWD/core/gfx/nvn" tests/test_nvn_dirty.c host/nvn_dirty.c -o work/tmp/test_nvn_dirty
work/tmp/test_nvn_dirty
clang -O1 -Wall -Wextra -Werror -fsanitize=address,undefined tests/test_nvn_pool_index.c -o work/tmp/test_nvn_pool_index
ZELDA_POOL_LOOKUP_CACHE=1 work/tmp/test_nvn_pool_index
ZELDA_POOL_LOOKUP_CACHE=0 work/tmp/test_nvn_pool_index
clang -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_query.m -framework Foundation -framework Metal -o work/tmp/test_nvn_query
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_query
clang -O2 -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_conditional_draw.m \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_conditional_draw
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_conditional_draw
clang -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" tests/test_nvn_depth.m host/performance.c \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_depth
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_depth
clang -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_depth_unorm.m \
    -framework Foundation -framework Metal -o work/tmp/test_nvn_depth_unorm
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_depth_unorm
rm -f work/tmp/test_nvn_depth_unorm
clang -Wall -Wextra -Werror -fobjc-arc tests/test_nvn_profile.m -framework Foundation -framework Metal -o work/tmp/test_nvn_profile
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_profile work/tmp/test_nvn_profile.tsv
clang -Wall -Wextra -Werror -fobjc-arc -ivfsoverlay work/build/graphics-overlay.json \
    -I "$PWD/core/gfx/nvn" -I "$PWD/core/gfx/nvn/include" -I "$PWD/core/gfx/tex" \
    tests/test_nvn_render_view.m "$PWD/core/gfx/nvn/nvn_tex.c" "$PWD/core/gfx/tex/nvn_fmt_mtl.c" \
    "$PWD/core/gfx/tex/nvn_deswizzle.c" -framework Foundation -framework Metal -o work/tmp/test_nvn_render_view
MTL_DEBUG_LAYER=1 work/tmp/test_nvn_render_view work/tmp/point-size-test.metal
clang -Wall -Wextra -Werror -fsanitize=undefined -I "$PWD/core/host" tests/test_kernel.c tests/runtime_stubs.c host/kernel.c host/threads.c host/timeline.c -o work/tmp/test_kernel
work/tmp/test_kernel
clang -Wall -Wextra -Werror -fsanitize=undefined -DZELDA_TEST_GUEST_STACK -I "$PWD/core/host" tests/test_threads.c tests/guest_stack.S tests/runtime_stubs.c host/kernel.c host/threads.c host/timeline.c -o work/tmp/test_threads
work/tmp/test_threads
clang -Wall -Wextra -Werror -fsanitize=address,undefined tests/test_filesystem.c host/filesystem.c -o work/tmp/test_filesystem
work/tmp/test_filesystem
clang -Wall -Wextra -Werror -fsanitize=address,undefined $(pkg-config --cflags opus) -I "$PWD/core/host" tests/test_services.c tests/runtime_stubs.c host/services.c host/audio.c host/audio_mixer.c host/filesystem.c host/kernel.c host/threads.c host/timeline.c $(pkg-config --libs opus) -framework AudioToolbox -o work/tmp/test_services
DM_AUDIO=0 work/tmp/test_services
clang -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined tests/test_audio.c -framework AudioToolbox -o work/tmp/test_audio
work/tmp/test_audio
clang -Wall -Wextra -Werror -Wno-deprecated-declarations -fobjc-arc -fsanitize=address,undefined \
    -ivfsoverlay work/build/graphics-overlay.json -I "$PWD/core/gfx/nvn" \
    -I "$PWD/core/host" tests/test_display.c tests/runtime_stubs.c host/trace_writer.c host/graphics.m host/shader_prep.m host/input.c host/vibration.c host/input_script.c host/timeline.c host/gamepad.m host/performance.c host/game_timing.c host/settings.m host/kernel.c host/threads.c \
    -framework Cocoa -framework QuartzCore -framework CoreVideo -framework Metal -framework GameController -framework CoreHaptics -o work/tmp/test_display
work/tmp/test_display
clang -Wall -Wextra -Werror -fobjc-arc -fsanitize=address,undefined -I host tests/test_settings.m host/settings.m -framework Foundation -o work/tmp/test_settings
work/tmp/test_settings work/tmp/test_settings.json
clang -Wall -Wextra -Werror -fsanitize=address,undefined -I "$PWD/core/host" tests/test_vibration.c host/vibration.c -o work/tmp/test_vibration
work/tmp/test_vibration
clang -Wall -Wextra -Werror -fobjc-arc -fsanitize=address,undefined -I "$PWD/core/host" \
    tests/test_gamepad_haptics.m host/vibration.c -framework Foundation -framework GameController -framework CoreHaptics -o work/tmp/test_gamepad_haptics
work/tmp/test_gamepad_haptics
clang -Wall -Wextra -Werror -fsanitize=address,undefined -I "$PWD/core/host" tests/test_font_watch.c -o work/tmp/test_font_watch
work/tmp/test_font_watch
clang -Wall -Wextra -Werror -fsanitize=address,undefined $(pkg-config --cflags vpx) tests/test_movie_video.c host/movie_video.c $(pkg-config --libs vpx) -o work/tmp/test_movie_video
work/tmp/test_movie_video
clang -Wall -Wextra -Werror -fsanitize=address,undefined -I "$PWD/core/host" $(pkg-config --cflags vpx) tests/test_movie.c host/movie_video.c $(pkg-config --libs vpx) -o work/tmp/test_movie
work/tmp/test_movie
clang -Wall -Wextra -Werror -fsanitize=address,undefined tests/test_performance.c host/performance.c -o work/tmp/test_performance
work/tmp/test_performance
work/venv/bin/python -m unittest discover -s tests -v
clang -Wall -Wextra -Werror tests/test_relr.c -o work/tmp/test_relr
work/tmp/test_relr
