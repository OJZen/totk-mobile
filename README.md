# Zelda-Mobile

**English** | [简体中文](README.zh-CN.md)

Zelda-Mobile runs the Nintendo Switch release of *The Legend of Zelda: Tears of the Kingdom* **v1.4.3** natively on Apple silicon Macs and iPads, with an early iPhone build. The game's ARM64 code executes directly on the Apple CPU, system services are reimplemented by the host, and NVN graphics commands are translated to Metal. The Mac and iOS front ends share one core.

> [!IMPORTANT]
> This project is not affiliated with, authorized or endorsed by Nintendo. The repository contains **source code only**: no game files, updates, keys, saves, game shaders or any other game-derived data, and no instructions for obtaining them. Use only a game and keys that you own and dumped from your own console.

## Status

The project is under active development. You can load a save and play, but a full playthrough has not been validated.

| Platform | Working | Known limits |
| --- | --- | --- |
| Apple silicon Mac | Loading saves and playing scenes, keyboard and Xbox controller (with rumble), audio, cutscenes, amiibo file scanning (experimental), 30 / 60 / 90 FPS targets | The first visit to a new area or camera angle may stutter while shaders compile; not every area holds 60 FPS |
| iPad (M1 or newer) | Development-signed install, resource import, loading saves, touch controller, camera, audio | Well below 60 FPS at the original resolution; the first shader preparation takes a long time; hardware controllers are not fully validated |
| iPhone 15 Pro | Development-signed install, resource import, verified offline shader libraries, warm launch to the title screen | Gameplay, frame rate, memory and thermals have not been measured; no other iPhone has been tried, and chips without BC texture support (before A17 Pro) are untested |

Only v1.4.3 is supported. The preparation scripts verify module fingerprints and refuse other versions.

## Requirements

- An **Apple silicon Mac** (M1 or newer) with full Xcode, the command line tools and Homebrew.
- **About 60 GB of free space** (an external drive works). The extracted RomFS alone is about 16 GB; shader caches and builds need more.
- Your own *Tears of the Kingdom* base game and **v1.4.3 update** (`.nsz`), plus the matching `prod.keys`.
- For iPhone or iPad: an Apple developer signing team, a device in Developer Mode and a USB connection.

## Quick start (Mac)

See [Building and running](docs/getting-started.md) for the full steps and troubleshooting. In short:

```sh
git clone https://github.com/OJZen/totk-mobile.git
cd totk-mobile
brew install cmake pkg-config opus libvpx python@3.14
mkdir -p local/games local/keys
# Put the base game and the v1.4.3 update in local/games/ and prod.keys in local/keys/, then:
./script/prepare.sh
./script/prepare_resources.sh local/keys/prod.keys
./script/prepare_shaders.sh
./script/build_and_run.sh --load-only
./launch.command
```

After that, start the game by double-clicking `launch.command` (or running it in a terminal). `quick-load.command` additionally walks the in-game menu and loads the first entry of the save list.

The first launch prepares tens of thousands of shaders. This can take several minutes or longer; the startup screen shows each stage and its progress. Later launches reuse the cache.

amiibo file scanning is currently available only on macOS. iPhone and iPad have no amiibo file picker or scan controls yet, and scanning has not been validated on iOS. Physical NFC readers are not supported. See [amiibo controls and limitations](docs/controls.md#amiibo-mac-experimental).

## Controls

| Action | Keyboard |
| --- | --- |
| Move / camera | W A S D / I J K L |
| A / B / X / Y | X (confirm) / Z (back) / R / F |
| L / R, ZL / ZR | Q / E, Shift / Space |
| Inventory (+) / map (−) | Return / Tab |
| Control panel / performance overlay / full screen | ⌘, / ⌘P / ⌃⌘F |

Xbox controllers connect automatically over Bluetooth or USB. Frame rate, resolution, MetalFX, sound and rumble are set in the control panel (⌘,). See [Controls and settings](docs/controls.md).

## Saves

Saves live in `work/save/` inside the project and use the same format as the console. See [Saves](docs/saves.md) for importing and backing up. **Do not modify this directory while the game is running.**

## FAQ

**Startup takes a long time.** The first shader preparation is slow; the startup screen shows the current stage and its progress, and during play the performance overlay (⌘P) shows shader activity. The log is `work/logs/native-probe.log`.

**A preparation script reports a module or version mismatch.** Make sure the update is v1.4.3 and that `local/games/` holds exactly one base game and one update.

**The game stutters.** The first time a scene is seen, its pipelines are compiled on the spot and then cached. For sustained low frame rates, compare against the 1080 or 720 resolution setting in the control panel.

**Does it run on Intel Macs?** No. The game code runs natively as ARM64 and needs Apple silicon.

## For developers

- [Documentation index](docs/README.md)
- [Building and running](docs/getting-started.md): full Mac, iPhone and iPad setup
- [Source map](docs/architecture.md): components, generated files and where to make changes
- [Development guide](docs/development.md): tests, diagnostic switches and contribution rules
- [Repository layout](docs/repository.md): directory roles and local data that never enters Git

## License

The original code in this repository is licensed under the [Apache License 2.0](LICENSE). Reused third-party components keep their own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).

## Credits

Zelda-Mobile was built with [d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile) as its reference project: the loader, the ARM64 module rewriter and the NVN → Metal backend follow its design, and the files this project builds on are included in `core/` under its MIT license. It also draws on public work from Ryujinx, libnx, Switchbrew, SPIRV-Cross, hactool and others. See [Third-party notices](THIRD_PARTY_NOTICES.md) for details.

"The Legend of Zelda", "Tears of the Kingdom" and "Nintendo Switch" are trademarks of Nintendo.
