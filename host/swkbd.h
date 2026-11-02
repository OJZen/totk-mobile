#pragma once
#include <stddef.h>
#include <stdint.h>

// nn::swkbd::String: the caller's buffer for the result and its size in bytes (GetRequiredStringBufferSize, 0x7d4).
typedef struct { void *text; uint64_t bytes; } swkbd_string;

// Asks the front end for text as the keyboard applet would and writes it into out; 0 = confirmed, 0x29f = canceled.
uint32_t swkbd_show(swkbd_string *out, const uint8_t *arg);
size_t swkbd_utf16_to_utf8(const uint16_t *in, size_t units, char *out, size_t cap);
size_t swkbd_utf8_to_utf16(const char *in, uint16_t *out, size_t max_units, int keep_new_lines);
