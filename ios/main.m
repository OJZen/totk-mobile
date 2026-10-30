#import <UIKit/UIKit.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <AVFAudio/AVFAudio.h>
#import <GameController/GameController.h>
#include <mach/mach.h>
#include <os/proc.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>
#include "dm.h"
#include "kernel.h"
#include "input.h"
#include "gamepad.h"
#include "audio.h"
#include "settings.h"
#include "performance.h"
#include "performance_metrics.h"
#include "game_timing.h"
#include "shader_prep.h"
#include "dmsh_chain.h"

extern int zelda_main(int, char **);
extern void dm_gfx_suspend(int);
extern void mtl_present_discard(int);
extern int zelda_runtime_shaders(const char *shaders, const char *library);   // ios/shaders/RuntimeShaders.cs
static CAMetalLayer *metal_layer;
static UILabel *status_label, *performance_label;
static UIStackView *performance_panel;
static UIButton *performance_toggle;
static BOOL performance_expanded = YES;
static ZeldaCPUUsage cpu_usage;
static BOOL launched, guest_running;
static uint32_t vsync_write, vsync_read;
static pthread_mutex_t vsync_mutex = PTHREAD_MUTEX_INITIALIZER;
static void (*resource_report)(uint32_t, const char *);
static NSString *documents;
static GCVirtualController *touch_controller;

static double physical_gib(void) {
    task_vm_info_data_t info={0};mach_msg_type_number_t count=TASK_VM_INFO_COUNT;
    return task_info(mach_task_self(),TASK_VM_INFO,(task_info_t)&info,&count)==KERN_SUCCESS?info.phys_footprint/1073741824.0:-1;
}

static NSString *metric(double value, NSString *unit) {
    return isfinite(value) && value >= 0 ? [NSString stringWithFormat:@"%.1f%@", value, unit] : @"N/A";
}

void *front_metal_layer(void) { return (__bridge void *)metal_layer; }
void crash_set_wait_dump(void (*callback)(void)) { (void)callback; }
void dm_res_report_register(void (*callback)(uint32_t, const char *)) { resource_report = callback; }
uint32_t front_vsync_handle(void) {
    pthread_mutex_lock(&vsync_mutex);
    if (!vsync_write && !kernel_event_create(&vsync_write, &vsync_read)) abort();
    uint32_t copy = kernel_event_copy(vsync_read);
    pthread_mutex_unlock(&vsync_mutex);
    return copy;
}

static void status(NSString *text) {
    dispatch_async(dispatch_get_main_queue(), ^{ status_label.text = text; });
}

void front_run(void (*guest)(void)) {
    NSString *directory = [[documents stringByAppendingPathComponent:@"work/shaders"]
        stringByAppendingPathComponent:@"b-" DMSH_CHAIN];
    CFAbsoluteTime began = CFAbsoluteTimeGetCurrent();
    fprintf(stderr, "SHADER_PREP begin chain=%s scope=needed platform=ios\n", DMSH_CHAIN);
    unsigned failed = shader_prep_run(metal_layer.device, directory, ^(unsigned count, unsigned total) {
        if (!(count % 500) || count == total) fprintf(stderr, "SHADER_PREP progress=%u/%u physical_gib=%.3f available_mib=%.1f\n",
            count,total,physical_gib(),os_proc_available_memory()/1048576.0);
        status([NSString stringWithFormat:@"Preparing shaders\n%u / %u\n\nFirst-time shader compilation can take a long time.\nPlease keep the app open. Later launches are usually faster.", count, total]);
    });
    fprintf(stderr, "SHADER_PREP end failed=%u seconds=%.3f\n", failed, CFAbsoluteTimeGetCurrent() - began);
    dispatch_sync(dispatch_get_main_queue(), ^{
        status_label.hidden = YES; guest_running = YES;
        for (UIView *view in status_label.superview.subviews) if ([view isKindOfClass:UIButton.class]) {
            view.hidden=view.tag==0;((UIButton *)view).enabled=view.tag!=0;
        }
        gamepad_start(); gamepad_vibration_mode(zelda_settings().vibration);
        if (!GCController.controllers.count) {
            GCVirtualControllerConfiguration *config = [GCVirtualControllerConfiguration new];
            config.elements = [NSSet setWithArray:@[GCInputLeftThumbstick, GCInputRightThumbstick,
                GCInputButtonA, GCInputButtonB, GCInputButtonX, GCInputButtonY,
                GCInputLeftShoulder, GCInputRightShoulder, GCInputLeftTrigger, GCInputRightTrigger]];
            touch_controller = [[GCVirtualController alloc] initWithConfiguration:config];
            [touch_controller connectWithReplyHandler:^(NSError *error) {
                if (error) fprintf(stderr, "TOUCH controller failed code=%ld\n", (long)error.code);
            }];
        }
    });
    guest();
}

