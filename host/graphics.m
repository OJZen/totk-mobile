#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <CoreVideo/CoreVideo.h>
#include "dm.h"
#include "front.h"
#include "kernel.h"
#include "input.h"
#include "gamepad.h"
#include "shader_prep.h"
#include "game_timing.h"
#include "settings.h"
#include "shader_async.h"
#include <unistd.h>

static CAMetalLayer *metal_layer;
#include "performance_overlay.inc"
static void (*guest_entry)(void);
static void (*wait_dump)(void);
static void (*resource_report)(uint32_t, const char *);
static CVDisplayLinkRef vsync_link;
static dispatch_source_t vsync_timer;
static dispatch_queue_t vsync_queue;
static uint32_t vsync_write, vsync_read;
static pthread_mutex_t vsync_lock = PTHREAD_MUTEX_INITIALIZER;

static void stop_vsync(void) {
    if (vsync_timer) {
        dispatch_source_cancel(vsync_timer);
        dispatch_sync(vsync_queue, ^{});
        vsync_timer = nil; vsync_queue = nil;
    }
    if (vsync_link) {
        CVDisplayLinkStop(vsync_link);
        CVDisplayLinkRelease(vsync_link);
        vsync_link = NULL;
    }
}

static CVReturn display_refresh(CVDisplayLinkRef link, const CVTimeStamp *now,
        const CVTimeStamp *output, CVOptionFlags flags, CVOptionFlags *out_flags, void *context) {
    (void)link; (void)now; (void)output; (void)flags; (void)out_flags; (void)context;
    // The guest display runs at most 60 Hz, including on ProMotion hosts.
    static uint64_t previous_slot;
    uint64_t slot = now_ns() / 16666667;
    if (slot != previous_slot) {
        previous_slot = slot;
        uint64_t regs[31] = {vsync_write};
        if (!kernel_svc(0x11, regs) || regs[0]) abort();
    }
    return kCVReturnSuccess;
}

uint32_t front_vsync_handle(void) {
    pthread_mutex_lock(&vsync_lock);
    if (!vsync_write) {
        if (!kernel_event_create(&vsync_write, &vsync_read)) die("display event handles exhausted");
        CVReturn result = CVDisplayLinkCreateWithActiveCGDisplays(&vsync_link);
        if (!result) result = CVDisplayLinkSetOutputCallback(vsync_link, display_refresh, NULL);
        if (!result) result = CVDisplayLinkStart(vsync_link);
        if (result) {
            if (vsync_link) { CVDisplayLinkRelease(vsync_link); vsync_link = NULL; }
            // A sleeping/disconnected host display must not stop the emulated console clock.
            vsync_queue = dispatch_queue_create("zelda.display-clock", DISPATCH_QUEUE_SERIAL);
            vsync_timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, vsync_queue);
            dispatch_source_set_timer(vsync_timer, DISPATCH_TIME_NOW, 16666667, 100000);
            dispatch_source_set_event_handler(vsync_timer, ^{ display_refresh(NULL, NULL, NULL, 0, NULL, NULL); });
            dispatch_resume(vsync_timer);
            LOG("VI virtual 60 Hz clock started; host display link unavailable code=%d", result);
        } else LOG("VI host refresh event started");
        if (atexit(stop_vsync)) die("display cleanup registration failed");
    }
    uint32_t copy = kernel_event_copy(vsync_read);
    pthread_mutex_unlock(&vsync_lock);
    return copy;
}

void *front_metal_layer(void) { return (__bridge void *)metal_layer; }
void crash_set_wait_dump(void (*fn)(void)) { wait_dump = fn; }
void dm_res_report_register(void (*fn)(uint32_t, const char *)) { resource_report = fn; }

static void *run_guest(void *unused) {
    (void)unused;
    @autoreleasepool { guest_entry(); }
    dispatch_async(dispatch_get_main_queue(), ^{ [NSApp terminate:nil]; });
    return NULL;
}

