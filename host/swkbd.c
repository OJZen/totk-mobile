// The software keyboard. The game asks for text (a horse's name, for example) with nn::swkbd::ShowKeyboard, which
// on the console starts the keyboard library applet and blocks until it closes. There is no applet here, so the call
// is taken over: the front end shows a native text box (front_text_input) and the text goes back the way the applet
// returns it. The layouts below are read from the SDK's ShowKeyboard, which copies the argument field by field into
// the applet's KeyboardConfig, and ReadCloseResultAndString, which reads the result into the String.
#include "swkbd.h"
#include "front.h"
#include "hle.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum {
    ARG_HEADER = 0x24, ARG_SUB = 0xa6, ARG_GUIDE = 0x1a8,          // char16_t[65], [129], [257]
    ARG_MAX_LENGTH = 0x3ac, ARG_MIN_LENGTH = 0x3b0,                // int32 UTF-16 units; 0 = no limit
    ARG_NEW_LINE = 0x3bc, ARG_UTF8 = 0x3bd,                        // bool
    ARG_INITIAL_OFFSET = 0x3c0, ARG_INITIAL_LENGTH = 0x3c4,        // into the work buffer; length in UTF-16 units
    ARG_WORK = 0x518,                                              // void *work buffer
};
#define SWKBD_CANCELED 0x29fu   // what ShowKeyboard returns when the keyboard is closed without confirming
#define INITIAL_MAX 500         // SetInitialText keeps at most this many units

static uint32_t u32_at(const uint8_t *arg, size_t offset) { uint32_t v; memcpy(&v, arg + offset, sizeof v); return v; }

// UTF-16 (at most `units`, stopping at a 0) to NUL-terminated UTF-8; returns the bytes written.
size_t swkbd_utf16_to_utf8(const uint16_t *in, size_t units, char *out, size_t cap) {
    size_t n = 0;
    if (!cap) return 0;
    for (size_t i = 0; i < units && in[i]; i++) {
        uint32_t c = in[i];
        if (c >= 0xd800 && c < 0xdc00 && i + 1 < units && in[i + 1] >= 0xdc00 && in[i + 1] < 0xe000)
            c = 0x10000 + ((c - 0xd800) << 10) + (in[++i] - 0xdc00);
        else if (c >= 0xd800 && c < 0xe000) c = 0xfffd;
        char b[4]; size_t k;
        if (c < 0x80) { b[0] = (char)c; k = 1; }
        else if (c < 0x800) { b[0] = (char)(0xc0 | c >> 6); b[1] = (char)(0x80 | (c & 63)); k = 2; }
        else if (c < 0x10000) { b[0] = (char)(0xe0 | c >> 12); b[1] = (char)(0x80 | (c >> 6 & 63)); b[2] = (char)(0x80 | (c & 63)); k = 3; }
        else { b[0] = (char)(0xf0 | c >> 18); b[1] = (char)(0x80 | (c >> 12 & 63)); b[2] = (char)(0x80 | (c >> 6 & 63)); b[3] = (char)(0x80 | (c & 63)); k = 4; }
        if (n + k >= cap) break;
        memcpy(out + n, b, k); n += k;
    }
    out[n] = 0;
    return n;
}

// UTF-8 to UTF-16 units without a terminator; a surrogate pair is never split by the limit. Returns the units written.
size_t swkbd_utf8_to_utf16(const char *in, uint16_t *out, size_t max_units, int keep_new_lines) {
    const uint8_t *s = (const uint8_t *)in;
    size_t n = 0;
    while (*s) {
        uint32_t c = *s++; int extra = c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : c >= 0xc0 ? 1 : 0;
        if (extra) c &= 0x3f >> extra;
        else if (c >= 0x80) c = 0xfffd;
        for (; extra && (*s & 0xc0) == 0x80; extra--) c = c << 6 | (*s++ & 63);
        if (extra || c > 0x10ffff || (c >= 0xd800 && c < 0xe000)) c = 0xfffd;
        if (c == '\r' || (c == '\n' && !keep_new_lines)) continue;
        if (c >= 0x10000) {
            if (n + 2 > max_units) break;
            out[n++] = (uint16_t)(0xd800 + ((c - 0x10000) >> 10)); out[n++] = (uint16_t)(0xdc00 + ((c - 0x10000) & 0x3ff));
        } else {
            if (n + 1 > max_units) break;
            out[n++] = (uint16_t)c;
        }
    }
    return n;
}

