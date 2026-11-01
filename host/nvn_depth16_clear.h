// Depth16Unorm load-clears that land where a fragment writing the same depth lands. Apple GPUs truncate a load-clear but
// round a fragment's depth: 0.5 clears to 32767 while a fragment writes 32768. v1.4.3's Ultrahand and Recall highlights
// keep G-buffer class values in a Depth16 target (cleared, then per-tile quads write gl_FragDepth) and shade them with
// depth EQUAL, so the cleared tiles failed the test and the highlight showed only inside 64x64 tiles. Take the nearest
// D16 value, then a point inside both its truncation and its rounding bin; the quarter-unit margin survives Metal's
// conversion of the descriptor's double to float. In-pass depth clears are drawn as fragments and need no change.
// From nxnative's nvn_depth16_clear_value (seedds/nxnative a5513d4, ported there from MacNativeGames 3d90e41).
#pragma once
#include <math.h>

static inline double zelda_depth16_clear_value(double depth) {
    if (!isfinite(depth) || depth <= 0.0 || depth >= 1.0) return depth;
    double q = round(depth * 65535.0);
    return q >= 65535.0 ? 1.0 : (q + 0.25) / 65535.0;
}
static inline double zelda_depth_clear_value(MTLPixelFormat format, double depth) {
    return format == MTLPixelFormatDepth16Unorm ? zelda_depth16_clear_value(depth) : depth;
}
