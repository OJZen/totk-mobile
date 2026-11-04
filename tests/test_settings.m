#import <Foundation/Foundation.h>
#include <assert.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
#include "settings.h"

// Settings file: defaults, validation, atomic round trip and preservation of fields this build does not know.
int main(int argc, char **argv) { @autoreleasepool {
    assert(argc == 2);
    NSString *path = @(argv[1]);
    setenv("ZELDA_SETTINGS", argv[1], 1);
    // Separate processes exercise the one-time loader with old and new settings.
    const char *renders[] = {"original", "896p", "balanced", "performance", "480p", "1080p", "720p", "640p"};
    const int expected[] = {0, 0, 0, 2, 3, 1, 2, 3};
    for (unsigned i = 0; i < sizeof expected / sizeof *expected; i++) {
        pid_t child = fork(); assert(child >= 0);
        if (!child) {
            NSString *rumble = i % 4 == 0 ? @"normal" : i % 4 == 1 ? @"strong" : i % 4 == 2 ? @"off" : @"on";
            NSDictionary *old = @{@"dynamic_resolution": @"fixed", @"render_resolution": @(renders[i]), @"vibration": rumble};
            assert([[NSJSONSerialization dataWithJSONObject:old options:0 error:nil] writeToFile:path atomically:YES]);
            zelda_settings_t migrated = zelda_settings();
            assert(migrated.render == expected[i] && migrated.vibration == (i % 4 != 2));
            assert(zelda_settings_store(migrated));
            NSDictionary *saved = [NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:path] options:0 error:nil];
            assert(!saved[@"dynamic_resolution"] && [saved[@"vibration"] isEqual:(migrated.vibration ? @"on" : @"off")]);
            _exit(0);
        }
        int status; assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && !WEXITSTATUS(status));
    }
    NSData *initial = [@"{\"target_fps\": 45, \"fullscreen\": true, \"dynamic_resolution\": \"auto\", \"metalfx_upscale\": true, \"shader_compile\": \"soon\", \"future\": {\"a\": 1}}" dataUsingEncoding:NSUTF8StringEncoding];
    assert([initial writeToFile:path atomically:YES]);
    zelda_settings_t settings = zelda_settings();
    // An invalid rate keeps the default; other fields still load, and the earlier boolean MetalFX switch means "window".
    assert(settings.target_fps == 60 && settings.fullscreen && settings.metalfx == ZELDA_METALFX_WINDOW);
    assert(!settings.muted && !settings.hide_performance && settings.vibration == ZELDA_VIBRATION_ON && settings.render == ZELDA_RENDER_ORIGINAL && !settings.shader_wait);
    assert(!zelda_settings_valid_fps(120) && zelda_settings_valid_fps(30) && zelda_settings_valid_fps(90));
    assert(!zelda_settings_store((zelda_settings_t){.target_fps = 120}));
    assert(!zelda_settings_store((zelda_settings_t){.target_fps = 60, .metalfx = 3}));
    assert(!zelda_settings_store((zelda_settings_t){.target_fps = 60, .vibration = -1}));
    assert(!zelda_settings_store((zelda_settings_t){.target_fps = 60, .vibration = 2}));
    assert(!zelda_settings_store((zelda_settings_t){.target_fps = 60, .render = 4}));
    assert(zelda_settings().target_fps == 60);
    assert(zelda_settings_store((zelda_settings_t){.target_fps = 90, .fullscreen = false,
        .metalfx = ZELDA_METALFX_4K, .muted = true, .hide_performance = true, .vibration = ZELDA_VIBRATION_ON, .render = ZELDA_RENDER_640}));
    NSDictionary *saved = [NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:path] options:0 error:nil];
    assert([saved[@"target_fps"] isEqual:@90] && [saved[@"fullscreen"] isEqual:@NO] && [saved[@"future"][@"a"] isEqual:@1] &&
           !saved[@"dynamic_resolution"] && [saved[@"metalfx_upscale"] isEqual:@"4k"] &&
           [saved[@"sound"] isEqual:@NO] && [saved[@"performance_overlay"] isEqual:@NO] && [saved[@"vibration"] isEqual:@"on"] && [saved[@"render_resolution"] isEqual:@"640p"]);
    NSString *json = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
    assert([json containsString:@"\"sound\" : false"] && [json containsString:@"\"performance_overlay\" : false"]);   // JSON booleans
    settings = zelda_settings();
    assert(settings.target_fps == 90 && !settings.fullscreen && settings.metalfx == ZELDA_METALFX_4K && settings.muted &&
           settings.hide_performance && settings.vibration == ZELDA_VIBRATION_ON && settings.render == ZELDA_RENDER_640);
    unlink(argv[1]);
    puts("Settings: preset/rumble migration, obsolete field removal, validation, atomic save and unknown fields passed");
} }