static void *boot(void *unused) {
    (void)unused;
    @autoreleasepool {
        NSString *tables = [NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"patched"];
        BOOL load_only = [NSProcessInfo.processInfo.arguments containsObject:@"--load-only"];
        char *argv[] = {"ZeldaMobile", (char *)tables.fileSystemRepresentation, "--load-only", NULL};
        int result = zelda_main(load_only ? 3 : 2, argv);
        fprintf(stderr, "IOS_HOST returned=%d load_only=%d\n", result, load_only);
        status([NSString stringWithFormat:load_only ? @"Loader check finished (%d)" : @"Game stopped (%d)", result]);
    }
    return NULL;
}

static void start_game(void) {
    if (launched) return;
    BOOL load_only = [NSProcessInfo.processInfo.arguments containsObject:@"--load-only"];
    NSString *romfs = [documents stringByAppendingPathComponent:@"work/romfs-host.bin"];
    NSString *shaders = [documents stringByAppendingPathComponent:@"work/shaders"];
    NSFileManager *fm = NSFileManager.defaultManager;
    if (!load_only) {
        NSData *data=[NSData dataWithContentsOfFile:[shaders stringByAppendingPathComponent:@"ios-ready.json"]];
        id ready=data?[NSJSONSerialization JSONObjectWithData:data options:0 error:nil]:nil;
        NSDictionary *info=[fm attributesOfItemAtPath:romfs error:nil];
        NSArray *names=[fm contentsOfDirectoryAtPath:[shaders stringByAppendingPathComponent:@"b-" DMSH_CHAIN] error:nil];
        NSUInteger count=0;for(NSString *name in names) if([name hasSuffix:@".metallib"])count++;
        if (![ready isKindOfClass:NSDictionary.class] || ![ready[@"chain"] isEqual:@DMSH_CHAIN] ||
            ![ready[@"romfs_bytes"] isKindOfClass:NSNumber.class] || ![ready[@"libraries"] isKindOfClass:NSNumber.class] ||
            ![ready[@"romfs_bytes"] unsignedLongLongValue] || [ready[@"romfs_bytes"] unsignedLongLongValue]!=[info[NSFileSize] unsignedLongLongValue] ||
            !count || count<[ready[@"libraries"] unsignedIntegerValue]) {
            status(@"Game files are not imported yet.\nFinish the transfer, then tap Retry."); return;
        }
    }
    launched = YES;
    // Imported game assets are reproducible; keep only the player's save eligible for device backup.
    for (NSString *path in @[romfs,shaders]) if ([fm fileExistsAtPath:path]) {
        NSError *issue=nil;
        if (![[NSURL fileURLWithPath:path] setResourceValue:@YES forKey:NSURLIsExcludedFromBackupKey error:&issue])
            fprintf(stderr,"IOS_ASSET backup exclusion failed code=%ld\n",(long)issue.code);
    }
    for (NSString *key in @[@"work", @"work/save", @"work/logs", @"work/tmp", @"work/shaders/source", @"work/shaders/variants"]) {
        if (![fm createDirectoryAtPath:[documents stringByAppendingPathComponent:key] withIntermediateDirectories:YES attributes:nil error:nil]) abort();
    }
    setenv("ZELDA_ROMFS", romfs.fileSystemRepresentation, 1);
    setenv("ZELDA_SHARED_FONT", [[NSBundle.mainBundle.resourcePath stringByAppendingPathComponent:@"shared.ttf"] fileSystemRepresentation], 1);
    setenv("ZELDA_SETTINGS", [[documents stringByAppendingPathComponent:@"work/settings.json"] fileSystemRepresentation], 1);
    setenv("DM_MSL_DIR", shaders.fileSystemRepresentation, 1);
    setenv("DM_NVN_SHMAP", [[shaders stringByAppendingPathComponent:@"shader_map.tsv"] fileSystemRepresentation], 1);
    setenv("DM_PSO_USER", [[shaders stringByAppendingPathComponent:@"pso_user.tsv"] fileSystemRepresentation], 1);
    setenv("DM_PSO_MANIFEST", "off", 1);
    setenv("DM_NVN_TBDR", "0", 1);
    setenv("DM_PRESENT_LINK", "0", 1);
    if (!getenv("ZELDA_TIMEOUT_SECONDS")) setenv("ZELDA_TIMEOUT_SECONDS", "0", 1);
    audio_mute(zelda_settings().muted);
    NSError *error = nil;
    AVAudioSession *session = AVAudioSession.sharedInstance;
    if (![session setCategory:AVAudioSessionCategoryPlayback error:&error] || ![session setActive:YES error:&error])
        fprintf(stderr, "AUDIO session activation failed code=%ld\n", (long)error.code);
    status(@"Loading game modules…");
    pthread_attr_t attr; pthread_t thread;
    if (pthread_attr_init(&attr) || pthread_attr_setstacksize(&attr,8<<20) || pthread_create(&thread,&attr,boot,NULL)) abort();
    pthread_attr_destroy(&attr); pthread_detach(thread);
}

