#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "unlock_logic.h"

struct scripted {
    struct avb_reply replies[3];
    unsigned calls;
    unsigned writes;
};
static struct avb_reply fake_call(void *context, uint32_t command,
                                  uint64_t attr, uint64_t value) {
    struct scripted *s = context;
    assert(s->calls < 3);
    if (s->calls == 1) {
        assert(command == 3 && attr == 1 && value == 0);
        s->writes++;
    } else {
        assert(command == 2 && attr == 2 && value == UINT32_MAX);
    }
    return s->replies[s->calls++];
}
static void check(struct scripted s, int result, unsigned calls, unsigned writes) {
    assert(avb_unlock_once(fake_call, &s) == result);
    assert(s.calls == calls && s.writes == writes);
}
int main(void) {
    const struct avb_reply locked = {0,0,1}, unlocked = {0,0,0};
    const struct avb_reply denied = {0,0xffff0001u,UINT32_MAX};
    const struct avb_reply transport = {-1,0,0}, invalid = {0,0,UINT32_MAX};
    check((struct scripted){{locked,unlocked,unlocked},0,0}, AVB_UNLOCKED,3,1);
    check((struct scripted){{unlocked,unlocked,unlocked},0,0}, AVB_ALREADY_UNLOCKED,1,0);
    check((struct scripted){{denied,unlocked,unlocked},0,0}, AVB_INITIAL_READ_FAILED,1,0);
    check((struct scripted){{transport,unlocked,unlocked},0,0}, AVB_INITIAL_READ_FAILED,1,0);
    check((struct scripted){{invalid,unlocked,unlocked},0,0}, AVB_INITIAL_STATE_INVALID,1,0);
    check((struct scripted){{locked,denied,unlocked},0,0}, AVB_WRITE_FAILED_OR_UNCERTAIN,2,1);
    check((struct scripted){{locked,transport,unlocked},0,0}, AVB_WRITE_FAILED_OR_UNCERTAIN,2,1);
    check((struct scripted){{locked,unlocked,denied},0,0}, AVB_FINAL_READ_FAILED,3,1);
    check((struct scripted){{locked,unlocked,transport},0,0}, AVB_FINAL_READ_FAILED,3,1);
    check((struct scripted){{locked,unlocked,locked},0,0}, AVB_FINAL_STATE_NOT_UNLOCKED,3,1);
    check((struct scripted){{locked,unlocked,invalid},0,0}, AVB_FINAL_STATE_NOT_UNLOCKED,3,1);
    puts("11 protocol scenarios passed; no failure retries a write.");
    return 0;
}
