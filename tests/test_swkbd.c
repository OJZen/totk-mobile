#include "swkbd.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

// Front end stand-in: records what it was asked and answers with the queued replies in turn.
static const char *replies[4]; static int reply_count, asked;
static char seen_title[256], seen_sub[256], seen_guide[256], seen_initial[256]; static int seen_max;
int front_text_input(const char *title, const char *sub, const char *guide, const char *initial, int max_chars, char *out, int out_cap) {
    snprintf(seen_title, sizeof seen_title, "%s", title); snprintf(seen_sub, sizeof seen_sub, "%s", sub);
    snprintf(seen_guide, sizeof seen_guide, "%s", guide); snprintf(seen_initial, sizeof seen_initial, "%s", initial);
    seen_max = max_chars;
    if (asked >= reply_count) return 0;
    const char *r = replies[asked++];
    if (!r) return 0;
    snprintf(out, (size_t)out_cap, "%s", r);
    return 1;
}

static void put_utf16(uint8_t *at, const char *ascii) { for (size_t i = 0; ascii[i]; i++) { at[i * 2] = (uint8_t)ascii[i]; at[i * 2 + 1] = 0; } }
static void put32(uint8_t *arg, size_t offset, int32_t v) { memcpy(arg + offset, &v, sizeof v); }

static uint8_t arg[0x548], work[0x100];
static uint16_t result16[0x7d4 / 2];

static void reset(int max, int min) {
    memset(arg, 0, sizeof arg); memset(work, 0, sizeof work); memset(result16, 0xaa, sizeof result16);
    put_utf16(arg + 0x24, "Name your horse"); put_utf16(arg + 0xa6, "Sub"); put_utf16(arg + 0x1a8, "Guide");
    put32(arg, 0x3ac, max); put32(arg, 0x3b0, min);
    uint8_t *w = work; memcpy(arg + 0x518, &w, sizeof w);
    asked = 0;
}

int main(void) {
    swkbd_string s = {result16, sizeof result16};

    // Confirmed: the header, sub and guide texts and the initial text reach the front end; the text is cut to the
    // maximum in UTF-16 units and terminated.
    reset(4, 1);
    put_utf16(work + 0x14, "Epona"); put32(arg, 0x3c0, 0x14); put32(arg, 0x3c4, 5);
    replies[0] = "Dorephan"; reply_count = 1;
    assert(swkbd_show(&s, arg) == 0);
    assert(!strcmp(seen_title, "Name your horse") && !strcmp(seen_sub, "Sub") && !strcmp(seen_guide, "Guide"));
    assert(!strcmp(seen_initial, "Epona") && seen_max == 4);
    assert(result16[0] == 'D' && result16[3] == 'e' && result16[4] == 0);

    // Non-ASCII text becomes UTF-16, and a surrogate pair is not split by the maximum.
    reset(3, 0);
    replies[0] = "\xe5\xb0\x8f\xe9\xbb\x91\xf0\x9f\x90\xb4"; reply_count = 1;   // two CJK characters and an emoji
    assert(swkbd_show(&s, arg) == 0);
    assert(result16[0] == 0x5c0f && result16[1] == 0x9ed1 && result16[2] == 0);

    // Under the minimum the box asks again with what was typed; canceling returns the SDK's canceled result.
    reset(10, 2);
    replies[0] = "A"; replies[1] = NULL; reply_count = 2;
    assert(swkbd_show(&s, arg) == 0x29f);
    assert(asked == 2 && !strcmp(seen_initial, "A"));

    // UTF-8 mode writes UTF-8 bytes.
    reset(0, 0); arg[0x3bd] = 1;
    replies[0] = "\xe5\xb0\x8f\xe9\xbb\x91"; reply_count = 1;
    assert(swkbd_show(&s, arg) == 0);
    assert(!memcmp(result16, "\xe5\xb0\x8f\xe9\xbb\x91", 7));

    // New lines are dropped unless the keyboard allows them.
    reset(0, 0);
    replies[0] = "a\nb"; reply_count = 1;
    assert(swkbd_show(&s, arg) == 0 && result16[0] == 'a' && result16[1] == 'b' && result16[2] == 0);

    puts("swkbd ok");
    return 0;
}
