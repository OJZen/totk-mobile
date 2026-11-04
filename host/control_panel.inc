// Collapsible control panel in the bottom-left corner: a small handle opens the player settings (frame rate, render
// resolution, MetalFX, shader compilation, full screen, performance overlay, sound, rumble). Changes apply at once and are saved; the menu
// bar items use the same apply_* actions. The collapsed handle fades out after the pointer rests, so it does not sit
// over the game. Controls refuse first responder and keys never reach them (the window monitor forwards them to the
// guest), so the panel cannot take input focus from the game.
static void apply_target_fps(unsigned fps);
static void apply_metalfx(int mode);
static void apply_render(int mode);
static void apply_sound(bool on);
static void apply_performance(bool shown);
static void apply_vibration(int mode);
static void apply_shader_wait(bool wait);

@interface ZeldaControls : NSObject {
@public
    NSWindow *window;
    NSButton *handle, *collapse, *test;
    NSView *panel;
    NSSegmentedControl *fps, *render, *metalfx, *shaders, *vibration;
    NSSwitch *fullscreen, *performance, *sound;
    NSTextField *controller;
    NSTimer *fade, *status;
}
@end

static ZeldaControls *controls;
static const unsigned control_rates[] = {30, 60, 90};
static void controls_refresh(void);

@implementation ZeldaControls
- (void)setExpanded:(BOOL)expanded {
    panel.hidden = !expanded; handle.hidden = expanded;
    [fade invalidate]; fade = nil;
    [status invalidate]; status = nil;
    if (expanded) {
        controls_refresh();
        // Controller connection and full-screen state can change outside the panel.
        status = [NSTimer scheduledTimerWithTimeInterval:1 repeats:YES block:^(NSTimer *timer) { (void)timer; controls_refresh(); }];
    } else [self showHandle];
}
- (void)toggle:(id)sender { (void)sender; [self setExpanded:panel.hidden]; }
- (void)showHandle {
    if (!panel.hidden) return;
    handle.hidden = NO; handle.alphaValue = 1;
    [fade invalidate];
    fade = [NSTimer scheduledTimerWithTimeInterval:2.5 repeats:NO block:^(NSTimer *timer) {
        (void)timer;
        NSPoint point = [self->handle convertPoint:self->window.mouseLocationOutsideOfEventStream fromView:nil];
        if ([self->handle mouse:point inRect:self->handle.bounds]) { [self showHandle]; return; }
        [NSAnimationContext runAnimationGroup:^(NSAnimationContext *context) { context.duration = 0.3; self->handle.animator.alphaValue = 0; }
                            completionHandler:^{ if (self->handle.alphaValue == 0) self->handle.hidden = YES; }];
    }];
}
- (void)mouseMoved:(NSEvent *)event { (void)event; [self showHandle]; }
- (void)mouseEntered:(NSEvent *)event { (void)event; [self showHandle]; }
- (void)mouseExited:(NSEvent *)event { (void)event; }
- (void)fpsChanged:(NSSegmentedControl *)sender { apply_target_fps(control_rates[sender.selectedSegment]); }
- (void)renderChanged:(NSSegmentedControl *)sender { apply_render((int)sender.selectedSegment); }
- (void)metalfxChanged:(NSSegmentedControl *)sender { apply_metalfx((int)sender.selectedSegment); }
- (void)shadersChanged:(NSSegmentedControl *)sender { apply_shader_wait(sender.selectedSegment == 1); }
- (void)vibrationChanged:(NSSegmentedControl *)sender { apply_vibration((int)sender.selectedSegment); }
- (void)fullscreenChanged:(NSSwitch *)sender { (void)sender; [window toggleFullScreen:nil]; }
- (void)performanceChanged:(NSSwitch *)sender { apply_performance(sender.state == NSControlStateValueOn); }
- (void)soundChanged:(NSSwitch *)sender { apply_sound(sender.state == NSControlStateValueOn); }
- (void)testVibration:(id)sender {
    (void)sender;
    if (!gamepad_test_vibration()) controller.stringValue = @"No rumble-capable controller connected";
}
@end

static NSTextField *control_label(NSString *text, CGFloat size, NSFontWeight weight, NSColor *color) {
    NSTextField *field = [NSTextField labelWithString:text];
    field.font = [NSFont systemFontOfSize:size weight:weight]; field.textColor = color;
    return field;
}
static NSSegmentedControl *control_choice(NSArray<NSString *> *labels, SEL action, NSString *tip, CGFloat width) {
    NSSegmentedControl *choice = [NSSegmentedControl segmentedControlWithLabels:labels trackingMode:NSSegmentSwitchTrackingSelectOne
                                                                         target:controls action:action];
    choice.controlSize = NSControlSizeSmall; choice.font = [NSFont systemFontOfSize:11];
    choice.segmentDistribution = NSSegmentDistributionFillEqually; choice.refusesFirstResponder = YES; choice.toolTip = tip;
    [choice.widthAnchor constraintEqualToConstant:width].active = YES;
    return choice;
}
static NSSwitch *control_switch(SEL action, NSString *tip) {
    NSSwitch *control = [NSSwitch new];
    control.controlSize = NSControlSizeMini; control.target = controls; control.action = action;
    control.refusesFirstResponder = YES; control.toolTip = tip;
    return control;
}
static NSButton *control_icon(NSString *symbol, NSString *label, SEL action) {
    NSButton *button = [NSButton buttonWithImage:[NSImage imageWithSystemSymbolName:symbol accessibilityDescription:label] target:controls action:action];
    button.bordered = NO; button.refusesFirstResponder = YES; button.contentTintColor = NSColor.whiteColor;
    button.accessibilityLabel = label;
    return button;
}

