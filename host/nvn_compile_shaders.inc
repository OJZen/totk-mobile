#include <TargetConditionals.h>
#if TARGET_OS_IPHONE
int zelda_runtime_shaders(const char *shaders, const char *library);   // ios/shaders/RuntimeShaders.cs
#endif
static void zelda_compile_shaders(void) {
#if TARGET_OS_IPHONE
    // The translator, SPIRV-Cross and the library checks run in process with the chain the libraries were prepared with.
    MLOG("Preparing captured shaders on the device");
    if (zelda_runtime_shaders(g_msl_dir.stringByDeletingLastPathComponent.fileSystemRepresentation, g_msl_dir.fileSystemRepresentation))
        MDIE("Runtime shader compilation failed; see the log above");
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
