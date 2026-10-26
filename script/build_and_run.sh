#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
mode=${1:-run}
case "$mode" in run|--load-only|--debug|--codec-test|--font-test|--vibration-test|--time-test|--amiibo-test) ;; *) echo "usage: $0 [--load-only|--debug|--codec-test|--font-test|--vibration-test|--time-test|--amiibo-test]" >&2; exit 2;; esac
if [[ "$mode" == --font-test && ! -r "${ZELDA_FONT_MANIFEST:-work/font-audit/font-files.tsv}" ]]; then
    echo 'Run work/venv/bin/python script/font_audit.py first (or set ZELDA_FONT_MANIFEST)' >&2; exit 2
fi
test "$(uname -m)" = arm64 || { echo 'Apple silicon is required' >&2; exit 1; }
test -f work/patched/main.patched.bin || { echo 'Run ./script/prepare.sh first' >&2; exit 1; }
work/venv/bin/python tools/prepare_modules.py --check
if [[ "$mode" != --codec-test && "$mode" != --font-test && "$mode" != --vibration-test && "$mode" != --time-test && "$mode" != --amiibo-test && "$mode" != --load-only && -z "${ZELDA_ROMFS:-}" ]]; then
    work/venv/bin/python script/prepare_render_resources.py
fi
mkdir -p work/build work/tmp work/logs
export TMPDIR="$PWD/work/tmp/"
work/venv/bin/python script/prepare_graphics.py
gfx="$PWD/core/gfx"
sources=(host/probe.c host/codec.c)
binary=work/build/zelda_probe
if [[ "$mode" == --codec-test ]]; then
    sources=(tests/test_codec.c)
    binary=work/tmp/test_codec
    trap 'rm -f work/tmp/test_codec' EXIT
elif [[ "$mode" == --font-test ]]; then
    sources=(tests/test_font_sdk.c)
    binary=work/tmp/test_font_sdk
    trap 'rm -f work/tmp/test_font_sdk' EXIT
elif [[ "$mode" == --vibration-test ]]; then
    sources=(tests/test_vibration_sdk.c)
    binary=work/tmp/test_vibration_sdk
    trap 'rm -f work/tmp/test_vibration_sdk; rm -rf work/tmp/test_vibration_sdk.dSYM' EXIT
elif [[ "$mode" == --amiibo-test ]]; then
    sources=(tests/test_amiibo_sdk.c)
    binary=work/tmp/test_amiibo_sdk
    trap 'rm -f work/tmp/test_amiibo_sdk; rm -rf work/tmp/test_amiibo_sdk.dSYM' EXIT
elif [[ "$mode" == --time-test ]]; then
    sources=(tests/test_time_sdk.c)
    binary=work/tmp/test_time_sdk
    trap 'rm -f work/tmp/test_time_sdk; rm -rf work/tmp/test_time_sdk.dSYM' EXIT
fi
clang -O2 -g -Wall -Wextra -Wno-deprecated-declarations -fobjc-arc \
    $(pkg-config --cflags opus vpx) \
    -ivfsoverlay work/build/graphics-overlay.json \
    -I host -I "$PWD/core/host" -I "$gfx" -I "$gfx/nvn" -I "$gfx/nvn/include" \
    "${sources[@]}" host/kernel.c host/threads.c host/timeline.c host/trace_writer.c host/services.c host/audio.c host/audio_mixer.c host/filesystem.c host/graphics.m host/shader_prep.m host/input.c host/vibration.c host/movie.c host/movie_video.c host/font_watch.c host/input_script.c host/gamepad.m host/performance.c host/game_timing.c host/settings.m \
    "$PWD/core/host/loader.c" "$PWD/core/host/entry.S" \
    work/build/hle_nv.c host/display.c work/build/hle_nvn.c \
    "$gfx/nvn/nvn_mtl.m" "$gfx/nvn/nvn_refl.m" "$gfx/nvn/nvn_tex.c" host/nvn_dirty.c \
    "$gfx/nvn/nvn_thunks.S" "$gfx/nvn/nvn_entry.S" \
    "$gfx/tex/nvn_fmt_mtl.c" "$gfx/tex/nvn_bcdec.c" "$gfx/tex/nvn_deswizzle.c" "$gfx/tex/nvn_deswizzle_gpu.m" \
    -framework Cocoa -framework QuartzCore -framework CoreVideo -framework Metal -framework MetalFX -framework ImageIO -framework UniformTypeIdentifiers -framework AudioToolbox -framework GameController -framework CoreHaptics \
    $(pkg-config --libs opus vpx) \
    -o "$binary" > work/logs/build.log 2>&1 || { tail -60 work/logs/build.log; exit 1; }
if [[ "$mode" == --codec-test || "$mode" == --vibration-test || "$mode" == --time-test || "$mode" == --amiibo-test ]]; then
    "$binary" work/patched
    exit
elif [[ "$mode" == --font-test ]]; then
    "$binary" work/patched "${ZELDA_FONT_MANIFEST:-work/font-audit/font-files.tsv}"
    exit
fi
app=work/build/Zelda-Mobile.app/Contents
mkdir -p "$app/MacOS"
# Rename into place: overwriting a running game executable invalidates its code signature.
cp "$binary" "$app/MacOS/zelda_probe.new" && mv -f "$app/MacOS/zelda_probe.new" "$app/MacOS/zelda_probe"
cat > "$app/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.zelda-mobile</string>
<key>CFBundleName</key><string>Zelda-Mobile</string>
<key>CFBundleExecutable</key><string>launch</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
cat > "$app/MacOS/launch" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../../../.."
export DM_NVN_TBDR=${DM_NVN_TBDR:-0} DM_PSO_MANIFEST=off
export DM_PRESENT_LINK=${DM_PRESENT_LINK:-0}
export DM_NVN_SHMAP="$PWD/work/shaders/shader_map.tsv" DM_MSL_DIR="$PWD/work/shaders" DM_PSO_USER=${DM_PSO_USER:-"$PWD/work/shaders/pso_user.tsv"}
export ZELDA_TIMEOUT_SECONDS=${ZELDA_TIMEOUT_SECONDS:-0}
if [[ -z "${ZELDA_ROMFS:-}" ]]; then
    work/venv/bin/python script/prepare_render_resources.py
    export ZELDA_ROMFS="$PWD/work/romfs-host.bin"
fi
exec work/build/Zelda-Mobile.app/Contents/MacOS/zelda_probe work/patched >work/logs/native-probe.log 2>&1
LAUNCH
chmod +x "$app/MacOS/launch"
binary="$app/MacOS/zelda_probe"
export DM_NVN_TBDR=${DM_NVN_TBDR:-0} DM_PSO_MANIFEST=off
export DM_NVN_SHMAP="$PWD/work/shaders/shader_map.tsv" DM_MSL_DIR="$PWD/work/shaders" DM_PSO_USER=${DM_PSO_USER:-"$PWD/work/shaders/pso_user.tsv"}
export ZELDA_ROMFS=${ZELDA_ROMFS:-"$PWD/work/romfs-host.bin"}
if [[ "$mode" == --debug ]]; then
    exec lldb -- "$binary" work/patched
fi
set --
[[ "$mode" != --load-only ]] || set -- --load-only
log=work/logs/native-probe.log
[[ "$mode" != --load-only ]] || log=work/logs/load-only.log
"$binary" work/patched "$@" 2>&1 | tee "$log"
