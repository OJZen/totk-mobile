#import <Foundation/Foundation.h>
#include "settings.h"
#include <pthread.h>
#include <stdlib.h>

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static zelda_settings_t current = {.target_fps = 60, .vibration = ZELDA_VIBRATION_ON};
static NSArray<NSString *> *metalfx_names(void) { return @[@"off", @"window", @"4k"]; }
static NSArray<NSString *> *vibration_names(void) { return @[@"off", @"on"]; }
static NSArray<NSString *> *render_names(void) { return @[@"original", @"1080p", @"720p", @"640p"]; }
static NSMutableDictionary *stored;   // Everything read from disk, so fields written by newer builds survive a save.
static bool loaded;

bool zelda_settings_valid_fps(unsigned fps) { return fps == 30 || fps == 60 || fps == 90; }

static NSString *settings_path(void) {
    const char *path = getenv("ZELDA_SETTINGS");
    return path && *path ? @(path) : @"work/settings.json";
}

static void load_locked(void) {
    if (loaded) return;
    loaded = true; stored = [NSMutableDictionary dictionary];
    NSData *data = [NSData dataWithContentsOfFile:settings_path()];
    id object = data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
    if (![object isKindOfClass:[NSDictionary class]]) {
        if (data) fprintf(stderr, "SETTINGS ignored unreadable %s\n", settings_path().UTF8String);
        return;
    }
    [stored addEntriesFromDictionary:object];
    id fps = stored[@"target_fps"], fullscreen = stored[@"fullscreen"];
    if ([fps isKindOfClass:[NSNumber class]] && zelda_settings_valid_fps([fps unsignedIntValue]) && [fps doubleValue] == [fps unsignedIntValue])
        current.target_fps = [fps unsignedIntValue];
    else if (fps) fprintf(stderr, "SETTINGS ignored target_fps\n");
    if ([fullscreen isKindOfClass:[NSNumber class]]) current.fullscreen = [fullscreen boolValue];
    else if (fullscreen) fprintf(stderr, "SETTINGS ignored fullscreen\n");
    id metalfx = stored[@"metalfx_upscale"];
    NSUInteger mode = [metalfx isKindOfClass:[NSString class]] ? [metalfx_names() indexOfObject:metalfx] : NSNotFound;
    if (mode != NSNotFound) current.metalfx = (int)mode;
    else if ([metalfx isKindOfClass:[NSNumber class]]) current.metalfx = [metalfx boolValue] ? ZELDA_METALFX_WINDOW : ZELDA_METALFX_OFF;   // earlier on/off switch
    else if (metalfx) fprintf(stderr, "SETTINGS ignored metalfx_upscale\n");
    id sound = stored[@"sound"], performance = stored[@"performance_overlay"], vibration = stored[@"vibration"];
    if ([sound isKindOfClass:[NSNumber class]]) current.muted = ![sound boolValue];
    else if (sound) fprintf(stderr, "SETTINGS ignored sound\n");
    if ([performance isKindOfClass:[NSNumber class]]) current.hide_performance = ![performance boolValue];
    else if (performance) fprintf(stderr, "SETTINGS ignored performance_overlay\n");
    mode = [vibration isKindOfClass:[NSString class]] ? [vibration_names() indexOfObject:vibration] : NSNotFound;
    if (mode != NSNotFound) current.vibration = (int)mode;
    else if ([vibration isEqual:@"normal"] || [vibration isEqual:@"strong"]) current.vibration = ZELDA_VIBRATION_ON;
    else if (vibration) fprintf(stderr, "SETTINGS ignored vibration\n");
    id render = stored[@"render_resolution"];
    mode = [render isKindOfClass:[NSString class]] ? [render_names() indexOfObject:render] : NSNotFound;
    if (mode != NSNotFound) current.render = (int)mode;
    else if ([render isEqual:@"896p"] || [render isEqual:@"balanced"]) current.render = ZELDA_RENDER_ORIGINAL;
    else if ([render isEqual:@"performance"]) current.render = ZELDA_RENDER_720;
    else if ([render isEqual:@"480p"]) current.render = ZELDA_RENDER_640;
    else if (render) fprintf(stderr, "SETTINGS ignored render_resolution\n");
    id shaders = stored[@"shader_compile"];
    if ([shaders isEqual:@"wait"] || [shaders isEqual:@"background"]) current.shader_wait = [shaders isEqual:@"wait"];
    else if (shaders) fprintf(stderr, "SETTINGS ignored shader_compile\n");
}

zelda_settings_t zelda_settings(void) {
    @autoreleasepool {
        pthread_mutex_lock(&lock); load_locked(); zelda_settings_t copy = current; pthread_mutex_unlock(&lock);
        return copy;
    }
}

bool zelda_settings_store(zelda_settings_t settings) {
    if (!zelda_settings_valid_fps(settings.target_fps) ||
        settings.metalfx < 0 || settings.metalfx > ZELDA_METALFX_4K || settings.vibration < 0 || settings.vibration > ZELDA_VIBRATION_ON ||
        settings.render < 0 || settings.render > ZELDA_RENDER_640) return false;
    @autoreleasepool {
        pthread_mutex_lock(&lock); load_locked();
        NSMutableDictionary *next = [stored mutableCopy];
        next[@"target_fps"] = @(settings.target_fps); next[@"fullscreen"] = @(settings.fullscreen);
        [next removeObjectForKey:@"dynamic_resolution"];
        next[@"metalfx_upscale"] = metalfx_names()[(NSUInteger)settings.metalfx];
        next[@"sound"] = @((bool)!settings.muted); next[@"performance_overlay"] = @((bool)!settings.hide_performance);
        next[@"vibration"] = vibration_names()[(NSUInteger)settings.vibration];
        next[@"render_resolution"] = render_names()[(NSUInteger)settings.render];
        next[@"shader_compile"] = settings.shader_wait ? @"wait" : @"background";
        NSData *data = [NSJSONSerialization dataWithJSONObject:next options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:nil];
        bool ok = data && [data writeToFile:settings_path() options:NSDataWritingAtomic error:nil];
        if (ok) { stored = next; current = settings; }
        pthread_mutex_unlock(&lock);
        if (!ok) fprintf(stderr, "SETTINGS could not write %s\n", settings_path().UTF8String);
        return ok;
    }
}
