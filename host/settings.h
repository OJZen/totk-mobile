#pragma once
#include <stdbool.h>

// Player-facing settings, persisted as JSON in work/settings.json (ZELDA_SETTINGS overrides the path).
// A missing file or an invalid field keeps the default for that field; unknown fields are preserved on save.
// MetalFX spatial upscale of the presented 1920x1080 image: to the window's pixels, or to 3840x2160.
enum { ZELDA_METALFX_OFF, ZELDA_METALFX_WINDOW, ZELDA_METALFX_4K };
// Original leaves the game's size selection untouched; the other choices set the scene render height.
enum { ZELDA_RENDER_ORIGINAL, ZELDA_RENDER_1080, ZELDA_RENDER_720, ZELDA_RENDER_640 };
enum { ZELDA_VIBRATION_OFF, ZELDA_VIBRATION_ON };
typedef struct {
    unsigned target_fps;        // 30, 60 or 90 (default 60)
    bool fullscreen;            // restore the window in full screen
    int metalfx;                // ZELDA_METALFX_* (default OFF)
    bool muted;                 // host speaker switch (default sound on)
    bool hide_performance;      // performance overlay hidden (default shown)
    int vibration;              // ZELDA_VIBRATION_* (default ON, calibrated strength)
    int render;                 // ZELDA_RENDER_* (default ORIGINAL)
    bool shader_wait;           // wait for new shaders to compile instead of skipping their draws meanwhile (default off)
} zelda_settings_t;

zelda_settings_t zelda_settings(void);
// Validates and writes the whole set atomically; returns false (and keeps the old values) if invalid or unwritable.
bool zelda_settings_store(zelda_settings_t settings);
bool zelda_settings_valid_fps(unsigned fps);
