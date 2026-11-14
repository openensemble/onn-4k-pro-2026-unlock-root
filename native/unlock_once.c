/* Persistent AVB unlock candidate. Deliberate execution only; never reboots. */
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/tee.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/system_properties.h>
#include <unistd.h>
#include "unlock_logic.h"

struct transport {
    int fd;
    uint32_t session;
    const char *marker;
    unsigned write_invocations;
};

static int same_property(const char *name, const char *expected) {
    char value[PROP_VALUE_MAX] = {0};
    if (__system_property_get(name, value) <= 0 || strcmp(value, expected)) {
        fprintf(stderr, "Identity mismatch: %s\n", name);
        return 0;
    }
    return 1;
}

/* Durable process-replay guard. This file does not schedule anything at boot. */
static int consume_marker(const char *path) {
    static const char message[] = "one unlock attempt consumed\n";
    char directory[256];
    if (strlen(path) >= sizeof(directory)) return -1;
    strcpy(directory, path);
    char *slash = strrchr(directory, '/');
    if (!slash) return -1;
    *slash = 0;
    int parent = open(directory, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (parent < 0) return -1;
    int marker = openat(parent, slash + 1,
                        O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (marker < 0) { close(parent); return -1; }
    int ok = write(marker, message, sizeof(message) - 1) == (ssize_t)(sizeof(message) - 1);
    if (ok) ok = fsync(marker) == 0;
    if (close(marker) != 0) ok = 0;
    if (ok) ok = fsync(parent) == 0;
    close(parent);
    return ok ? 0 : -1;
}

static struct avb_reply invoke(void *opaque, uint32_t command, uint64_t attr, uint64_t value) {
    struct transport *t = opaque;
    struct avb_reply reply = {-1, UINT32_MAX, UINT64_MAX};
    /* No arbitrary command, parameter type, state value or repeated write. */
    if (!((command == 2 && attr == 2 && value == UINT32_MAX) ||
          (command == 3 && attr == 1 && value == 0))) return reply;
    if (command == 3) {
        if (t->write_invocations || consume_marker(t->marker) != 0) {
            fprintf(stderr, "Attempt marker could not be durably consumed; no write request\n");
            return reply;
        }
    }
    size_t size = sizeof(struct tee_ioctl_invoke_arg) + 4 * sizeof(struct tee_ioctl_param);
    struct tee_ioctl_invoke_arg *request = calloc(1, size);
    if (!request) return reply;
    request->func = command;
    request->session = t->session;
    request->num_params = 4;
    request->ret = UINT32_MAX;
    request->params[0].attr = attr;
    request->params[0].a = value;
    struct tee_ioctl_buf_data buffer = {.buf_ptr=(uintptr_t)request,.buf_len=size};
    if (command == 3) t->write_invocations++;
    printf("{\"stage\":\"request\",\"command\":%u,\"write_invocations\":%u}\n",
           command, t->write_invocations);
    reply.transport_rc = ioctl(t->fd, TEE_IOC_INVOKE, &buffer);
    int saved_errno = reply.transport_rc < 0 ? errno : 0;
    reply.result = request->ret;
    reply.value = request->params[0].a;
    printf("{\"stage\":\"response\",\"command\":%u,\"rc\":%d,\"errno\":%d,"
           "\"tee_result\":\"0x%08x\",\"origin\":%u,\"value\":%" PRIu64 "}\n",
           command, reply.transport_rc, saved_errno, reply.result, request->ret_origin, reply.value);
    free(request);
    return reply;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc != 8 || strcmp(argv[1], "--unlock-once")) {
        fprintf(stderr, "Required: --unlock-once SERIAL FINGERPRINT BOOTLOADER SLOT BOOT_ID MARKER\n");
        return 20;
    }
    int serial_ok = (strncmp(argv[2], "JUSA", 4) == 0 || (strncmp(argv[2], "JCA", 3) == 0 && strlen(argv[2]) > 3));
    int fp_ok = (strncmp(argv[3], "onn/jarvis2/jarvis2:", 20) == 0 ||
                 strncmp(argv[3], "onn/jarvis2_row/jarvis2:", 24) == 0);
    if (getuid() != 2000 || geteuid() != 2000 ||
        !serial_ok || !fp_ok ||
        strncmp(argv[7], "/data/local/tmp/oe26-avb-", sizeof("/data/local/tmp/oe26-avb-") - 1) ||
        strstr(argv[7], "..")) {
        fprintf(stderr, "Invalid identity, caller or marker path\n");
        return 21;
    }
    if (!same_property("ro.product.device", "jarvis2") ||
        !same_property("ro.serialno", argv[2]) ||
        !same_property("ro.build.fingerprint", argv[3]) ||
        !same_property("ro.bootloader", argv[4]) ||
        !same_property("ro.boot.slot_suffix", argv[5]) ||
        !same_property("ro.boot.flash.locked", "1") ||
        !same_property("ro.boot.verifiedbootstate", "green")) return 22;
    char boot_id[80] = {0};
    FILE *file = fopen("/proc/sys/kernel/random/boot_id", "re");
    if (!file || !fgets(boot_id, sizeof(boot_id), file)) {
        if (file) fclose(file);
        return 23;
    }
    fclose(file);
    boot_id[strcspn(boot_id, "\r\n")] = 0;
    if (strcmp(boot_id, argv[6])) return 24;
    printf("{\"stage\":\"identity_verified\",\"uid\":2000,\"automatic_reboot\":false}\n");
    alarm(12);
    int fd = open("/dev/tee0", O_RDWR | O_CLOEXEC);
    if (fd < 0) return 25;
    struct tee_ioctl_version_data version = {0};
    if (ioctl(fd, TEE_IOC_VERSION, &version) < 0 || version.impl_id != TEE_IMPL_ID_OPTEE ||
        !(version.gen_caps & TEE_GEN_CAP_GP)) { close(fd); return 26; }
    size_t size = sizeof(struct tee_ioctl_open_session_arg) + 4 * sizeof(struct tee_ioctl_param);
    struct tee_ioctl_open_session_arg *request = calloc(1, size);
    if (!request) { close(fd); return 27; }
    const uint8_t uuid[16] = {
        0x02,0x3f,0x8f,0x1a,0x29,0x2a,0x43,0x2b,
        0x8f,0xc4,0xde,0x84,0x71,0x35,0x80,0x67
    };
    memcpy(request->uuid, uuid, sizeof(uuid));
    request->clnt_login = TEE_IOCTL_LOGIN_PUBLIC;
    request->num_params = 4;
    request->ret = UINT32_MAX;
    struct tee_ioctl_buf_data buffer = {.buf_ptr=(uintptr_t)request,.buf_len=size};
    int result = ioctl(fd, TEE_IOC_OPEN_SESSION, &buffer);
    printf("{\"stage\":\"open\",\"rc\":%d,\"errno\":%d,\"tee_result\":\"0x%08x\",\"origin\":%u}\n",
           result,result<0?errno:0,request->ret,request->ret_origin);
    if (result < 0 || request->ret != 0) { free(request); close(fd); return 28; }
    struct transport t = {.fd=fd,.session=request->session,.marker=argv[7],.write_invocations=0};
    int outcome = avb_unlock_once(invoke, &t);
    struct tee_ioctl_close_session_arg end = {.session=request->session};
    result = ioctl(fd, TEE_IOC_CLOSE_SESSION, &end);
    printf("{\"stage\":\"close\",\"rc\":%d,\"errno\":%d}\n",result,result<0?errno:0);
    printf("{\"stage\":\"finished\",\"outcome\":%d,\"write_invocations\":%u,\"uid\":%u,\"euid\":%u}\n",
           outcome,t.write_invocations,(unsigned)getuid(),(unsigned)geteuid());
    free(request);
    close(fd);
    alarm(0);
    return outcome;
}