static void controls_refresh(void) {
    if (!controls) return;
    zelda_settings_t s = zelda_settings();
    unsigned rate = zelda_target_fps();
    controls->fps.enabled = rate != 0;
    for (NSInteger i = 0; i < 3; i++) if (control_rates[i] == rate) controls->fps.selectedSegment = i;
    controls->render.enabled = zelda_render_scale() != 0;
    controls->render.selectedSegment = s.render;
    controls->metalfx.selectedSegment = zelda_present_metalfx_mode();   // ZELDA_METALFX can override the setting
    controls->shaders.selectedSegment = s.shader_wait;
    controls->vibration.selectedSegment = s.vibration;
    controls->fullscreen.state = controls->window.styleMask & NSWindowStyleMaskFullScreen ? NSControlStateValueOn : NSControlStateValueOff;
    controls->performance.state = performance_visible() ? NSControlStateValueOn : NSControlStateValueOff;
    controls->sound.state = audio_muted() ? NSControlStateValueOff : NSControlStateValueOn;
    char name[128]; bool haptics = false;
    gamepad_describe(name, sizeof name, &haptics);
    controls->controller.stringValue = !*name ? @"No controller connected" :
        [NSString stringWithFormat:@"%s · %@", name, haptics ? @"Rumble available" : @"Rumble unavailable"];
    controls->test.enabled = haptics && s.vibration == ZELDA_VIBRATION_ON;
}

