# Building and running

**English** | [简体中文](getting-started.zh-CN.md)

Run every command from the project root. Once an environment is prepared, just use the launchers; there is no need to extract, translate or clear caches again.

## Requirements

- Apple silicon Mac with full Xcode and the command line tools.
- Homebrew packages: `cmake`, `pkg-config`, `opus`, `libvpx` and `python@3.14`.
- Your own base game and **v1.4.3** update as `.nsz` files, and the matching `prod.keys`.
- Plan for about 60 GB of free space on the drive that holds the project.

Game files, keys, saves, shader derivatives and signed apps are never part of the repository. `local/` and `work/` are ignored by Git; see [Repository layout](repository.md).

## Mac: first-time setup

```sh
git clone https://github.com/OJZen/totk-mobile.git
cd totk-mobile
brew install cmake pkg-config opus libvpx python@3.14
mkdir -p local/games local/keys local/save-imports
```

Put the base game and the update into `local/games/` (the update is the file whose name contains `[UPD]`) and `prod.keys` into `local/keys/`. If more than one base game or update is found, the scripts stop instead of guessing.

```sh
./script/prepare.sh
./script/prepare_resources.sh local/keys/prod.keys
./script/prepare_shaders.sh
./script/build_and_run.sh --load-only
```

| Step | What it does |
| --- | --- |
| `prepare.sh` | Creates `work/venv`, extracts the ExeFS, verifies the module versions and original bytes, and writes the patched modules to `work/patched/` |
| `prepare_resources.sh` | Builds a pinned hactool, verifies NCA / BKTR / IVFC and writes the RomFS to `work/romfs.bin`. Pass the key file as a path; never paste its contents anywhere |
| `prepare_shaders.sh` | Fetches the pinned shader tools (.NET SDK, SPIRV-Cross), then extracts, pairs, translates and builds the game shaders into `work/shaders/` |
| `build_and_run.sh --load-only` | Builds `work/build/Zelda-Mobile.app` and checks that the game loads, without opening the title screen |

Some guest shader requests are still unsupported. When shader preparation fails, check `work/shaders/build-failures.json` and do not ignore the exit code.

To build and keep the game running:

```sh
DM_PRESENT_LINK=0 ZELDA_TIMEOUT_SECONDS=0 ./script/build_and_run.sh
```

Without `ZELDA_TIMEOUT_SECONDS`, the build script stops the game after 120 seconds. The launchers have no time limit. Always quit the game before rebuilding.

## Mac: everyday launch

```sh
./launch.command
# Or walk the in-game menu and load the first save in the list:
./quick-load.command
```

Both also work from Finder. If the game is already running, the launcher only brings it to the front. `quick-load.command` goes through the normal load menu (it is not a memory snapshot), so the destination changes when your save list changes.

The app at `work/build/Zelda-Mobile.app` uses resources inside the project and cannot be moved elsewhere. The shared system font defaults to the macOS AppleGothic.ttf; set `ZELDA_SHARED_FONT` to another TrueType font with `glyf`/`loca` tables (smaller than 17 MiB, no CFF). Fonts are not distributed with the source.

## Mac: optional offline shader libraries

By default both the Mac and iOS builds load verified offline Metal IR libraries when they exist and fall back to the MSL source when they are missing, stale or damaged. To build them, install the Metal Toolchain component in Xcode (check with `xcrun --sdk macosx metal --version`), quit the game, and run:

```sh
mkdir -p work/tmp work/logs
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/work/tmp/" \
  work/venv/bin/python script/run_guarded.py --seconds 3600 --max-gib 1.25 \
  --report work/logs/shader-ir-build-guard.json -- \
  work/venv/bin/python script/build_shader_ir.py work/shaders/b-<chain> --jobs 2
```

