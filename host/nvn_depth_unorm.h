#include <math.h>

// D16 load-action clears and fragment depth writes can round differently at
// half steps (0.5 clears to 32767 but draws as 32768 on the affected device).
// Quantize explicitly, then choose an interior value of that integer's bin.
// The quarter-step margin survives float conversion and yields the same code
// whether the Metal clear conversion truncates or rounds to nearest.
static inline double zelda_depth_clear_value(MTLPixelFormat format, double value) {
    if (format != MTLPixelFormatDepth16Unorm || !isfinite(value)) return value;
    double clamped = fmin(1.0, fmax(0.0, value));
    double code = floor(clamped * 65535.0 + 0.5);
    return fmin(1.0, (code + 0.25) / 65535.0);
}
