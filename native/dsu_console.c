/* Owner's DSU-only root console. Never invoked automatically; no exploit or flash code. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/system_properties.h>
#include <sys/types.h>
#include <unistd.h>
#include "onn26_binding.h"

static void fail(const char *why) { fprintf(stderr, "onn26-su: %s\n", why); exit(1); }
static void fail_errno(const char *op) { fprintf(stderr, "onn26-su: %s: %s\n", op, strerror(errno)); exit(1); }

static void property(const char *key, const char *expected) {
    char value[PROP_VALUE_MAX];
    if (__system_property_get(key, value) <= 0 || strcmp(value, expected)) fail(key);
}

static void metadata(const char *path, const char *expected) {
    char value[64] = {0};
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) fail(path);
    ssize_t size = read(fd, value, sizeof(value)-1);
    close(fd);
    if (size < 0 || strcmp(value, expected)) fail("DSU is not durably disabled/single-use");
}

int main(int argc, char **argv) {
    uid_t caller = getuid();
    if (caller != 2000 && caller != 0) fail("only the owner's ADB shell or root may call this");
    if (geteuid() != 0) fail("setuid elevation unavailable");
    property("ro.serialno", ONN26_SERIAL);
    property("ro.build.fingerprint", ONN26_FINGERPRINT);
    property("ro.bootloader", ONN26_BOOTLOADER);
    property("ro.boot.slot_suffix", ONN26_SLOT);
    property("ro.boot.flash.locked", "0");
    property("ro.gsid.image_running", "1");
    metadata("/metadata/gsi/dsu/install_status", "disabled");
    metadata("/metadata/gsi/dsu/one_shot_boot", "1");
    if (setgroups(0, NULL)) fail_errno("setgroups");
    if (setresgid(0, 0, 0)) fail_errno("setresgid");
    if (setresuid(0, 0, 0)) fail_errno("setresuid");
    if (getuid() != 0 || geteuid() != 0 || getgid() != 0) fail("root credentials not established");
    if (argc == 1) execl("/system/bin/sh", "sh", (char *)NULL);
    else if (argc == 3 && !strcmp(argv[1], "-c")) execl("/system/bin/sh", "sh", "-c", argv[2], (char *)NULL);
    else fail("usage: onn26-su [-c command]");
    fail(strerror(errno));
    return 1;
}