@interface ZeldaView : UIView
@end
@implementation ZeldaView
+ (Class)layerClass { return CAMetalLayer.class; }
- (void)layoutSubviews {
    [super layoutSubviews];
    CGFloat scale = self.window.screen.scale ?: UIScreen.mainScreen.scale;
    metal_layer.contentsScale = scale;
    metal_layer.drawableSize = CGSizeMake(MAX(1,round(self.bounds.size.width*scale)),MAX(1,round(self.bounds.size.height*scale)));
}
@end

@interface ZeldaController : UIViewController
@property (nonatomic, strong) CADisplayLink *displayLink;
@property (nonatomic, strong) NSTimer *monitor;
@end
@implementation ZeldaController
- (void)loadView {
    self.view = [[ZeldaView alloc] initWithFrame:UIScreen.mainScreen.bounds];
    self.view.backgroundColor = UIColor.blackColor;
    metal_layer = (CAMetalLayer *)self.view.layer;
    metal_layer.device = MTLCreateSystemDefaultDevice();
    metal_layer.pixelFormat = MTLPixelFormatBGRA8Unorm;
    if (!metal_layer.device) abort();
    fprintf(stderr,"IOS_DEVICE gpu=%s memory_gib=%.3f available_mib=%.1f bc=%d metal4=%d\n",
        metal_layer.device.name.UTF8String,NSProcessInfo.processInfo.physicalMemory/1073741824.0,
        os_proc_available_memory()/1048576.0,metal_layer.device.supportsBCTextureCompression,
        [metal_layer.device supportsFamily:MTLGPUFamilyMetal4]);
    fprintf(stderr,"IOS_METAL_COUNTER_SETS count=%lu\n",(unsigned long)metal_layer.device.counterSets.count);
    for (id<MTLCounterSet> set in metal_layer.device.counterSets)
        fprintf(stderr,"IOS_METAL_COUNTER_SET name=%s\n",set.name.UTF8String);
    status_label = [UILabel new]; status_label.textColor = UIColor.whiteColor;
    status_label.numberOfLines = 0; status_label.textAlignment = NSTextAlignmentCenter;
    status_label.font = [UIFont systemFontOfSize:22 weight:UIFontWeightMedium];
    status_label.translatesAutoresizingMaskIntoConstraints = NO; [self.view addSubview:status_label];
    performance_label = [UILabel new]; performance_label.textColor = UIColor.whiteColor;
    performance_label.numberOfLines = 0;
    performance_label.font = [[UIFontMetrics metricsForTextStyle:UIFontTextStyleCaption1]
        scaledFontForFont:[UIFont monospacedSystemFontOfSize:11 weight:UIFontWeightRegular]];
    performance_label.adjustsFontForContentSizeCategory = YES;
    performance_toggle = [UIButton buttonWithType:UIButtonTypeCustom];
    [performance_toggle setTitleColor:UIColor.whiteColor forState:UIControlStateNormal];
    performance_toggle.contentHorizontalAlignment = UIControlContentHorizontalAlignmentLeft;
    performance_toggle.titleLabel.font = [UIFont monospacedSystemFontOfSize:13 weight:UIFontWeightSemibold];
    [performance_toggle.heightAnchor constraintGreaterThanOrEqualToConstant:44].active = YES;
    [performance_toggle addTarget:self action:@selector(togglePerformance:) forControlEvents:UIControlEventTouchUpInside];
    performance_panel = [[UIStackView alloc] initWithArrangedSubviews:@[performance_toggle,performance_label]];
    performance_panel.axis = UILayoutConstraintAxisVertical; performance_panel.spacing = 4;
    performance_panel.layoutMarginsRelativeArrangement = YES;
    performance_panel.layoutMargins = UIEdgeInsetsMake(4,10,10,10);
    performance_panel.backgroundColor = [UIColor.blackColor colorWithAlphaComponent:.78];
    performance_panel.layer.cornerRadius = 8;
    performance_panel.translatesAutoresizingMaskIntoConstraints = NO; [self.view addSubview:performance_panel];
    [NSLayoutConstraint activateConstraints:@[
        [status_label.centerXAnchor constraintEqualToAnchor:self.view.centerXAnchor],
        [status_label.centerYAnchor constraintEqualToAnchor:self.view.centerYAnchor],
        [status_label.widthAnchor constraintLessThanOrEqualToAnchor:self.view.safeAreaLayoutGuide.widthAnchor multiplier:.85],
        [performance_panel.leadingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.leadingAnchor constant:12],
        [performance_panel.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:10],
        [performance_panel.widthAnchor constraintEqualToConstant:280]]];
    NSArray *titles=@[@"Retry",@"−",@"+"];
    for (unsigned i=0;i<titles.count;i++) {
        UIButton *button=[UIButton buttonWithType:UIButtonTypeSystem]; [button setTitle:titles[i] forState:UIControlStateNormal];
        button.tag=i;button.backgroundColor=[UIColor.blackColor colorWithAlphaComponent:.65];button.translatesAutoresizingMaskIntoConstraints=NO;
        button.enabled=i==0;
        [button addTarget:self action:@selector(press:) forControlEvents:UIControlEventTouchDown];
        [button addTarget:self action:@selector(release:) forControlEvents:UIControlEventTouchUpInside|UIControlEventTouchUpOutside|UIControlEventTouchCancel];
        [self.view addSubview:button];
        [NSLayoutConstraint activateConstraints:@[[button.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:8],
            [button.trailingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.trailingAnchor constant:-12.0-70.0*i],
            [button.widthAnchor constraintEqualToConstant:60],[button.heightAnchor constraintEqualToConstant:40]]];
    }
    self.displayLink=[CADisplayLink displayLinkWithTarget:self selector:@selector(tick:)];
    self.displayLink.preferredFrameRateRange=CAFrameRateRangeMake(60,60,60);
    [self.displayLink addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
    __weak ZeldaController *weakSelf=self;
    self.monitor=[NSTimer scheduledTimerWithTimeInterval:1 repeats:YES block:^(NSTimer *timer){(void)timer;[weakSelf updatePerformance];}];
    [NSNotificationCenter.defaultCenter addObserverForName:GCControllerDidConnectNotification object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note){
        if (touch_controller && note.object != touch_controller.controller) { [touch_controller disconnect];touch_controller=nil; }
    }];
}
- (void)viewDidAppear:(BOOL)animated { [super viewDidAppear:animated];start_game(); }
- (BOOL)prefersStatusBarHidden { return YES; }
- (BOOL)prefersHomeIndicatorAutoHidden { return YES; }
- (void)press:(UIButton *)button { if (!button.tag) start_game();else input_key(button.tag==1?48:36,1); }
- (void)release:(UIButton *)button { if (button.tag) input_key(button.tag==1?48:36,0); }
- (void)togglePerformance:(UIButton *)button {
    (void)button; performance_expanded = !performance_expanded; [self updatePerformance];
}
- (void)tick:(CADisplayLink *)link {
    (void)link;pthread_mutex_lock(&vsync_mutex);uint32_t write=vsync_write;pthread_mutex_unlock(&vsync_mutex);
    if (write) { uint64_t regs[31]={write};if (!kernel_svc(0x11,regs)||regs[0]) abort(); }
}
- (void)updatePerformance {
    ZeldaPerformance p; BOOL ready=performance_snapshot(&p);
    NSProcessInfo *process=NSProcessInfo.processInfo;
    unsigned cores=(unsigned)process.activeProcessorCount;
    double now=CACurrentMediaTime(), physical=physical_gib(), available=os_proc_available_memory()/1048576.0;
    struct rusage usage;
    double cpu=getrusage(RUSAGE_SELF,&usage)==0 ? usage.ru_utime.tv_sec+usage.ru_stime.tv_sec+
        (usage.ru_utime.tv_usec+usage.ru_stime.tv_usec)/1e6 : NAN;
    double cpu_percent=zelda_cpu_percent(&cpu_usage,now,cpu,cores);
    BOOL active=UIApplication.sharedApplication.applicationState==UIApplicationStateActive;
    BOOL fresh=ready && active && p.last_presented>0 && now-p.last_presented<2;
    double fps=fresh?p.fps:-1, gpu=fresh?p.gpu_ms:-1, target=zelda_game_frame_rate();
    // shortcut: sensor temperature and hardware GPU load have no verified sampler here; omit them until available.
    NSInteger heat=MIN(MAX(process.thermalState,0),3);
    performance_panel.hidden=zelda_settings().hide_performance;
    performance_label.hidden=!performance_expanded;
    NSString *rate=fps>=0?[NSString stringWithFormat:@"FPS %@ / %.0f",metric(fps,@""),target]:active?@"Preparing":@"Paused";
    // System buttons crossfade changing titles, making a once-per-second counter blink.
    [UIView performWithoutAnimation:^{
        [performance_toggle setTitle:[NSString stringWithFormat:@"%@  %@",rate,
            performance_expanded?@"▴":@"▾"] forState:UIControlStateNormal];
        [performance_toggle layoutIfNeeded];
    }];
    performance_toggle.accessibilityLabel=performance_expanded?@"Collapse performance details":@"Expand performance details";
    performance_toggle.accessibilityValue=performance_toggle.currentTitle;
    NSString *render=p.render_width?[NSString stringWithFormat:@"%u × %u",p.render_width,p.render_height]:@"N/A";
    NSMutableArray<NSString *> *details=[NSMutableArray array];
    if (p.render_width) [details addObject:[@"Render  " stringByAppendingString:render]];
    NSMutableArray<NSString *> *load=[NSMutableArray array];
    if (isfinite(cpu_percent)) [load addObject:[@"CPU app " stringByAppendingString:metric(cpu_percent,@"%")]];
    if (gpu>=0) [load addObject:[@"GPU " stringByAppendingString:metric(gpu,@" ms")]];
    if (load.count) [details addObject:[load componentsJoinedByString:@"  |  "]];
    if (physical>=0) [details addObject:[NSString stringWithFormat:@"Memory  %.2f GiB",physical]];
    if (process.lowPowerModeEnabled) [details addObject:@"Low Power Mode"];
    performance_label.text=[details componentsJoinedByString:@"\n"];
    fprintf(stderr,"IOS_PERF time_s=%.3f frame=%llu fps=%.3f render_fps=%.3f p95_ms=%.3f target=%.0f width=%u height=%u "
        "gpu_ms=%.3f cpu_ms=%.3f cpu_percent=%.3f cpu_cores=%u physical_gib=%.3f available_mib=%.1f "
        "thermal=%ld active=%d low_power=%d game_s=%.6f\n",
        now,(unsigned long long)p.frame,fps,fresh?p.render_fps:-1,fresh?p.frame_p95_ms:-1,target,p.render_width,p.render_height,
        gpu,fresh?p.submit_cpu_ms:-1,cpu_percent,cores,physical,available,(long)heat,active,process.lowPowerModeEnabled,zelda_game_seconds());
}
@end

