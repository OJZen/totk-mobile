#include "shader_status.h"
// A fragment shader may write depth while the pass has no depth attachment (no depth target, or stencil only).
// NVN discards that output; Metal rejects the pipeline ("shader writes to depth"). Such pipelines use a copy of the
// fragment function without the depth output. Every Zelda depth write is the SPIR-V-Cross pair
// `float gl_FragDepth [[depth(any)]];` / `out.gl_FragDepth = value;` (73 of the shipped fragment shaders).
#import <objc/runtime.h>

static NSString *zelda_depthless_source(NSString *source) {
    if (!source) return nil;
    NSMutableString *result = [source mutableCopy];
    NSRegularExpression *member = [NSRegularExpression regularExpressionWithPattern:@"^[ \\t]*float gl_FragDepth \\[\\[depth\\(\\w+\\)\\]\\];\\n"
        options:NSRegularExpressionAnchorsMatchLines error:nil];
    NSRegularExpression *store = [NSRegularExpression regularExpressionWithPattern:@"\\bout\\.gl_FragDepth = ([^;\\n]+);" options:0 error:nil];
    if ([member replaceMatchesInString:result options:0 range:NSMakeRange(0, result.length) withTemplate:@""] != 1) return nil;
    [store replaceMatchesInString:result options:0 range:NSMakeRange(0, result.length) withTemplate:@"(void)($1);"];
    if ([result rangeOfString:@"gl_FragDepth"].location != NSNotFound || [result rangeOfString:@"[[depth("].location != NSNotFound) return nil;
    return result;
}

#ifndef ZELDA_DEPTH_OUTPUT_ANALYSIS_ONLY
// Loaded functions remember their source file, runtime-built ones their text, so a depth-free copy can be compiled later.
static char z_function_path, z_function_text;
static void zelda_function_source(id<MTLFunction> function, NSString *path, NSString *text) {
    if (!function) return;
    if (path) function.label = path.lastPathComponent.stringByDeletingPathExtension;
    if (path) objc_setAssociatedObject(function, &z_function_path, path, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    if (text) objc_setAssociatedObject(function, &z_function_text, text, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}
static NSString *zelda_function_text(id<MTLFunction> function) {
    NSString *text = objc_getAssociatedObject(function, &z_function_text);
    if (text) return text;
    NSString *path = objc_getAssociatedObject(function, &z_function_path);
    return path ? [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil] : nil;
}
static os_unfair_lock z_depthless_lock = OS_UNFAIR_LOCK_INIT;
static NSMapTable *z_depthless;   // function -> depth-free copy, or NSNull when it writes no depth
static uint64_t z_depthless_functions;
// The depth-free copy of a function, or NSNull when it writes no depth. Takes no lock: compiles may run in parallel.
static id zelda_depthless_make(id<MTLFunction> function, NvShader *shader) {
    NSString *text = zelda_function_text(function);
    if (!text || [text rangeOfString:@"[[depth("].location == NSNotFound) return NSNull.null;
    NSString *source = zelda_depthless_source(text);
    if (!source) MDIE("Depth output of fragment shader %s could not be removed", shader ? shader->key : "?");
    MTLCompileOptions *options = nil; source = zelda_shader_math(source, &options); NSError *error = nil;
    id<MTLLibrary> library = ZELDA_SHADER_BUILD([g_dev newLibraryWithSource:source options:options error:&error]);
    id<MTLFunction> copy = library ? [library newFunctionWithName:function.name] : nil;
    if (!copy) MDIE("Depth-free fragment shader %s failed: %s", shader ? shader->key : "?", error.localizedDescription.UTF8String);
    copy.label = [NSString stringWithFormat:@"%@ depthless", function.label ?: function.name];
    if (shader && shader->rf.nb && [copy newArgumentEncoderWithBufferIndex:shader->rf.abi].encodedLength != shader->arg_len)
        MDIE("Depth-free fragment shader changed the argument buffer layout");
    return copy;
}
// Called with z_depthless_lock held.
static void zelda_depthless_store(id<MTLFunction> function, NvShader *shader, id made) {
    if (made != NSNull.null) {
        z_depthless_functions++;
        MLOG("Depthless pass: removed depth output from fragment shader %s (variant %llu)", shader ? shader->key : "?", z_depthless_functions);
    }
    [z_depthless setObject:made forKey:function];
}
static id zelda_depthless_lookup(id<MTLFunction> function, NvShader *shader) {
    if (!z_depthless) z_depthless = [NSMapTable strongToStrongObjectsMapTable];
    id cached = [z_depthless objectForKey:function];
    if (cached) return cached;
    cached = zelda_depthless_make(function, shader);
    zelda_depthless_store(function, shader, cached);
    return cached;
}
// Background preparation (shader_async.h): whether the depth-free copy is known, starting its compile if not.
static NSHashTable *z_depthless_pending;
static int zelda_depthless_ready(id<MTLFunction> function, NvShader *shader) {
    if (!function) return 1;
    os_unfair_lock_lock(&z_depthless_lock);
    if (!z_depthless) z_depthless = [NSMapTable strongToStrongObjectsMapTable];
    if (!z_depthless_pending) z_depthless_pending = [NSHashTable hashTableWithOptions:NSPointerFunctionsObjectPointerPersonality];
    int known = [z_depthless objectForKey:function] != nil, started = [z_depthless_pending containsObject:function];
    if (!known && !started) [z_depthless_pending addObject:function];
    os_unfair_lock_unlock(&z_depthless_lock);
    if (known || started) return known;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        id made = zelda_depthless_make(function, shader);
        os_unfair_lock_lock(&z_depthless_lock);
        if (![z_depthless objectForKey:function]) zelda_depthless_store(function, shader, made);
        [z_depthless_pending removeObject:function];
        os_unfair_lock_unlock(&z_depthless_lock);
    });
    return 0;
}
static int zelda_function_writes_depth(id<MTLFunction> function, NvShader *shader) {
    if (!function) return 0;
    os_unfair_lock_lock(&z_depthless_lock);
    id cached = zelda_depthless_lookup(function, shader);
    os_unfair_lock_unlock(&z_depthless_lock);
    return cached != NSNull.null;
}
// The fragment function of a pipeline whose pass has no depth attachment.
static id<MTLFunction> zelda_depthless_function(id<MTLFunction> function, NvShader *shader) {
    if (!function) return function;
    os_unfair_lock_lock(&z_depthless_lock);
    id cached = zelda_depthless_lookup(function, shader);
    os_unfair_lock_unlock(&z_depthless_lock);
    return cached == NSNull.null ? function : cached;
}
#endif