// The drawable follows the window's backing pixels (Retina, resizing, full screen); presentation scales the game
// image into it with the original aspect ratio.
static void fit_drawable(NSWindow *window) {
    NSView *view = window.contentView;
    CGFloat scale = window.backingScaleFactor;
    NSSize size = view.bounds.size;
    CGSize pixels = CGSizeMake(MAX(1, round(size.width * scale)), MAX(1, round(size.height * scale)));
    metal_layer.contentsScale = scale;
    if (!CGSizeEqualToSize(metal_layer.drawableSize, pixels)) metal_layer.drawableSize = pixels;
}

static void store_settings(void (^change)(zelda_settings_t *settings)) {
    zelda_settings_t settings = zelda_settings();
    change(&settings);
    zelda_settings_store(settings);
}

#include "control_panel.inc"
// Shared by the menu bar and the control panel: apply at once, save, then refresh the panel.
static void apply_target_fps(unsigned fps) {
    if (zelda_set_target_fps(fps)) {
        store_settings(^(zelda_settings_t *settings) { settings->target_fps = fps; });
        LOG("SETTINGS target_fps=%u", fps);
    }
    controls_refresh();
}
static void apply_render(int mode) {
    if (zelda_set_render_scale(zelda_render_scale_factor(mode))) {
        store_settings(^(zelda_settings_t *settings) { settings->render = mode; });
        LOG("SETTINGS render_resolution=%d scale=%.2f", mode, zelda_render_scale());
    }
    controls_refresh();
}
static void apply_metalfx(int mode) {
    store_settings(^(zelda_settings_t *settings) { settings->metalfx = mode; });
    zelda_present_metalfx(mode);
    controls_refresh();
}
static void apply_sound(bool on) {
    audio_mute(!on);
    store_settings(^(zelda_settings_t *settings) { settings->muted = !on; });
    controls_refresh();
}
static void apply_performance(bool shown) {
    performance_set_visible(shown);
    store_settings(^(zelda_settings_t *settings) { settings->hide_performance = !shown; });
    controls_refresh();
}
static void apply_shader_wait(bool wait) {
    zelda_set_shader_wait(wait);
    store_settings(^(zelda_settings_t *settings) { settings->shader_wait = wait; });
    controls_refresh();
}
static void apply_vibration(int mode) {
    gamepad_vibration_mode(mode);
    store_settings(^(zelda_settings_t *settings) { settings->vibration = mode; });
    LOG("SETTINGS vibration=%d", mode);
    controls_refresh();
}

@interface ZeldaWindowDelegate : NSObject <NSWindowDelegate>
@end
@implementation ZeldaWindowDelegate
- (void)windowDidResignKey:(NSNotification *)notification { (void)notification; gamepad_active(false); }
- (void)windowDidBecomeKey:(NSNotification *)notification { (void)notification; gamepad_active(true); }
- (void)windowDidResize:(NSNotification *)notification { fit_drawable(notification.object); }
- (void)windowDidChangeBackingProperties:(NSNotification *)notification { fit_drawable(notification.object); }
- (void)windowDidEnterFullScreen:(NSNotification *)notification {
    fit_drawable(notification.object);
    store_settings(^(zelda_settings_t *settings) { settings->fullscreen = true; });
    controls_refresh();
}
- (void)windowDidExitFullScreen:(NSNotification *)notification {
    fit_drawable(notification.object);
    store_settings(^(zelda_settings_t *settings) { settings->fullscreen = false; });
    controls_refresh();
}
- (void)windowWillClose:(NSNotification *)notification {
    (void)notification;
    stop_vsync();
    gamepad_stop(); performance_stop();
    if (resource_report) resource_report(0, "window closed");
    // Reclaim live guest threads and host resources together; per-resource exit handlers race ongoing IPC.
    fflush(NULL); _exit(0);
}
@end

