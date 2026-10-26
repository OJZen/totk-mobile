# Controls and settings

**English** | [简体中文](controls.zh-CN.md)

## Mac keyboard

| Action | Key |
| --- | --- |
| Move / camera | W A S D / I J K L |
| A / B / X / Y | X / Z / R / F (X confirms, Z goes back) |
| D-pad | Arrow keys |
| L / R, ZL / ZR | Q / E, Shift / Space |
| Inventory (+) / map (−) | Return / Tab |
| Left / right stick click | C / V |
| Control panel / performance overlay | ⌘, / ⌘P |
| Full screen | ⌃⌘F |

## Xbox controller

Xbox controllers connect automatically over Bluetooth or USB while the game window is in front. Menu (≡) is +, View is −. When the stick is deflected it takes priority over held keyboard keys. Rumble uses a curve calibrated for Xbox controllers.

## Control panel

Open it with ⌘, or **View → Controls**. Settings are saved to `work/settings.json` (override the path with `ZELDA_SETTINGS`).

| Setting | Values | Notes |
| --- | --- | --- |
| Frame rate | 30 / 60 / 90 FPS | Default 60. A target is not a guarantee; pre-rendered movies still play at 30 |
| Resolution | Original / 1080 / 720 / 640 | **Original** keeps the game's own dynamic resolution. Fixed modes render the scene at 1920×1080, 1280×720 or 1136×640 |
| MetalFX | Off / Window / 4K | Spatially upscales only the final image: **Window** to the window's pixel size, **4K** to 3840×2160 and then fitted to the window. Both apply only when the window has more pixels than the game image |
| Fullscreen, Sound, Performance | Off / On | Performance shows the overlay |
| Rumble | Off / On | Off stops rumble immediately; turning it back on does not replay old pulses. **Test** only checks the host output |

Older settings files are migrated automatically: `896p`/`balanced` become Original, `performance` becomes 720, the removed `480p` becomes 640, an old MetalFX On becomes Window, and old rumble strengths read as On. Unknown fields are kept.

## Startup screen

Until the game can draw without stalling, a startup screen covers the window with one progress bar that only moves forward. It shows the current stage: checking the shader libraries, compiling the ones that are missing (a long step on the first launch), starting the game, then loading the shaders and building the pipelines recorded on earlier runs. Stages with a counter show it, for example `Building pipelines 4,000 / 8,452`. The screen fades into the game when the warm-up ends.

## Performance overlay

The overlay (⌘P) shows FPS and target, render size, CPU, GPU time and memory. Its top-right corner shows the shader warm-up state: `Checking shaders`, `Shaders N%`, `Pipelines N%`, `Warming up` while the total is not yet known, and `Compiling shaders` when a new library or pipeline is created during play. The percentage refers to the current stage only.

## iPad

Without a hardware controller, the system touch controller is shown; + and − at the top open the matching menus. The overlay shows FPS and target, render size, app CPU, GPU time and physical memory; tap its top edge to collapse it. Auto-lock is suspended while the game is in front. Settings live in the app's `Documents/work/settings.json`, separate from the Mac; there is no full settings panel on iOS yet.

## amiibo (Mac, experimental)

This feature currently has a macOS interface only. iPhone and iPad do not yet provide amiibo file selection or scanning controls; the shared service implementation alone does not enable scanning on iOS.

Enable amiibo in the game's Options, then hold Q (controller L) to open the ability wheel, use I/J/K/L (right stick) to select amiibo, release Q and press it again to activate the ability. While the game is waiting for a scan, choose **amiibo > Scan amiibo File...** (Command-Shift-A) and select a raw NTAG215 `.bin` dump (532, 540 or 572 bytes). A file selected before detection begins is queued for the next scan. Choose **Remove amiibo** to remove the tag; select a file again for another scan.

The virtual reader imports only the public UID and model information. It preserves the UID and leaves daily limits to the game. Original files are never written, and selections are not persisted between launches. It checks the file size, UID check bytes and public header, not the encrypted data's authenticity. Encrypted registration/application data, writing and physical NFC readers are not supported. Only ROM mounting is implemented; games requesting RAM receive an error. Scanning has been confirmed in game on the prepared v1.4.3 build; compatibility with every amiibo has not been verified.

If a “waiting for the game” alert appears after selecting a file, the file loaded but the game has not begun scanning. Close the alert and activate the amiibo ability in game. NFC requests and results are recorded in `work/logs/native-probe.log`; `ZELDA_AMIIBO_TRACE=1` also logs state polling.
