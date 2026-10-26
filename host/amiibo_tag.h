#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Raw NTAG215 page layout; public UID/model fields only. No key material or
// encrypted personal/application data is imported. This is not HMAC validation.
typedef struct { uint8_t uid[7], model[7]; } amiibo_tag;
static inline bool amiibo_tag_parse(const uint8_t *data, size_t size, amiibo_tag *out) {
    if (!data || !out || (size != 532 && size != 540 && size != 572)) return false;
    if (data[0] != 4 || data[3] != (uint8_t)(0x88 ^ data[0] ^ data[1] ^ data[2]) ||
        data[8] != (uint8_t)(data[4] ^ data[5] ^ data[6] ^ data[7])) return false;
    // NTAG215 capability container and amiibo model marker.
    if (memcmp(data + 12, "\xf1\x10\xff\xee", 4) || data[0x5b] != 2) return false;
    amiibo_tag tag = {0};
    memcpy(tag.uid, data, 3); memcpy(tag.uid + 3, data + 4, 4);
    // ModelInfo integers use host (little) endian, unlike the tag's model ID.
    tag.model[0] = data[0x55]; tag.model[1] = data[0x54]; tag.model[2] = data[0x56];
    tag.model[3] = data[0x5a]; tag.model[4] = data[0x59]; tag.model[5] = data[0x58];
    tag.model[6] = data[0x57];
    *out = tag; return true;
}
