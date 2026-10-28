#import <Metal/Metal.h>
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "../host/nvn_depth_unorm.h"

// The left two pixels are load-action clears; the right two are fragment
// writes of the same guest float. A D16 equality mask must not distinguish them.
static void check(id<MTLDevice> device, id<MTLLibrary> library, float value, int fixed, int metal4) {
    NSError *error = nil;
    MTLFunctionConstantValues *constants = [MTLFunctionConstantValues new];
    [constants setConstantValue:&value type:MTLDataTypeFloat atIndex:0];
    MTLRenderPipelineDescriptor *pd = [MTLRenderPipelineDescriptor new];
    pd.vertexFunction = [library newFunctionWithName:@"vs"];
    pd.fragmentFunction = [library newFunctionWithName:@"fs" constantValues:constants error:&error];
    pd.depthAttachmentPixelFormat = MTLPixelFormatDepth16Unorm;
    id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:pd error:&error];
    assert(pipeline);
    MTLDepthStencilDescriptor *ds = [MTLDepthStencilDescriptor new];
    ds.depthCompareFunction = MTLCompareFunctionAlways; ds.depthWriteEnabled = YES;
    id<MTLDepthStencilState> state = [device newDepthStencilStateWithDescriptor:ds];
    MTLTextureDescriptor *td = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatDepth16Unorm width:4 height:1 mipmapped:NO];
    td.usage = MTLTextureUsageRenderTarget; td.storageMode = MTLStorageModePrivate;
    id<MTLTexture> depth = [device newTextureWithDescriptor:td];
    id<MTLBuffer> pixels = [device newBufferWithLength:256 options:MTLResourceStorageModeShared];
    double clear = fixed ? zelda_depth_clear_value(td.pixelFormat, value) : value;
    if (metal4) {
        id<MTL4CommandQueue> queue = [device newMTL4CommandQueue];
        id<MTL4CommandAllocator> allocator = [device newCommandAllocator];
        MTLResidencySetDescriptor *rd = [MTLResidencySetDescriptor new]; rd.initialCapacity = 2;
        id<MTLResidencySet> residency = [device newResidencySetWithDescriptor:rd error:&error];
        [residency addAllocation:depth]; [residency addAllocation:pixels]; [residency commit];
        [queue addResidencySet:residency];
        id<MTL4CommandBuffer> command = [device newCommandBuffer]; [command beginCommandBufferWithAllocator:allocator];
        MTL4RenderPassDescriptor *pass = [MTL4RenderPassDescriptor new];
        pass.depthAttachment.texture = depth; pass.depthAttachment.loadAction = MTLLoadActionClear;
        pass.depthAttachment.storeAction = MTLStoreActionStore; pass.depthAttachment.clearDepth = clear;
        id<MTL4RenderCommandEncoder> render = [command renderCommandEncoderWithDescriptor:pass];
        [render setRenderPipelineState:pipeline]; [render setDepthStencilState:state];
        [render setViewport:(MTLViewport){0, 0, 4, 1, 0, 1}]; [render setScissorRect:(MTLScissorRect){2, 0, 2, 1}];
        [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
        [render barrierAfterStages:MTLStageAll beforeQueueStages:MTLStageAll visibilityOptions:MTL4VisibilityOptionDevice];
        [render endEncoding];
        id<MTL4ComputeCommandEncoder> copy = [command computeCommandEncoder];
        [copy barrierAfterQueueStages:MTLStageAll beforeStages:MTLStageAll visibilityOptions:MTL4VisibilityOptionDevice];
        [copy copyFromTexture:depth sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0)
            sourceSize:MTLSizeMake(4, 1, 1) toBuffer:pixels destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256];
        [copy endEncoding]; [command endCommandBuffer];
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        MTL4CommitOptions *options = [MTL4CommitOptions new];
        [options addFeedbackHandler:^(id<MTL4CommitFeedback> feedback) { assert(!feedback.error); dispatch_semaphore_signal(done); }];
        id<MTL4CommandBuffer> commands[] = {command}; [queue commit:commands count:1 options:options];
        assert(!dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)));
        [queue removeResidencySet:residency];
    } else {
        id<MTLCommandQueue> queue = [device newCommandQueue]; id<MTLCommandBuffer> command = [queue commandBuffer];
        MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
        pass.depthAttachment.texture = depth; pass.depthAttachment.loadAction = MTLLoadActionClear;
        pass.depthAttachment.storeAction = MTLStoreActionStore; pass.depthAttachment.clearDepth = clear;
        id<MTLRenderCommandEncoder> render = [command renderCommandEncoderWithDescriptor:pass];
        [render setRenderPipelineState:pipeline]; [render setDepthStencilState:state];
        [render setViewport:(MTLViewport){0, 0, 4, 1, 0, 1}]; [render setScissorRect:(MTLScissorRect){2, 0, 2, 1}];
        [render drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3]; [render endEncoding];
        id<MTLBlitCommandEncoder> copy = [command blitCommandEncoder];
        [copy copyFromTexture:depth sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0)
            sourceSize:MTLSizeMake(4, 1, 1) toBuffer:pixels destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:256];
        [copy endEncoding]; [command commit]; [command waitUntilCompleted]; assert(!command.error);
    }
    const uint16_t *data = pixels.contents;
    printf("Metal%d input=%.9g fixed=%d clear=%u draw=%u\n", metal4 ? 4 : 3, value, fixed, data[0], data[2]);
    fflush(stdout);
    assert(data[0] == data[1] && data[2] == data[3]);
    if (fixed) assert(data[0] == data[2]);
}

int main(void) { @autoreleasepool {
    assert(zelda_depth_clear_value(MTLPixelFormatDepth32Float, .5) == .5);
    assert(zelda_depth_clear_value(MTLPixelFormatDepth32Float_Stencil8, .1) == .1);
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device) { fputs("Metal device unavailable; run this test from Terminal.\n", stderr); return 77; }
    NSError *error = nil;
    NSString *source = @"#include <metal_stdlib>\nusing namespace metal;\n"
        "constant float z [[function_constant(0)]];\n"
        "vertex float4 vs(uint i [[vertex_id]]) { float2 p[] = {float2(-1,-1),float2(3,-1),float2(-1,3)}; return float4(p[i],0,1); }\n"
        "struct O { float depth [[depth(any)]]; }; fragment O fs() { return {z}; }\n";
    MTLCompileOptions *options = [MTLCompileOptions new]; options.mathMode = MTLMathModeSafe;
    id<MTLLibrary> library = [device newLibraryWithSource:source options:options error:&error]; assert(library);
    float values[] = {0, .1f, .25f, nextafterf(.5f, 0), .5f, nextafterf(.5f, 1), .75f, .9f, 1};
    for (int api = 0; api < 2; api++) for (unsigned i = 0; i < sizeof(values) / sizeof(*values); i++)
        for (int fixed = 0; fixed < 2; fixed++) check(device, library, values[i], fixed, api);
    puts("D16 clear/fragment readback checks passed for Metal 3 and Metal 4");
} }
