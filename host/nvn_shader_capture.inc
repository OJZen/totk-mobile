#include "shader_key.h"
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <sys/stat.h>

void mtl_prog_prefetch(const NVNshaderData *shaders, int count);

static void zelda_capture_shader(const NVNshaderData *shader) {
    uint8_t hash[32]; const void *code = (const void *)(uintptr_t)shader->data;
    // Capture recognizable records here; draw-time validation remains authoritative.
    if (!zelda_shader_hash(shader->control, code, hash)) return;
    char key[17], path[96];
    for (unsigned i = 0; i < 8; i++) snprintf(key + 2 * i, 3, "%02x", hash[i]);
    // Indexed shaders keep their code; iOS has no SPIR-V of the prepared set.
    snprintf(path, sizeof path, "work/shaders/code/%s.bin", key);
    if (!access(path, F_OK)) return;
    snprintf(path, sizeof path, "work/shaders/source/runtime-%s.nvn", key);
    if (!access(path, F_OK)) return;
    char temporary[] = "work/tmp/shader-XXXXXX";
    int fd = mkstemp(temporary);
    if (fd < 0) die("Cannot create shader capture");
    FILE *file = fdopen(fd, "wb");
    if (!file) { close(fd); unlink(temporary); die("Cannot open shader capture"); }
    size_t control_bytes = shader_word(shader->control, 0x20), code_bytes = shader_word(shader->control, 0x704);
    int complete = fwrite(shader->control, 1, control_bytes, file) == control_bytes &&
                   fwrite(code, 1, code_bytes, file) == code_bytes;
    int closed = fclose(file);
    if (!complete || closed || rename(temporary, path)) { unlink(temporary); die("Cannot save shader capture"); }
}

static void zelda_capture_program(const NVNshaderData *shaders, int count) {
    char stages[2][17] = {{0}};
    for (int i = 0; i < count; i++) {
        zelda_capture_shader(&shaders[i]);
        uint8_t hash[32];
        if (count != 2 || !zelda_shader_hash(shaders[i].control, (void *)(uintptr_t)shaders[i].data, hash)) continue;
        unsigned stage = shader_word(shaders[i].control, 0x714);
        if (stage > 1) continue;
        for (unsigned j = 0; j < 8; j++) snprintf(stages[stage] + 2 * j, 3, "%02x", hash[j]);
    }
    if (!stages[0][0] || !stages[1][0]) return;
    // Prepare the program's float-input pairing before its first draw; integer variants still use actual attributes.
    char identity[64], key[17], path[96]; uint8_t hash[32];
    snprintf(identity, sizeof identity, "%s-00000000-%s", stages[0], stages[1]);
    CC_SHA256(identity, (CC_LONG)strlen(identity), hash);
    for (unsigned i = 0; i < 8; i++) snprintf(key + 2 * i, 3, "%02x", hash[i]);
    snprintf(path, sizeof path, "work/shaders/variants/%s.json", key);
    if (!access(path, F_OK)) return;
    if (mkdir("work/shaders/variants", 0700) && errno != EEXIST) die("Cannot create shader pair directory");
    char temporary[] = "work/tmp/shader-pair-XXXXXX";
    int fd = mkstemp(temporary);
    if (fd < 0) die("Cannot create shader pair capture");
    FILE *file = fdopen(fd, "wb");
    if (!file) { close(fd); unlink(temporary); die("Cannot open shader pair capture"); }
    int complete = fprintf(file, "{\"base\":\"%s\",\"mask\":0,\"next\":\"%s\"}\n", stages[0], stages[1]) > 0;
    int closed = fclose(file);
    if (!complete || closed || rename(temporary, path)) { unlink(temporary); die("Cannot save shader pair capture"); }
}