@interface ZeldaMenu : NSObject <NSMenuItemValidation>
@property (weak) NSWindow *window;
@end
@implementation ZeldaMenu
- (void)targetFPS:(NSMenuItem *)item { apply_target_fps((unsigned)item.tag); }
- (void)fullScreen:(id)sender { (void)sender; [self.window toggleFullScreen:nil]; }
- (void)metalFX:(NSMenuItem *)item { apply_metalfx((int)item.tag); }
- (void)renderResolution:(NSMenuItem *)item { apply_render((int)item.tag); }
- (void)togglePerformance:(id)sender { (void)sender; apply_performance(!performance_visible()); }
- (void)toggleShaderWait:(id)sender { (void)sender; apply_shader_wait(!zelda_settings().shader_wait); }
- (void)toggleControls:(id)sender { (void)sender; controls_toggle(); }
- (void)quit:(id)sender { (void)sender; [self.window performClose:nil]; }
- (BOOL)validateMenuItem:(NSMenuItem *)item {
    if (item.action == @selector(targetFPS:)) {
        item.state = zelda_target_fps() == (unsigned)item.tag ? NSControlStateValueOn : NSControlStateValueOff;
        return zelda_target_fps() != 0;
    }
    if (item.action == @selector(renderResolution:)) {
        item.state = zelda_settings().render == item.tag ? NSControlStateValueOn : NSControlStateValueOff;
        return zelda_render_scale() != 0;
    }
    if (item.action == @selector(metalFX:)) item.state = zelda_settings().metalfx == item.tag ? NSControlStateValueOn : NSControlStateValueOff;
    if (item.action == @selector(toggleShaderWait:)) item.state = zelda_settings().shader_wait ? NSControlStateValueOff : NSControlStateValueOn;
    if (item.action == @selector(togglePerformance:)) item.state = performance_visible() ? NSControlStateValueOn : NSControlStateValueOff;
    if (item.action == @selector(fullScreen:))
        item.title = self.window.styleMask & NSWindowStyleMaskFullScreen ? @"Exit Full Screen" : @"Enter Full Screen";
    return YES;
}
@end

static NSMenuItem *menu_item(NSString *title, SEL action, NSString *key, NSEventModifierFlags modifiers, id target) {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:action keyEquivalent:key];
    item.keyEquivalentModifierMask = modifiers; item.target = target;
    return item;
}

static void install_menu(ZeldaMenu *actions) {
    NSMenu *bar = [NSMenu new];
    NSMenu *app = [[NSMenu alloc] initWithTitle:@"Zelda-Mobile"];
    [app addItem:menu_item(@"Controls", @selector(toggleControls:), @",", NSEventModifierFlagCommand, actions)];
    [app addItem:[NSMenuItem separatorItem]];
    [app addItem:menu_item(@"Quit Zelda-Mobile", @selector(quit:), @"q", NSEventModifierFlagCommand, actions)];
    NSMenu *view = [[NSMenu alloc] initWithTitle:@"View"];
    NSMenu *rates = [[NSMenu alloc] initWithTitle:@"Target Frame Rate"];
    for (unsigned fps = 30; fps <= 90; fps += 30) {
        NSMenuItem *item = menu_item([NSString stringWithFormat:@"%u FPS", fps], @selector(targetFPS:), @"", 0, actions);
        item.tag = fps; [rates addItem:item];
    }
    NSMenuItem *rate = [[NSMenuItem alloc] initWithTitle:@"Target Frame Rate" action:nil keyEquivalent:@""]; rate.submenu = rates;
    [view addItem:rate];
    NSMenu *renders = [[NSMenu alloc] initWithTitle:@"Render Resolution"];
    NSString *render_titles[] = {@"Original (Game Selected)", @"1080 (1920×1080)", @"720 (1280×720)", @"640 (1136×640)"};
    for (int i = 0; i < 4; i++) {
        NSMenuItem *item = menu_item(render_titles[i], @selector(renderResolution:), @"", 0, actions);
        item.tag = i; [renders addItem:item];
    }
    NSMenuItem *render = [[NSMenuItem alloc] initWithTitle:@"Render Resolution" action:nil keyEquivalent:@""]; render.submenu = renders;
    [view addItem:render];
    [view addItem:menu_item(@"Enter Full Screen", @selector(fullScreen:), @"f", NSEventModifierFlagCommand | NSEventModifierFlagControl, actions)];
    NSMenu *scales = [[NSMenu alloc] initWithTitle:@"MetalFX Upscaling"];
    NSString *scale_titles[] = {@"Off", @"Scale to Window", @"Scale to 4K (3840×2160)"};
    for (int i = 0; i < 3; i++) {
        NSMenuItem *item = menu_item(scale_titles[i], @selector(metalFX:), @"", 0, actions);
        item.tag = i; [scales addItem:item];
    }
    NSMenuItem *scale = [[NSMenuItem alloc] initWithTitle:@"MetalFX Upscaling" action:nil keyEquivalent:@""]; scale.submenu = scales;
    [view addItem:scale];
    [view addItem:menu_item(@"Compile New Shaders in Background", @selector(toggleShaderWait:), @"", 0, actions)];
    [view addItem:menu_item(@"Performance Overlay", @selector(togglePerformance:), @"p", NSEventModifierFlagCommand, actions)];
    for (NSMenu *menu in @[app, view]) {
        NSMenuItem *holder = [[NSMenuItem alloc] initWithTitle:menu.title action:nil keyEquivalent:@""];
        holder.submenu = menu; [bar addItem:holder];
    }
    NSApp.mainMenu = bar;
}

