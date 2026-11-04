#include <TargetConditionals.h>
#include <pthread.h>
static void zelda_compile_shaders_locked(void);
// The submit thread (waiting mode) and the background variant compiler (nvn_shader_async.inc) never run at once.
static void zelda_compile_shaders(void) {
    static pthread_mutex_t running = PTHREAD_MUTEX_INITIALIZER;
    pthread_mutex_lock(&running); zelda_compile_shaders_locked(); pthread_mutex_unlock(&running);
}
static void zelda_compile_shaders_locked(void) {
#if TARGET_OS_IPHONE
    // Captured requests remain in Documents/work/shaders for the Mac preparation tool.
    MDIE("Shader translation is required on the Mac; export work/shaders requests, prepare them, and sync back to iOS");
#else
    @autoreleasepool {
        NSString *root = NSFileManager.defaultManager.currentDirectoryPath;
        NSString *log = [root stringByAppendingPathComponent:@"work/logs/runtime-shaders.log"];
        if (![NSFileManager.defaultManager createFileAtPath:log contents:nil attributes:nil])
            MDIE("Cannot create runtime shader build log");
        NSFileHandle *output = [NSFileHandle fileHandleForWritingAtPath:log];
        if (!output) MDIE("Cannot open runtime shader build log");
        NSTask *task = [NSTask new];
        task.executableURL = [NSURL fileURLWithPath:[root stringByAppendingPathComponent:@"work/venv/bin/python"]];
        task.arguments = @[@"script/compile_runtime_shaders.py"];
        NSMutableDictionary *environment = [NSProcessInfo.processInfo.environment mutableCopy];
        environment[@"PYTHONDONTWRITEBYTECODE"] = @"1";
        environment[@"TMPDIR"] = [root stringByAppendingPathComponent:@"work/tmp/"];
        task.environment = environment; task.standardOutput = output; task.standardError = output;
        MLOG("Preparing captured shaders; details: work/logs/runtime-shaders.log");
        NSError *error = nil;
        if (![task launchAndReturnError:&error]) MDIE("Cannot launch local shader compiler");
        [task waitUntilExit]; [output closeFile];
        if (task.terminationReason != NSTaskTerminationReasonExit || task.terminationStatus)
            MDIE("Runtime shader compilation failed; inspect work/logs/runtime-shaders.log");
    }
#endif
}
