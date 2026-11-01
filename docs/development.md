# Development guide

**English** | [简体中文](development.zh-CN.md)

Set up the environment first with [Building and running](getting-started.md), and read the [Source map](architecture.md) to find the code you need.

## Starting a change

```sh
git status --short
mkdir -p work/tmp work/logs
export TMPDIR="$PWD/work/tmp/"
export PYTHONDONTWRITEBYTECODE=1
work/venv/bin/python tools/prepare_modules.py --check
```


Quit the game before building. Builds, the full test suite and runtime shader compilation share generated directories, so run them one at a time. Never edit `work/build` by hand.

## Tests

| Goal | Command |
| --- | --- |
| Repository checks: document links, dependency manifests, private paths | `python3 -m unittest discover -s tests -p test_repository.py -v` |
| Install the test dependencies | `work/venv/bin/python -m pip install --no-cache-dir -r requirements/test.txt` |
| Full Python, native and Metal suite | `./script/test.sh` |
| Build and check loading only | `./script/build_and_run.sh --load-only` |
| Real SDK clock, audio, font and rumble paths | `./script/build_and_run.sh --time-test` (also `--codec-test`, `--font-test`, `--vibration-test`) |

Run the checks related to your change first; run the native and Metal suites when you touch shared graphics, loading or threading. The full `test.sh` needs the prepared local environment and a Metal device, so it is not a resource-free CI job. The complete build log is `work/logs/build.log`.

Software checks cannot replace hands-on checks: what is visible on screen, what you hear and what a controller's rumble feels like must be checked on the real device.

## Scripted input

`ZELDA_INPUT_SCRIPT=<file>` runs an input script after shader preparation; `script/send_input.py` sends more lines to a running game through its FIFO. A script is a list of lines:

| Line | Meaning |
| --- | --- |
| `wait <ms>` | Wait |
| `tap <key> [ms]` / `down <key>` / `up <key>` | Tap, hold or release a host keyboard key; names follow the keyboard mapping |
| `mark <text>` | Write a marker to the log |
| `shot <path>` | Save a screenshot |
| `trace <seconds> <path>`, `dump <frames>` | Record a frame timeline / dump graphics commands |
| `fps <30\|60\|90\|120>`, `render <50..200>` | Change the frame rate target / the internal render scale |

`script/scenes/first-save.input` and `first-save-fast.input` walk the menu to the first save. Scripts know nothing about your save list, so check where you end up. Never use scripts to advance the story or to save.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `ZELDA_TIMEOUT_SECONDS` | Stop the game after N seconds (0 = never, maximum 3600). `build_and_run.sh` defaults to 120; the launchers default to 0 |
| `ZELDA_SETTINGS` | Use another settings file instead of `work/settings.json` |
| `ZELDA_TARGET_FPS`, `ZELDA_METALFX` | Override the saved frame rate target and MetalFX setting for this run |
| `ZELDA_SHARED_FONT` | TrueType font used as the shared system font |
| `ZELDA_SHADER_IR=0`, `ZELDA_SHADER_PREP_ALL=1` | Ignore offline Metal libraries / force a full shader preparation pass |
| `ZELDA_DAY_RATE` | Scale the in-game day/night clock (0–16, 0 pauses it). Frame rate and physics are unaffected |
| `ZELDA_WINDOW_POINTS` | Window size such as `1600x900` |
| `ZELDA_RENDER_SCALE` | Experimental render scale (0.5–2). Clear it for normal play |
| `ZELDA_SHADER_ASYNC=0\|1`, `ZELDA_SHADER_ASYNC_BUDGET_MS` | Override the Shaders setting for this run (1 = background, 0 = wait); per-frame wait for background pipelines before their draws are skipped (default 3) |
| `ZELDA_PSO_LOG=1` | Diagnostics: every pipeline and shader library created at draw time, and background preparation steps over 5 ms |
| `ZELDA_THREAD_CPU=1`, `ZELDA_IPC_LOG=1` | Diagnostics: per-thread CPU census to `work/logs/threads.tsv`, IPC logging |

Many other `ZELDA_*` and `DM_*` switches exist for A/B comparisons of individual optimizations. They default to the validated behavior; search for `getenv` in `host/` before relying on one.

## Imported code and dependencies

- `core/` is imported code kept byte-identical: the replacement anchors in `script/prepare_graphics.py`, the shader chain fingerprint and the module fingerprints all depend on its exact bytes. Prefer changing the adapters. If a `core/` file must change, update the anchors, add a proven alias in `script/shader-chain-aliases.json` or regenerate the shader cache, and re-run `prepare.sh`; see [`core/README.md`](../core/README.md).
- Python dependencies are pinned in [`requirements/`](../requirements/): `resources.txt` includes `runtime.txt`, `test.txt` includes `resources.txt`, and `ios-transfer.txt` is a separate environment.
- Every game patch must stay bound to the game version, check the original bytes and keep its manifest verification.

## Ground rules

- **Original quality, normal speed.** Optimizations must not lower resolution, drop effects, skip draws or speed up the game. The one exception is the player's Shaders setting (`host/shader_async.h`): in Background mode a draw waits only until its first-time shader or pipeline is ready. Never disable a check or fake a success value to make a test pass.
- Source code, comments, UI text, log messages and file names are in English. Documentation is English-first, with a Simplified Chinese copy (`*.zh-CN.md`) kept in sync. Encode required non-ASCII upstream anchors and Unicode test characters as escapes; `tests/test_repository.py` enforces this.
- Change the sources in `host/`, `ios/`, `script/` and `tools/`, never the generated files in `work/build`. Keep `core/` unchanged unless you follow the steps above.
- Never commit game files, keys, saves, logs or build products. `local/` and `work/` are ignored; see [Repository layout](repository.md). Do not run `git clean` in a prepared checkout: it would delete your local data.

## Contributing

1. Keep each commit to one change, with a clear message. Check the staged content with `git diff --cached --check` and `git diff --cached --stat`.
2. Include tests for new behavior. Graphics changes need a Metal read-back test where possible.
3. Describe what you verified and how (unit tests, build, on-screen check, device), and what you did not verify.
4. Update the English and Chinese documentation together.

Contributions are accepted under the [Apache License 2.0](../LICENSE), the license of this repository.