static size_t utf8_bytes(const char *in, size_t units_limit) {   // the UTF-8 prefix holding at most units_limit UTF-16 units
    const uint8_t *s = (const uint8_t *)in; size_t units = 0, n = 0;
    while (s[n]) {
        size_t k = s[n] >= 0xf0 ? 4 : s[n] >= 0xe0 ? 3 : s[n] >= 0xc0 ? 2 : 1, u = k == 4 ? 2 : 1;
        if (units + u > units_limit) break;
        for (size_t i = 1; i < k; i++) if (!s[n + i]) return n;
        units += u; n += k;
    }
    return n;
}

uint32_t swkbd_show(swkbd_string *out, const uint8_t *arg) {
    if (!out || !out->text || out->bytes < 2 || !arg) return SWKBD_CANCELED;
    char header[65 * 3 + 1], sub[129 * 3 + 1], guide[257 * 3 + 1], initial[INITIAL_MAX * 3 + 1] = "";
    swkbd_utf16_to_utf8((const uint16_t *)(arg + ARG_HEADER), 65, header, sizeof header);
    swkbd_utf16_to_utf8((const uint16_t *)(arg + ARG_SUB), 129, sub, sizeof sub);
    swkbd_utf16_to_utf8((const uint16_t *)(arg + ARG_GUIDE), 257, guide, sizeof guide);
    int32_t max = (int32_t)u32_at(arg, ARG_MAX_LENGTH), min = (int32_t)u32_at(arg, ARG_MIN_LENGTH);
    int32_t initial_offset = (int32_t)u32_at(arg, ARG_INITIAL_OFFSET), initial_length = (int32_t)u32_at(arg, ARG_INITIAL_LENGTH);
    const uint8_t *work; memcpy(&work, arg + ARG_WORK, sizeof work);
    if (work && initial_offset >= 0 && initial_length > 0 && initial_length <= INITIAL_MAX)
        swkbd_utf16_to_utf8((const uint16_t *)(work + initial_offset), (size_t)initial_length, initial, sizeof initial);
    int utf8 = arg[ARG_UTF8] != 0, new_lines = arg[ARG_NEW_LINE] != 0;
    // The string buffer holds the text and its terminator, in UTF-16 units or UTF-8 bytes.
    size_t room = utf8 ? out->bytes - 1 : out->bytes / 2 - 1;
    size_t limit = max > 0 && (size_t)max < room ? (size_t)max : room;
    if (min < 0) min = 0;
    fprintf(stderr, "SWKBD show max=%d min=%d utf8=%d header_bytes=%zu initial_bytes=%zu\n", max, min, utf8, strlen(header), strlen(initial));

    // Guest thread stacks are small; the buffers live on the heap.
    enum { TEXT = 4096, UNITS = 2048 };
    char *text = malloc(TEXT), *clean = malloc(UNITS * 3 + 1);
    uint16_t *units = malloc(UNITS * sizeof *units);
    uint32_t result = SWKBD_CANCELED;
    if (!text || !clean || !units) goto done;
    for (;;) {
        int got = front_text_input(header, sub, guide, initial, max > 0 ? max : 0, text, TEXT);
        if (got < 0) {   // no window: ZELDA_SWKBD_TEXT answers for scripted runs
            const char *scripted = getenv("ZELDA_SWKBD_TEXT");
            if (!scripted) break;
            snprintf(text, TEXT, "%s", scripted); got = 1;
        }
        if (got != 1) break;
        size_t n = swkbd_utf8_to_utf16(text, units, limit < UNITS ? limit : UNITS, new_lines);
        if (n < (size_t)min) {   // the console keyboard does not accept text under the minimum either
            fprintf(stderr, "SWKBD text shorter than the minimum %d; asking again\n", min);
            swkbd_utf16_to_utf8(units, n, initial, sizeof initial);
            if (getenv("ZELDA_SWKBD_TEXT")) break;
            continue;
        }
        memset(out->text, 0, out->bytes);
        if (utf8) {
            swkbd_utf16_to_utf8(units, n, clean, UNITS * 3 + 1);
            memcpy(out->text, clean, utf8_bytes(clean, limit) < room ? utf8_bytes(clean, limit) : room);
        } else memcpy(out->text, units, n * 2);
        fprintf(stderr, "SWKBD confirmed units=%zu\n", n);
        result = 0;
        break;
    }
done:
    free(text); free(clean); free(units);
    if (result) fprintf(stderr, "SWKBD canceled\n");
    return result;
}

HLE_FORCE("_ZN2nn5swkbd12ShowKeyboardEPNS0_6StringERKNS0_15ShowKeyboardArgE",
          "No keyboard library applet: a native text box answers the software keyboard", uint32_t, h_swkbd_show,
          swkbd_string *out, const uint8_t *arg) {
    return swkbd_show(out, arg);
}