@interface ZeldaSceneDelegate : UIResponder <UIWindowSceneDelegate>
@property (nonatomic, strong) UIWindow *window;
@end
@implementation ZeldaSceneDelegate
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    (void)session;(void)options;
    self.window=[[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
    self.window.rootViewController=[ZeldaController new];[self.window makeKeyAndVisible];
}
- (void)sceneWillResignActive:(UIScene *)scene { (void)scene;gamepad_active(false);audio_mute(true); }
- (void)sceneDidEnterBackground:(UIScene *)scene {
    UIApplication.sharedApplication.idleTimerDisabled=NO;
    (void)scene;if (guest_running) { mtl_present_discard(1);dm_gfx_suspend(1); }
    [AVAudioSession.sharedInstance setActive:NO error:nil];
    fprintf(stderr,"IOS_LIFECYCLE background\n");fflush(NULL);
}
- (void)sceneWillEnterForeground:(UIScene *)scene {
    (void)scene;if (guest_running) { dm_gfx_suspend(0);mtl_present_discard(0); }
}
- (void)sceneDidBecomeActive:(UIScene *)scene {
    UIApplication.sharedApplication.idleTimerDisabled=YES;
    cpu_usage=(ZeldaCPUUsage){0}; performance_reset_window();
    (void)scene;[AVAudioSession.sharedInstance setActive:YES error:nil];
    gamepad_active(true);audio_mute(zelda_settings().muted);fprintf(stderr,"IOS_LIFECYCLE active\n");
}
@end

@interface ZeldaAppDelegate : UIResponder <UIApplicationDelegate>
@end
@implementation ZeldaAppDelegate
- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)options {
    (void)app;(void)options;
    documents=NSSearchPathForDirectoriesInDomains(NSDocumentDirectory,NSUserDomainMask,YES).firstObject;
    if (chdir(documents.fileSystemRepresentation)) abort();
    rename("boot.log","boot.previous.log");
    if (!freopen("boot.log","w",stderr)) abort();setvbuf(stderr,NULL,_IOLBF,0);
    dup2(STDERR_FILENO,STDOUT_FILENO);setvbuf(stdout,NULL,_IOLBF,0);
    struct rlimit files;if (!getrlimit(RLIMIT_NOFILE,&files)) { files.rlim_cur=MIN(files.rlim_max,4096);setrlimit(RLIMIT_NOFILE,&files); }
    fprintf(stderr,"IOS_BOOT version=0.1 build=1 chain=%s\n",DMSH_CHAIN);
    NSDictionary *disk=[NSFileManager.defaultManager attributesOfFileSystemForPath:documents error:nil];
    fprintf(stderr,"IOS_STORAGE free_gib=%.3f\n",[disk[NSFileSystemFreeSize] unsignedLongLongValue]/1073741824.0);
    setenv("TZ",NSTimeZone.localTimeZone.name.UTF8String,1);tzset();
    if ([NSProcessInfo.processInfo.arguments containsObject:@"--prepare-shaders"]) {
        // Device check of the shader compiler: prepares pending requests as a missing shader would, then stops.
        int result=zelda_runtime_shaders("work/shaders","work/shaders/b-" DMSH_CHAIN);
        fprintf(stderr,"IOS_SHADER_PREP returned=%d\n",result);exit(result);
    }
    return YES;
}
- (void)applicationDidReceiveMemoryWarning:(UIApplication *)app {
    (void)app;fprintf(stderr,"IOS_MEMORY_WARNING available_mib=%.1f\n",os_proc_available_memory()/1048576.0);
    if (resource_report) resource_report(0,"iOS memory warning");
}
@end
int main(int argc,char **argv) { @autoreleasepool { return UIApplicationMain(argc,argv,nil,NSStringFromClass(ZeldaAppDelegate.class)); } }
