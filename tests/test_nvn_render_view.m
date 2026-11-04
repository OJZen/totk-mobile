#import <Metal/Metal.h>
#include "nvn_obj.h"
#include "nvn_fmt_mtl.h"
#include <assert.h>

#define MDIE(...) do { fprintf(stderr, __VA_ARGS__); abort(); } while (0)
static id<MTLDevice> g_dev;
static id<MTL4CommandBuffer> commands;
static id<MTL4ComputeCommandEncoder> g_blit;
static id<MTLResidencySet> residency;
static NSMutableArray *retired;
static void res_add(id object) { [residency addAllocation:object]; }
static void defer_obj_release(id object) { [retired addObject:object]; }
static id<MTLTexture> sampled_texture;
typedef struct { void *array_view; uint64_t array_rid; } treg_t;
static treg_t g_treg[291];
static uint64_t tex_fast_rid(int tid, void **out) {
    assert(tid == 290); *out = (__bridge void *)sampled_texture; return sampled_texture.gpuResourceID._impl;
}
#include "../host/nvn_sampled_view.inc"
static id blit(void) {
    if (!g_blit) {
        g_blit = [commands computeCommandEncoder];
        [g_blit barrierAfterQueueStages:MTLStageAll beforeStages:MTLStageAll visibilityOptions:MTL4VisibilityOptionDevice];
    }
    return g_blit;
}
static void end_blit(void) {
    if (g_blit) {
        [g_blit barrierAfterStages:MTLStageAll beforeQueueStages:MTLStageAll visibilityOptions:MTL4VisibilityOptionDevice];
        [g_blit endEncoding]; g_blit = nil;
    }
}
static void enc_b_copy_texture_buffer(id e, id<MTLTexture> s, NSUInteger sl, NSUInteger lv, MTLOrigin origin,
        MTLSize size, id<MTLBuffer> d, NSUInteger off, NSUInteger row, NSUInteger image) {
    [e copyFromTexture:s sourceSlice:sl sourceLevel:lv sourceOrigin:origin sourceSize:size toBuffer:d
        destinationOffset:off destinationBytesPerRow:row destinationBytesPerImage:image];
}
static void enc_b_copy_buffer_texture(id e, id<MTLBuffer> s, NSUInteger off, NSUInteger row, NSUInteger image,
        MTLSize size, id<MTLTexture> d, NSUInteger sl, NSUInteger lv, MTLOrigin origin) {
    [e copyFromBuffer:s sourceOffset:off sourceBytesPerRow:row sourceBytesPerImage:image sourceSize:size
        toTexture:d destinationSlice:sl destinationLevel:lv destinationOrigin:origin];
}
static int fmt_compressed(int format) { return format == NVN_FORMAT_RGTC1_UNORM; }
static int tex_need_pfv(MTLPixelFormat a, MTLPixelFormat b) { return a != b; }
static id<MTLTexture> tex_upgrade_pfv(const tex_t *t, id<MTLTexture> texture, MTLPixelFormat f) {
    (void)t; (void)texture; (void)f; abort();
}
static size_t arena_alloc(size_t size, id<MTLBuffer> *buffer) {
    *buffer = [g_dev newBufferWithLength:size + 256 options:MTLResourceStorageModeShared];
    assert(*buffer); res_add(*buffer); return 256;
}
#include "../host/nvn_render_view.inc"
static void enc_b_end(id e) {
    [e barrierAfterStages:MTLStageAll beforeQueueStages:MTLStageAll visibilityOptions:MTL4VisibilityOptionDevice];
    [e endEncoding];
}
static id enc_r_begin(MTL4RenderPassDescriptor *pass) {
    id<MTL4RenderCommandEncoder> e = [commands renderCommandEncoderWithDescriptor:pass];
    [e barrierAfterQueueStages:MTLStageAll beforeStages:MTLStageAll visibilityOptions:MTL4VisibilityOptionDevice];
    return e;
}
static void enc_r_end(id e) { enc_b_end(e); }
static void enc_b_copy_texture(id e, id<MTLTexture> source, NSUInteger sl, NSUInteger lv, MTLOrigin origin,
        MTLSize size, id<MTLTexture> destination, NSUInteger dl, NSUInteger dv, MTLOrigin dest) {
    [e copyFromTexture:source sourceSlice:sl sourceLevel:lv sourceOrigin:origin sourceSize:size
        toTexture:destination destinationSlice:dl destinationLevel:dv destinationOrigin:dest];
}
#include "../host/nvn_clear_region.inc"
// Render-target state the attachment logic reads (field names as in the backend).
static struct { int has_rt; struct { int n; tex_t *color[8]; view_t cview[8]; int has_cview[8]; tex_t *depth; } rt; int clear_c[8], clear_d, clear_s; } R;
static void *g_enc;
static unsigned pending_clear_targets;
static unsigned zelda_color_clear_targets(void) { return pending_clear_targets; }
static int zelda_texture_same_storage(const tex_t *a, const tex_t *b) {
    return a && b && (a == b || (a->serial && a->serial == b->serial));
}
#include "../host/nvn_color_targets.inc"
#include "../host/nvn_attachmentless.inc"
static unsigned flushes, flushed_targets;
static void rp_flush(void) {
    flushed_targets = z_color_targets; flushes++; end_blit();
    memset(R.clear_c, 0, sizeof R.clear_c); pending_clear_targets = 0;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    @autoreleasepool {
        g_dev = MTLCreateSystemDefaultDevice(); assert(g_dev.supportsBCTextureCompression);
        NSError *error = nil;
        residency = [g_dev newResidencySetWithDescriptor:[MTLResidencySetDescriptor new] error:&error]; assert(residency);
        id<MTL4CommandQueue> queue = [g_dev newMTL4CommandQueue]; assert(queue); [queue addResidencySet:residency];
        id<MTL4CommandAllocator> allocator = [g_dev newCommandAllocator]; assert(allocator);
        retired = [NSMutableArray array];
        static tex_t first, second; first.serial = 1; second.serial = 2;
        R.has_rt = 1; R.rt.n = 2; R.rt.color[0] = &first; R.rt.color[1] = &second;
        zelda_color_targets(0x0000000f, 1, 0);
        assert(flushes == 1 && flushed_targets == 0xff && z_color_targets == 1);
        zelda_color_targets(0x0000000f, 3, 0); assert(flushes == 1);
        zelda_begin_color_targets(0); g_enc = &R;
        assert(zelda_use_color_target(0, 0, 0) && !zelda_use_color_target(1, 0, 0));
        assert(zelda_use_color_target(1, 0, 1));  // a pending clear attaches its slot
        zelda_begin_color_targets(1); assert(!zelda_use_color_target(0, 1, 0));
        // Draws alternating between c0 and c1 in one configuration: the second pass attaches both and keeps going.
        zelda_begin_color_targets(0); zelda_color_targets(0x000000f0, 2, 0); assert(flushes == 2 && z_color_targets == 2);
        zelda_begin_color_targets(0); assert(zelda_use_color_target(0, 0, 0) && zelda_use_color_target(1, 0, 0));
        zelda_color_targets(0x0000000f, 1, 0); assert(flushes == 2 && z_color_targets == 1);
        assert(zelda_color_write_mask(0xff) == 0x0f);  // the attached c1 is not written by this draw
        // Written tracking: a pass's own writes and clears make sampling an attachment end the pass.
        R.clear_c[1] = 1; R.clear_d = 0; zelda_note_draw_writes(0); zelda_begin_color_targets(0);
        assert(z_pass_written == 3 && !z_pass_depth_written); zelda_note_draw_writes(1); assert(z_pass_depth_written);
        R.clear_c[1] = 0;
        // Store tracking: the draw that opens a pass carries its stores in; a clear-only pass starts without them.
        assert(!z_pass_stores); zelda_note_draw_stores(1); assert(z_pass_stores);
        zelda_begin_color_targets(1); assert(!z_pass_stores && !z_barriers_kept);
        zelda_begin_color_targets(0); assert(z_pass_stores); zelda_note_draw_stores(0); zelda_begin_color_targets(0); assert(!z_pass_stores);
        // The same subresource bound to both slots is attached once.
        R.rt.color[1] = &first; g_enc = NULL;
        zelda_color_targets(0x000000f0, 2, 0); zelda_color_targets(0x0000000f, 1, 0); zelda_begin_color_targets(0);
        assert(zelda_use_color_target(0, 0, 0) && !zelda_use_color_target(1, 0, 0));
        R.rt.color[1] = &second;
        zelda_color_targets(0xf0000000, 0x80, 0);
        assert(z_color_targets == 0x80);
        zelda_color_targets(0, 0xff, 0); assert(z_color_targets == 0);
        unsigned prior = flushes;
        zelda_color_targets(0x0f, 1, 1);
        assert(z_color_targets == 1 && flushes == prior);
        // Different slots (including copied NVN objects) of one storage cannot be cleared/attached together.
        tex_t copied = first; R.rt.color[1] = &copied;
        pending_clear_targets = 1; assert(zelda_pending_clear_conflicts(2));
        assert(!zelda_pending_clear_conflicts(1));
        R.rt.has_cview[1] = 1; R.rt.cview[1].set_layers = 1; R.rt.cview[1].min_layer = 1;
        assert(!zelda_pending_clear_conflicts(2)); // Distinct array slices can share a pass.
        R.rt.cview[1].min_layer = 0;
        prior = flushes; zelda_color_targets(0xf0, 2, 1);
        assert(flushes == prior + 1 && !pending_clear_targets);
        R.clear_c[0] = 1; assert(zelda_pending_clear_conflicts(2));
        rp_flush(); assert(!zelda_pending_clear_conflicts(2));
        R.rt.color[1] = &second; R.rt.has_cview[1] = 0;
        MTLTextureDescriptor *descriptor = [MTLTextureDescriptor new];
        descriptor.textureType = MTLTextureType3D; descriptor.pixelFormat = MTLPixelFormatBC4_RUnorm;
        descriptor.width = descriptor.height = 16; descriptor.depth = 8; descriptor.mipmapLevelCount = 5;
        descriptor.storageMode = MTLStorageModePrivate; descriptor.usage = MTLTextureUsageShaderRead;
        id<MTLTexture> base = [g_dev newTextureWithDescriptor:descriptor]; assert(base); res_add(base);
        tex_t texture = {.d = {.format = NVN_FORMAT_RGTC1_UNORM, .target = 2}};
        for (unsigned iteration = 0; iteration < 2; iteration++) {
            unsigned level = iteration ? 4 : 1, plane = iteration ? 0 : 2;
            unsigned width = 16 >> level, depth = MAX(8 >> level, 1), blocks = (width + 3) / 4;
            size_t row = 256, image = row * blocks, bytes = image * depth;
            id<MTLBuffer> source = [g_dev newBufferWithLength:bytes options:MTLResourceStorageModeShared];
            id<MTLBuffer> before = [g_dev newBufferWithLength:image options:MTLResourceStorageModeShared];
            id<MTLBuffer> after = [g_dev newBufferWithLength:bytes options:MTLResourceStorageModeShared];
            assert(source && before && after); res_add(source); res_add(before); res_add(after);
            memset(source.contents, 0x3c, bytes); memset(after.contents, 0xa5, bytes);
            commands = [g_dev newCommandBuffer]; [commands beginCommandBufferWithAllocator:allocator];
            enc_b_copy_buffer_texture(blit(), source, 0, row, image, MTLSizeMake(width, width, depth), base, 0, level, MTLOriginMake(0, 0, 0));
            end_blit();
            MTL4RenderPassDescriptor *pass = [MTL4RenderPassDescriptor new];
            MTLRenderPassColorAttachmentDescriptor *attachment = pass.colorAttachments[0];
            attachment.texture = base; attachment.level = level; attachment.depthPlane = plane;
            view_t view = {.set_format = 1, .format = NVN_FORMAT_RGBA16UI, .set_layers = 1, .min_layer = plane, .nlayers = 1};
            zelda_attach_view(attachment, &texture, &view, 1);
            assert(attachment.texture.width == blocks && !attachment.level && !attachment.depthPlane);
            zelda_block_barrier(g_blit);
            enc_b_copy_texture_buffer(blit(), attachment.texture, 0, 0, MTLOriginMake(0, 0, 0), MTLSizeMake(blocks, blocks, 1), before, 0, row, image);
            end_blit();
            attachment.loadAction = MTLLoadActionClear; attachment.storeAction = MTLStoreActionStore;
            attachment.clearColor = MTLClearColorMake(0x1122, 0x3344, 0x5566, 0x7788);
            id<MTL4RenderCommandEncoder> render = [commands renderCommandEncoderWithDescriptor:pass];
            [render barrierAfterQueueStages:MTLStageAll beforeStages:MTLStageAll visibilityOptions:MTL4VisibilityOptionDevice];
            [render barrierAfterStages:MTLStageAll beforeQueueStages:MTLStageAll visibilityOptions:MTL4VisibilityOptionDevice];
            [render endEncoding];
            zelda_flush_block_targets(); assert(!z_block_count);
            end_blit();
            enc_b_copy_texture_buffer(blit(), base, 0, level, MTLOriginMake(0, 0, 0), MTLSizeMake(width, width, depth), after, 0, row, image);
            end_blit(); [commands endCommandBuffer]; [residency commit];
            dispatch_semaphore_t done = dispatch_semaphore_create(0);
            MTL4CommitOptions *options = [MTL4CommitOptions new];
            [options addFeedbackHandler:^(id<MTL4CommitFeedback> feedback) { assert(!feedback.error); dispatch_semaphore_signal(done); }];
            id<MTL4CommandBuffer> batch[] = {commands}; [queue commit:batch count:1 options:options];
            assert(!dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)));
            for (unsigned z = 0; z < depth; z++) for (unsigned y = 0; y < blocks; y++) for (unsigned x = 0; x < blocks; x++) {
                uint64_t expected = z == plane ? 0x7788556633441122ull : 0x3c3c3c3c3c3c3c3cull;
                assert(*(uint64_t *)((uint8_t *)after.contents + z * image + y * row + 8 * x) == expected);
                assert(*(uint64_t *)((uint8_t *)before.contents + y * row + 8 * x) == 0x3c3c3c3c3c3c3c3cull);
            }
            [allocator reset];
        }
        descriptor.pixelFormat = MTLPixelFormatRGBA8Unorm; descriptor.width = descriptor.height = 8;
        descriptor.depth = 4; descriptor.mipmapLevelCount = 3; descriptor.usage |= MTLTextureUsageRenderTarget;
        id<MTLTexture> target = [g_dev newTextureWithDescriptor:descriptor]; assert(target); res_add(target);
        size_t row = 256, image = row * 4, bytes = image * 2;
        id<MTLBuffer> source = [g_dev newBufferWithLength:bytes options:MTLResourceStorageModeShared];
        id<MTLBuffer> after = [g_dev newBufferWithLength:bytes options:MTLResourceStorageModeShared];
        res_add(source); res_add(after); memset(source.contents, 0x3c, bytes);
        commands = [g_dev newCommandBuffer]; [commands beginCommandBufferWithAllocator:allocator];
        enc_b_copy_buffer_texture(blit(), source, 0, row, image, MTLSizeMake(4, 4, 2), target, 0, 1, MTLOriginMake(0, 0, 0));
        end_blit();
        R.rt.color[1] = &first; zelda_color_targets(0x0000000f, 1, 0); zelda_begin_color_targets(0);
        MTL4RenderPassDescriptor *duplicate = [MTL4RenderPassDescriptor new];
        for (unsigned i = 0; i < 2; i++) if (zelda_use_color_target(i, 0, 0)) {
            duplicate.colorAttachments[i].texture = target; duplicate.colorAttachments[i].level = 1;
            duplicate.colorAttachments[i].loadAction = MTLLoadActionLoad;
            duplicate.colorAttachments[i].storeAction = MTLStoreActionStore;
        }
        assert(duplicate.colorAttachments[0].texture && !duplicate.colorAttachments[1].texture);
        enc_r_end(enc_r_begin(duplicate));
        MTLRenderPassColorAttachmentDescriptor *attachment = [MTLRenderPassColorAttachmentDescriptor new];
        attachment.texture = target; attachment.level = 1; attachment.depthPlane = 1;
        zelda_clear_region(attachment, (p_scissor){-1, 2, 3, 10}, MTLClearColorMake(1, 0, 1, 1));
        zelda_clear_region(attachment, (p_scissor){10, 0, 1, 1}, MTLClearColorMake(0, 0, 0, 0));
        end_blit();
        enc_b_copy_texture_buffer(blit(), target, 0, 1, MTLOriginMake(0, 0, 0), MTLSizeMake(4, 4, 2), after, 0, row, image);
        end_blit(); [commands endCommandBuffer]; [residency commit];
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        MTL4CommitOptions *options = [MTL4CommitOptions new];
        [options addFeedbackHandler:^(id<MTL4CommitFeedback> feedback) { assert(!feedback.error); dispatch_semaphore_signal(done); }];
        id<MTL4CommandBuffer> batch[] = {commands}; [queue commit:batch count:1 options:options];
        assert(!dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)));
        for (unsigned z = 0; z < 2; z++) for (unsigned y = 0; y < 4; y++) for (unsigned x = 0; x < 4; x++) {
            uint32_t expected = z == 1 && y >= 2 && x < 2 ? 0xffff00ff : 0x3c3c3c3c;
            assert(*(uint32_t *)((uint8_t *)after.contents + z * image + y * row + 4 * x) == expected);
        }
        [allocator reset];
        MTL4RenderPassDescriptor *empty = [MTL4RenderPassDescriptor new];
        zelda_attachmentless_pass(empty, (float[]){2, 1, 4, 3}, 1);
        assert(empty.renderTargetWidth == 6 && empty.renderTargetHeight == 4);
        zelda_attachmentless_pass(empty, (float[]){0, 0, 4, 3}, 1);
        NSString *shader = @"#include <metal_stdlib>\nusing namespace metal;\n"
            "vertex float4 vs(uint i [[vertex_id]]) { return float4(i == 1 ? 3 : -1, i == 2 ? 3 : -1, 0.5, 1); }\n"
            "fragment void fs(device atomic_uint *n [[buffer(0)]]) { atomic_fetch_add_explicit(n, 1u, memory_order_relaxed); }\n";
        id<MTLLibrary> library = [g_dev newLibraryWithSource:shader options:nil error:&error]; assert(library);
        MTLRenderPipelineDescriptor *pipeline = [MTLRenderPipelineDescriptor new];
        pipeline.vertexFunction = [library newFunctionWithName:@"vs"];
        pipeline.fragmentFunction = [library newFunctionWithName:@"fs"];
        id<MTLRenderPipelineState> state = [g_dev newRenderPipelineStateWithDescriptor:pipeline error:&error]; assert(state);
        id<MTLBuffer> counter = [g_dev newBufferWithLength:16 options:MTLResourceStorageModeShared]; res_add(counter);
        memset(counter.contents, 0, 16);
        MTL4ArgumentTableDescriptor *table = [MTL4ArgumentTableDescriptor new]; table.maxBufferBindCount = 1;
        id<MTL4ArgumentTable> arguments = [g_dev newArgumentTableWithDescriptor:table error:&error]; assert(arguments);
        [arguments setAddress:counter.gpuAddress atIndex:0];
        commands = [g_dev newCommandBuffer]; [commands beginCommandBufferWithAllocator:allocator];
        id<MTL4RenderCommandEncoder> render = enc_r_begin(empty);
        [render setRenderPipelineState:state]; [render setArgumentTable:arguments atStages:MTLRenderStageFragment];
        [render setViewport:(MTLViewport){0, 0, 4, 3, 0, 1}];
        [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; enc_r_end(render);
        [commands endCommandBuffer]; [residency commit]; batch[0] = commands;
        options = [MTL4CommitOptions new];
        [options addFeedbackHandler:^(id<MTL4CommitFeedback> feedback) { assert(!feedback.error); dispatch_semaphore_signal(done); }];
        [queue commit:batch count:1 options:options];
        assert(!dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)));
        assert(*(uint32_t *)counter.contents == 12);
        [allocator reset];
        shader = [NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&error]; assert(shader);
        library = [g_dev newLibraryWithSource:shader options:nil error:&error]; assert(library);
        pipeline.vertexFunction = [library newFunctionWithName:@"vs"];
        pipeline.fragmentFunction = [library newFunctionWithName:@"fs"];
        state = [g_dev newRenderPipelineStateWithDescriptor:pipeline error:&error]; assert(state);
        table.maxBufferBindCount = 16;
        arguments = [g_dev newArgumentTableWithDescriptor:table error:&error]; assert(arguments);
        [arguments setAddress:counter.gpuAddress atIndex:0]; [arguments setAddress:counter.gpuAddress + 4 atIndex:15];
        zelda_attachmentless_pass(empty, (float[]){0, 0, 8, 8}, 1);
        for (unsigned size = 1; size <= 5; size += 4) {
            *(uint32_t *)counter.contents = 0; ((float *)counter.contents)[1] = size;
            commands = [g_dev newCommandBuffer]; [commands beginCommandBufferWithAllocator:allocator];
            render = enc_r_begin(empty); [render setRenderPipelineState:state];
            [render setArgumentTable:arguments atStages:MTLRenderStageVertex | MTLRenderStageFragment];
            [render setViewport:(MTLViewport){0, 0, 8, 8, 0, 1}];
            [render drawPrimitives:MTLPrimitiveTypePoint vertexStart:0 vertexCount:1]; enc_r_end(render);
            [commands endCommandBuffer]; batch[0] = commands;
            options = [MTL4CommitOptions new];
            [options addFeedbackHandler:^(id<MTL4CommitFeedback> feedback) { assert(!feedback.error); dispatch_semaphore_signal(done); }];
            [queue commit:batch count:1 options:options];
            assert(!dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)));
            assert(*(uint32_t *)counter.contents == size * size);
            [allocator reset];
        }
        descriptor = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR8Unorm width:128 height:64 mipmapped:NO];
        descriptor.storageMode = MTLStorageModeShared; descriptor.usage = MTLTextureUsageShaderRead;
        descriptor.swizzle = MTLTextureSwizzleChannelsMake(MTLTextureSwizzleOne, MTLTextureSwizzleOne, MTLTextureSwizzleOne, MTLTextureSwizzleRed);
        sampled_texture = [g_dev newTextureWithDescriptor:descriptor]; assert(sampled_texture); res_add(sampled_texture);
        uint8_t pixels[128 * 64] = {0}; pixels[14 * 128 + 35] = 160;
        [sampled_texture replaceRegion:MTLRegionMake2D(0, 0, 128, 64) mipmapLevel:0 withBytes:pixels bytesPerRow:128];
        void *view;
        uint64_t rid = zelda_sampled_texture(290, MTLTextureType2D, &view);
        assert(rid == sampled_texture.gpuResourceID._impl && view == (__bridge void *)sampled_texture);
        rid = zelda_sampled_texture(290, MTLTextureType2DArray, &view);
        id<MTLTexture> array = (__bridge id<MTLTexture>)view;
        assert(array.textureType == MTLTextureType2DArray && array.arrayLength == 1 && array.parentTexture == sampled_texture);
        assert(array.swizzle.alpha == MTLTextureSwizzleRed);
        for (unsigned i = 0; i < 100; i++) {
            assert(zelda_sampled_texture(290, MTLTextureType2DArray, &view) == rid);
            assert(view == (__bridge void *)array);
        }
        MTLSamplerDescriptor *sampler_desc = [MTLSamplerDescriptor new]; sampler_desc.supportArgumentBuffers = YES;
        id<MTLSamplerState> sampler = [g_dev newSamplerStateWithDescriptor:sampler_desc]; assert(sampler);
        shader = @"#include <metal_stdlib>\nusing namespace metal;\n"
            "struct Args {texture2d_array<float> t [[id(0)]]; sampler s [[id(1)]]; device float4 *out [[id(2)]];};\n"
            "kernel void sample_array(constant Args& a [[buffer(0)]]) { a.out[0]=a.t.sample(a.s,float2(35.5/128,14.5/64),0,level(0)); a.out[1]=a.t.sample(a.s,float2(1.5/128,1.5/64),0,level(0)); }\n";
        library = [g_dev newLibraryWithSource:shader options:nil error:&error]; assert(library);
        id<MTLComputePipelineState> sampling = [g_dev newComputePipelineStateWithFunction:[library newFunctionWithName:@"sample_array"] error:&error]; assert(sampling);
        id<MTLBuffer> samples = [g_dev newBufferWithLength:32 options:MTLResourceStorageModeShared]; res_add(samples);
        id<MTLBuffer> bindings = [g_dev newBufferWithLength:256 options:MTLResourceStorageModeShared]; res_add(bindings);
        uint64_t *slots = bindings.contents; slots[0] = rid; slots[1] = sampler.gpuResourceID._impl; slots[2] = samples.gpuAddress;
        [arguments setAddress:bindings.gpuAddress atIndex:0];
        commands = [g_dev newCommandBuffer]; [commands beginCommandBufferWithAllocator:allocator];
        id<MTL4ComputeCommandEncoder> compute = [commands computeCommandEncoder];
        [compute setComputePipelineState:sampling]; [compute setArgumentTable:arguments];
        [compute dispatchThreads:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
        enc_b_end(compute); [commands endCommandBuffer]; [residency commit]; batch[0] = commands;
        options = [MTL4CommitOptions new];
        [options addFeedbackHandler:^(id<MTL4CommitFeedback> feedback) { assert(!feedback.error); dispatch_semaphore_signal(done); }];
        [queue commit:batch count:1 options:options];
        assert(!dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)));
        float *sample_values = samples.contents;
        assert(sample_values[0] == 1 && sample_values[3] > 0.627f && sample_values[3] < 0.628f);
        assert(sample_values[4] == 1 && sample_values[7] == 0);
        CFRelease(g_treg[290].array_view);
        puts("Metal 4 views, sampled arrays, clipped clears and attachmentless buffer writes passed");
    }
}
