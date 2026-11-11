/* Fixed AVB protocol state machine; no transport or startup side effects here. */
#ifndef OE26_UNLOCK_LOGIC_H
#define OE26_UNLOCK_LOGIC_H
#include <stdint.h>

struct avb_reply {
    int transport_rc;
    uint32_t result;
    uint64_t value;
};
typedef struct avb_reply (*avb_call)(void *, uint32_t, uint64_t, uint64_t);
enum avb_outcome {
    AVB_UNLOCKED = 0,
    AVB_ALREADY_UNLOCKED = 10,
    AVB_INITIAL_READ_FAILED = 11,
    AVB_INITIAL_STATE_INVALID = 12,
    AVB_WRITE_FAILED_OR_UNCERTAIN = 13,
    AVB_FINAL_READ_FAILED = 14,
    AVB_FINAL_STATE_NOT_UNLOCKED = 15
};

static int avb_unlock_once(avb_call call, void *context) {
    /* Command 2, VALUE_OUTPUT. Never infer success from an untouched output. */
    struct avb_reply before = call(context, 2, 2, UINT32_MAX);
    if (before.transport_rc != 0 || before.result != 0)
        return AVB_INITIAL_READ_FAILED;
    if (before.value == 0) return AVB_ALREADY_UNLOCKED;
    if (before.value != 1) return AVB_INITIAL_STATE_INVALID;
    /* Command 3, VALUE_INPUT, state 0. Exactly one invocation, never retried. */
    struct avb_reply write = call(context, 3, 1, 0);
    if (write.transport_rc != 0 || write.result != 0)
        return AVB_WRITE_FAILED_OR_UNCERTAIN;
    struct avb_reply after = call(context, 2, 2, UINT32_MAX);
    if (after.transport_rc != 0 || after.result != 0)
        return AVB_FINAL_READ_FAILED;
    if (after.value != 0) return AVB_FINAL_STATE_NOT_UNLOCKED;
    return AVB_UNLOCKED;
}
#endif