Replace `<chain>` with the value in `work/build/dmsh_chain.h`. The build can be interrupted and resumed and never overwrites the MSL source. The cache lives in `work/shaders/ir-macos/`; this historical name is also used for the unified Metal libraries transferred to iOS. To compare against the source path, launch with `ZELDA_SHADER_IR=0`; to force a full preparation pass, use `ZELDA_SHADER_PREP_ALL=1`.

## iPhone / iPad: build and install

The same app targets both iPhone and iPad, using arm64 with a minimum deployment target of iOS / iPadOS 26. A device does not need to be connected to build the app. Installation requires a development provisioning profile that includes the target device; register a new device in Xcode and refresh the signing before installing.

Finish the Mac preparation first (resources, modules, MSL, render heap, and `./script/prepare_shader_tools.sh`). The build links an on-device shader compiler (`script/prepare_runtime_shaders.sh` runs as part of it), so new shader requests are translated on the device and do not need the Mac. `ios.sh transfer shaders` also sends the shader index and code it translates from.

```sh
# Use your own team and device identifiers; do not write them into the source.
export ZELDA_IOS_TEAM=YOUR_TEAM_ID
export ZELDA_IOS_DEVICE=YOUR_DEVICE_ID
work/venv/bin/python script/prepare_render_resources.py
work/venv/bin/python script/run_guarded.py --seconds 180 --max-gib 2 \
  --report work/logs/ios-build-guard.json -- ./script/ios.sh build
work/venv/bin/python script/run_guarded.py --seconds 90 --max-gib 0.75 \
  --report work/logs/ios-install-guard.json -- ./script/ios.sh install
```

`ZELDA_IOS_BUNDLE` overrides the development bundle identifier. Resource transfer uses its own Python environment (verified with Python 3.15):

```sh
python3 -m venv work/ios/transfer-venv
TMPDIR="$PWD/work/tmp/" PIP_CACHE_DIR="$PWD/work/ios/pip-cache" \
  work/ios/transfer-venv/bin/python -m pip install -r requirements/ios-transfer.txt
./script/ios.sh transfer probe
./script/ios.sh transfer romfs
./script/ios.sh transfer shaders
./script/ios.sh transfer seed
./script/ios.sh transfer ready
./script/ios.sh launch
./script/ios.sh logs
```

If the offline IR cache above has already been built, run `./script/ios.sh transfer ir` before launching. It transfers only that cache, retaining the source files and saves. A complete cache lets iOS defer the full source-library preparation pass; every library is still checked when loaded, with source fallback if it is missing, stale or rejected. Device pipeline creation may still take time on the first IR launch.

Run each step only after the previous one succeeds; `ready` must come after both asset transfers finish. Transfers verify all content by default. To skip content hashes and read-back, append `--no-verify` to the `romfs`, `shaders`, or `ir` commands. This mode checks file sizes, resumes by length, and marks its receipts as unverified; it cannot detect same-size corruption and does not apply to saves. Keep at least about 25 GB free on the device. The transfer keeps a 2 GiB safety margin and refuses to overwrite a different existing save.

> [!WARNING]
> Do not push the large resource files with `devicectl` directly. That can exhaust the Mac's memory and internal disk. Always use `script/ios.sh transfer`, which copies over AFC in bounded chunks, can resume, and verifies every file by default.

A normal iOS launch does not load a save automatically. Each device keeps its own copy of the saves and never writes back to the Mac. The first on-device shader preparation can take most of an hour; later launches with a warm cache take well under a minute on the tested M1 iPad. Shader preparation time, memory headroom and game performance must be measured separately on an iPhone.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Module fingerprint or version mismatch | The update must be v1.4.3; keep exactly one base game and one update in `local/games/` |
| Build fails | The full log is `work/logs/build.log`; the script only prints the tail |
| Black window for a long time | First shader preparation; watch the performance overlay (⌘P) and `work/logs/native-probe.log` |
| Low disk space | Generated data lives in `work/`. Delete only what you can rebuild, never `work/save*` |
