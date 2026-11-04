#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
root="$PWD/work/ios/deps"
mkdir -p "$root" work/tmp
export TMPDIR="$PWD/work/tmp/"
if [[ ! -f "$root/install/lib/libvpx.a" ]]; then
archive="$root/libvpx-1.17.0.tar.gz"
if [[ ! -f "$archive" ]]; then
    curl --fail --location --retry 2 --max-time 180 \
        https://github.com/webmproject/libvpx/archive/refs/tags/v1.17.0.tar.gz -o "$archive.part"
    mv "$archive.part" "$archive"
fi
printf '%s  %s\n' 1020f184046187baa2985dbde38e0691f49c44088bca7a1842b0236c6081dc0a "$archive" | shasum -a 256 -c -
if [[ ! -d "$root/libvpx-1.17.0" ]]; then tar -xzf "$archive" -C "$root"; fi
mkdir -p "$root/build"
cd "$root/build"
export CC="$(xcrun --sdk iphoneos --find clang)" CXX="$(xcrun --sdk iphoneos --find clang++)"
export SDKROOT="$(xcrun --sdk iphoneos --show-sdk-path)"
export CFLAGS="-target arm64-apple-ios26.0 -isysroot $SDKROOT"
export CXXFLAGS="$CFLAGS" LDFLAGS="$CFLAGS" IOS_VERSION_MIN=26.0
"$root/libvpx-1.17.0/configure" --target=arm64-darwin-gcc --prefix="$root/install" \
    --disable-examples --disable-tools --disable-docs --disable-unit-tests \
    --disable-shared --enable-static --disable-vp8-encoder --disable-vp9-encoder \
    --enable-vp9-highbitdepth
make -j4
make install
fi
if [[ ! -f "$root/install/lib/libopus.a" ]]; then
    archive="$root/opus-1.6.1.tar.gz"
    if [[ ! -f "$archive" ]]; then
        curl --fail --location --retry 2 --max-time 180 \
            https://ftp.osuosl.org/pub/xiph/releases/opus/opus-1.6.1.tar.gz -o "$archive.part"
        mv "$archive.part" "$archive"
    fi
    printf '%s  %s\n' 6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1 "$archive" | shasum -a 256 -c -
    if [[ ! -d "$root/opus-1.6.1" ]]; then tar -xzf "$archive" -C "$root"; fi
    cmake -S "$root/opus-1.6.1" -B "$root/opus-build" -G 'Unix Makefiles' \
        -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_SYSROOT=iphoneos -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DCMAKE_OSX_DEPLOYMENT_TARGET=26.0 -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$root/install" \
        -DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY -DOPUS_BUILD_TESTING=OFF -DOPUS_BUILD_PROGRAMS=OFF -DBUILD_SHARED_LIBS=OFF
    cmake --build "$root/opus-build" -j4
    cmake --install "$root/opus-build"
fi
# The on-device shader compiler links into the app (ios/CMakeLists.txt).
bash "${root%/work/ios/deps}/script/prepare_runtime_shaders.sh" ios