static void controls_start(NSWindow *window) {
    controls = [ZeldaControls new]; controls->window = window;
    NSView *content = window.contentView;
    const CGFloat margin = 10, width = 308, inner = width - 24, choice_width = 192;

    NSButton *handle = control_icon(@"slider.horizontal.3", @"Show controls", @selector(toggle:));
    handle.frame = NSMakeRect(margin, margin, 30, 30);
    handle.wantsLayer = YES; handle.layer.backgroundColor = [NSColor colorWithWhite:0.03 alpha:0.7].CGColor; handle.layer.cornerRadius = 8;
    handle.autoresizingMask = NSViewMaxXMargin | NSViewMaxYMargin; handle.toolTip = @"Controls (⌘,)";
    controls->handle = handle;

    NSTextField *title = control_label(@"Controls", 13, NSFontWeightSemibold, NSColor.whiteColor);
    controls->collapse = control_icon(@"chevron.down", @"Hide controls", @selector(toggle:));
    controls->collapse.toolTip = @"Collapse (⌘,)";
    NSStackView *header = [NSStackView stackViewWithViews:@[title, controls->collapse]];
    header.distribution = NSStackViewDistributionEqualSpacing;

    controls->fps = control_choice(@[@"30", @"60", @"90"], @selector(fpsChanged:),
        @"Target frame rate. Game speed remains unchanged when performance falls below the target.", choice_width);
    controls->render = control_choice(@[@"Original", @"1080", @"720", @"640"], @selector(renderChanged:),
        @"Original keeps the game-selected resolution. The 1080 / 720 / 640 presets request 1920×1080, 1280×720 and 1136×640.", choice_width);
    controls->metalfx = control_choice(@[@"Off", @"Window", @"4K"], @selector(metalfxChanged:),
        @"MetalFX spatially upscales the 1920×1080 output to the window or 3840×2160. It has no effect when the window is at or below 1920×1080.", choice_width);
    controls->shaders = control_choice(@[@"Background", @"Wait"], @selector(shadersChanged:),
        @"Background: a new shader compiles while the game keeps running, so an effect seen for the first time may appear a few frames late. Wait: every frame waits for its shaders, which can stall briefly.", choice_width);
    controls->vibration = control_choice(@[@"Off", @"On"], @selector(vibrationChanged:),
        @"Enable controller rumble at the calibrated strength.", 118);
    controls->fullscreen = control_switch(@selector(fullscreenChanged:), @"⌃⌘F");
    controls->performance = control_switch(@selector(performanceChanged:), @"⌘P");
    controls->sound = control_switch(@selector(soundChanged:), @"Mute sound output while keeping game audio processing active.");
    controls->test = [NSButton buttonWithTitle:@"Test" target:controls action:@selector(testVibration:)];
    controls->test.controlSize = NSControlSizeSmall; controls->test.font = [NSFont systemFontOfSize:11];
    controls->test.refusesFirstResponder = YES; controls->test.toolTip = @"Run both controller motors for 0.4 seconds.";
    NSStackView *rumble = [NSStackView stackViewWithViews:@[controls->vibration, controls->test]];
    rumble.spacing = 6;
    controls->controller = control_label(@"", 10, NSFontWeightRegular, [NSColor colorWithWhite:1 alpha:0.5]);
    controls->controller.lineBreakMode = NSLineBreakByTruncatingTail;

    NSColor *secondary = [NSColor colorWithWhite:1 alpha:0.62];
    NSArray *rows = @[
        @[control_label(@"Frame rate", 11, NSFontWeightRegular, secondary), controls->fps],
        @[control_label(@"Resolution", 11, NSFontWeightRegular, secondary), controls->render],
        @[control_label(@"MetalFX", 11, NSFontWeightRegular, secondary), controls->metalfx],
        @[control_label(@"Shaders", 11, NSFontWeightRegular, secondary), controls->shaders],
        @[control_label(@"Fullscreen", 11, NSFontWeightRegular, secondary), controls->fullscreen],
        @[control_label(@"Performance", 11, NSFontWeightRegular, secondary), controls->performance],
        @[control_label(@"Sound", 11, NSFontWeightRegular, secondary), controls->sound],
        @[control_label(@"Rumble", 11, NSFontWeightRegular, secondary), rumble],
    ];
    NSGridView *grid = [NSGridView gridViewWithViews:rows];
    grid.rowSpacing = 8; grid.columnSpacing = 8;
    [grid columnAtIndex:0].width = inner - choice_width - 8;
    for (NSInteger i = 0; i < grid.numberOfRows; i++) [grid rowAtIndex:i].yPlacement = NSGridCellPlacementCenter;

    NSStackView *stack = [NSStackView stackViewWithViews:@[header, grid, controls->controller]];
    stack.orientation = NSUserInterfaceLayoutOrientationVertical; stack.alignment = NSLayoutAttributeLeading;
    stack.spacing = 10; stack.edgeInsets = NSEdgeInsetsMake(10, 12, 10, 12);
    [stack setCustomSpacing:6 afterView:grid];
    stack.translatesAutoresizingMaskIntoConstraints = NO;
    NSView *panel = [NSView new];
    panel.wantsLayer = YES; panel.layer.backgroundColor = [NSColor colorWithWhite:0.03 alpha:0.86].CGColor; panel.layer.cornerRadius = 10;
    panel.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    panel.accessibilityLabel = @"Controls";
    [panel addSubview:stack];
    for (NSView *view in @[header, controls->controller]) [view.widthAnchor constraintEqualToConstant:inner].active = YES;
    [NSLayoutConstraint activateConstraints:@[
        [stack.leadingAnchor constraintEqualToAnchor:panel.leadingAnchor], [stack.trailingAnchor constraintEqualToAnchor:panel.trailingAnchor],
        [stack.topAnchor constraintEqualToAnchor:panel.topAnchor], [stack.widthAnchor constraintEqualToConstant:width]]];
    panel.frame = NSMakeRect(margin, margin, width, 400);
    panel.autoresizingMask = NSViewMaxXMargin | NSViewMaxYMargin;
    panel.hidden = YES;
    controls->panel = panel;
    [content addSubview:panel]; [content addSubview:handle];
    // Size the panel to the laid-out stack (with the status text filled in), keeping its bottom-left corner.
    controls_refresh(); [panel layoutSubtreeIfNeeded];
    panel.frame = NSMakeRect(margin, margin, width, NSHeight(stack.frame));
    [content addTrackingArea:[[NSTrackingArea alloc] initWithRect:NSZeroRect
        options:NSTrackingMouseMoved | NSTrackingMouseEnteredAndExited | NSTrackingActiveInKeyWindow | NSTrackingInVisibleRect
          owner:controls userInfo:nil]];
    [controls setExpanded:NO];
    // Diagnostic: ZELDA_UI_SNAPSHOT=path.png opens the panel after 3 s and saves the overlay views once (rendered in
    // process, without the Metal image), so layout can be checked without screen-recording permission.
    const char *snapshot = getenv("ZELDA_UI_SNAPSHOT");
    if (snapshot && *snapshot) {
        NSString *path = @(snapshot);
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            [controls setExpanded:YES];
            NSBitmapImageRep *image = [content bitmapImageRepForCachingDisplayInRect:content.bounds];
            [content cacheDisplayInRect:content.bounds toBitmapImageRep:image];
            BOOL saved = [[image representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:path atomically:YES];
            LOG("UI snapshot %s %s", saved ? "saved" : "failed", path.UTF8String);
        });
    }
}
static void controls_toggle(void) { [controls toggle:nil]; }
