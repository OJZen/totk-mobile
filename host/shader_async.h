#pragma once
#include <stdbool.h>

// New render pipelines and shader variants are prepared on background threads. A draw whose pipeline is not ready
// waits a few milliseconds per frame at most and is then skipped until the pipeline is built, so the first sight of
// an effect may show it a few frames late instead of stalling the frame. The "shader_compile": "wait" setting keeps
// the earlier behavior (every draw waits for its pipeline); ZELDA_SHADER_ASYNC=0 or 1 overrides the setting.
void zelda_set_shader_wait(bool wait);