void front_run(void (*guest)(void)) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        NSRect frame = NSMakeRect(0, 0, 960, 540);
        const char *points = getenv("ZELDA_WINDOW_POINTS");   // e.g. 1600x900, to exercise upscaling without full screen
        unsigned pw, ph;
        if (points && sscanf(points, "%ux%u", &pw, &ph) == 2 && pw >= 640 && ph >= 360) frame.size = NSMakeSize(pw, ph);
        NSWindow *window = [[NSWindow alloc] initWithContentRect:frame
            styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
            backing:NSBackingStoreBuffered defer:NO];
        window.title = @"Zelda-Mobile";
        window.contentAspectRatio = NSMakeSize(16, 9);
        window.contentMinSize = NSMakeSize(640, 360);
        window.collectionBehavior = NSWindowCollectionBehaviorFullScreenPrimary;
        ZeldaWindowDelegate *delegate = [ZeldaWindowDelegate new];
        window.delegate = delegate;
        ZeldaMenu *actions = [ZeldaMenu new]; actions.window = window;
        // Sound used to be saved in user defaults; carry it into the settings file once.
        NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
        if ([defaults objectForKey:@"ZeldaSoundMuted"]) {
            bool muted = [defaults boolForKey:@"ZeldaSoundMuted"];
            store_settings(^(zelda_settings_t *settings) { settings->muted = muted; });
            [defaults removeObjectForKey:@"ZeldaSoundMuted"];
        }
        zelda_settings_t settings = zelda_settings();
        audio_mute(settings.muted); gamepad_vibration_mode(settings.vibration);
        install_menu(actions);
        [NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskKeyDown | NSEventMaskKeyUp | NSEventMaskFlagsChanged
            handler:^NSEvent *(NSEvent *event) {
                if (event.window != window) return event;
                // Command shortcuts belong to the menu; the game mapping uses no Command keys.
                if (event.type == NSEventTypeKeyDown && (event.modifierFlags & NSEventModifierFlagCommand)) return event;
                int down = event.type == NSEventTypeFlagsChanged ? !!(event.modifierFlags & NSEventModifierFlagShift) : event.type == NSEventTypeKeyDown;
                input_key(event.keyCode, down); return nil;
            }];
        metal_layer = [CAMetalLayer layer];
        metal_layer.device = MTLCreateSystemDefaultDevice();
        if (!metal_layer.device) die("Metal presentation device unavailable");
        metal_layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
        window.contentView.wantsLayer = YES;
        window.contentView.layer = metal_layer;
        fit_drawable(window);
        [window center]; [window makeKeyAndOrderFront:nil];
        if (settings.fullscreen) dispatch_async(dispatch_get_main_queue(), ^{ [window toggleFullScreen:nil]; });
        [NSApp activateIgnoringOtherApps:YES];
        gamepad_start(); gamepad_active(window.keyWindow);
        guest_entry = guest;
        shader_prep_begin(window.contentView, metal_layer.device, ^{
            performance_start(window, !zelda_settings().hide_performance);
            controls_start(window);
            pthread_attr_t attr;
            if (pthread_attr_init(&attr) || pthread_attr_setstacksize(&attr, 8 << 20)) abort();
            pthread_t thread;
            if (pthread_create(&thread, &attr, run_guest, NULL)) abort();
            pthread_attr_destroy(&attr); pthread_detach(thread);
        });
        [NSApp run];
    }
}
