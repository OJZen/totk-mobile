#!/usr/bin/env bash
# Builds the on-device shader compiler (ios/shaders): the prepared translator with RuntimeShaders.cs as a NativeAOT
# static library for iOS, and the SPIRV-Cross sources its bridge compiles against. `mac` builds the comparison
# check in tests/runtime_shaders instead.
set -euo pipefail
cd "$(dirname "$0")/.."
target=${1:-ios}
export TMPDIR="$PWD/work/tmp/" DOTNET_ROOT="$PWD/work/tools/dotnet" DOTNET_CLI_HOME="$PWD/work/tools/dotnet-home"
export NUGET_PACKAGES="$PWD/work/tools/nuget" DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
test -f work/tools/xlate-source/Program.cs || { echo 'Run ./script/prepare_shader_tools.sh first' >&2; exit 1; }
out=work/build/runtime-shaders
mkdir -p "$out" work/tmp work/logs
# Same commit as script/prepare_shader_tools.sh builds the Mac command line from.
spirv=work/ios/deps/SPIRV-Cross
if [[ ! -f "$spirv/main.cpp" ]]; then
    rm -rf "$spirv.part"; git init --quiet "$spirv.part"
    git -C "$spirv.part" fetch --quiet --depth 1 https://github.com/KhronosGroup/SPIRV-Cross.git aa217aeb6c9f0ace7a0ab233b28807edf45eb165
    git -C "$spirv.part" checkout --quiet --detach FETCH_HEAD
    mv "$spirv.part" "$spirv"
fi
# The translator's Main runs in process; RuntimeShaders calls it like the Mac pipeline runs the command.
work/venv/bin/python - <<'PY'
from pathlib import Path
code = Path('work/tools/xlate-source/Program.cs').read_text()
anchor = '    static int Main(string[] args)\n'
if code.count(anchor) != 1:
    raise SystemExit('Inspect the translator entry before preparing the runtime shader compiler')
target = Path('work/build/runtime-shaders/Program.cs')
code = code.replace(anchor, '    internal static int Main(string[] args)\n')
if not target.exists() or target.read_text() != code:
    target.write_text(code)
PY
ryujinx="$PWD/core/third_party/ryujinx"
# The libraries SPIRV-Cross's command line links; ios/CMakeLists.txt compiles the same list.
SPIRV_SOURCES="spirv_cross spirv_parser spirv_cross_parsed_ir spirv_cfg spirv_glsl spirv_cpp spirv_msl spirv_hlsl spirv_reflect spirv_cross_util"
if [[ "$target" == mac ]]; then
    sources=()
    for name in $SPIRV_SOURCES; do sources+=("$spirv/$name.cpp"); done
    clang++ -std=c++17 -O2 -dynamiclib -w -I"$spirv" -o "$out/libzelda_spirv.dylib" ios/shaders/spirv_msl.cpp "${sources[@]}"
    "$DOTNET_ROOT/dotnet" build tests/runtime_shaders/RuntimeShadersCheck.csproj -c Release --disable-build-servers \
        --artifacts-path "$PWD/work/build/dotnet-check" -p:RyuSrc="$ryujinx" > work/logs/runtime-shaders-check.log 2>&1 ||
        { tail -20 work/logs/runtime-shaders-check.log >&2; exit 1; }
else
    "$DOTNET_ROOT/dotnet" publish ios/shaders/RuntimeShaders.csproj -c Release --disable-build-servers \
        --artifacts-path "$PWD/work/build/dotnet-ios" -p:RyuSrc="$ryujinx" > work/logs/runtime-shaders-ios.log 2>&1 ||
        { tail -20 work/logs/runtime-shaders-ios.log >&2; exit 1; }
    # ios/CMakeLists.txt links the library with the NativeAOT runtime it was compiled against.
    work/venv/bin/python - <<'PY'
import json, shutil
from pathlib import Path
build = Path('work/build/dotnet-ios')
packs = [Path(f).parent for f in json.loads((build / 'obj/RuntimeShaders/project.nuget.cache').read_text())['expectedPackageFiles']
         if Path(f).parent.parent.name == 'microsoft.netcore.app.runtime.nativeaot.ios-arm64']
if len(packs) != 1:
    raise SystemExit('Expected one NativeAOT iOS runtime pack')
native = packs[0] / 'runtimes/ios-arm64/native'
target = Path('work/ios/deps/runtime-shaders'); target.mkdir(parents=True, exist_ok=True)
names = ['libbootstrapperdll.o', 'libRuntime.WorkstationGC.a', 'libeventpipe-disabled.a', 'libstandalonegc-disabled.a',
         'libaotminipal.a', 'libstdc++compat.a', 'libSystem.Native.a', 'libSystem.Globalization.Native.a',
         'libSystem.IO.Compression.Native.a', 'libSystem.Security.Cryptography.Native.Apple.a']
for source in [build / 'publish/RuntimeShaders/release_ios-arm64/RuntimeShaders.a', *(native / n for n in names)]:
    shutil.copy2(source, target / source.name)
PY
fi
