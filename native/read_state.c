/* Ordinary-shell AVB lock-state query. Fixed command 2; no write command. */
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/tee.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (getuid() != 2000 || geteuid() != 2000) return 2;
    alarm(8);
    int fd = open("/dev/tee0", O_RDWR | O_CLOEXEC);
    if (fd < 0) return 3;
    struct tee_ioctl_version_data version = {0};
    if (ioctl(fd, TEE_IOC_VERSION, &version) < 0 ||
        version.impl_id != TEE_IMPL_ID_OPTEE || !(version.gen_caps & TEE_GEN_CAP_GP)) {
        close(fd);
        return 4;
    }
    size_t open_size = sizeof(struct tee_ioctl_open_session_arg) + 4 * sizeof(struct tee_ioctl_param);
    struct tee_ioctl_open_session_arg *open_arg = calloc(1, open_size);
    if (!open_arg) { close(fd); return 5; }
    const uint8_t uuid[16] = {
        0x02,0x3f,0x8f,0x1a,0x29,0x2a,0x43,0x2b,
        0x8f,0xc4,0xde,0x84,0x71,0x35,0x80,0x67
    };
    memcpy(open_arg->uuid, uuid, sizeof(uuid));
    open_arg->clnt_login = TEE_IOCTL_LOGIN_PUBLIC;
    open_arg->num_params = 4;
    open_arg->ret = UINT32_MAX;
    struct tee_ioctl_buf_data buffer = {.buf_ptr=(uintptr_t)open_arg,.buf_len=open_size};
    int result = ioctl(fd, TEE_IOC_OPEN_SESSION, &buffer);
    printf("{\"stage\":\"open\",\"rc\":%d,\"errno\":%d,\"ret\":\"0x%08x\",\"origin\":%u}\n",
           result, result < 0 ? errno : 0, open_arg->ret, open_arg->ret_origin);
    if (result < 0 || open_arg->ret != 0) { free(open_arg); close(fd); return 6; }
    const size_t query_size = sizeof(struct tee_ioctl_invoke_arg) + 4 * sizeof(struct tee_ioctl_param);
    struct tee_ioctl_invoke_arg *query = calloc(1, query_size);
    int exit_status = 7;
    if (query) {
        query->func = 2; /* TA_AVB_CMD_READ_LOCK_STATE */
        query->session = open_arg->session;
        query->num_params = 4;
        query->ret = UINT32_MAX;
        query->params[0].attr = TEE_IOCTL_PARAM_ATTR_TYPE_VALUE_OUTPUT;
        query->params[0].a = UINT32_MAX;
        buffer.buf_ptr = (uintptr_t)query;
        buffer.buf_len = query_size;
        printf("{\"stage\":\"query\",\"command\":2,\"write_commands\":0}\n");
        result = ioctl(fd, TEE_IOC_INVOKE, &buffer);
        printf("{\"stage\":\"lock_state\",\"rc\":%d,\"errno\":%d,\"ret\":\"0x%08x\","
               "\"origin\":%u,\"a\":%" PRIu64 ",\"b\":%" PRIu64 ",\"c\":%" PRIu64 "}\n",
               result, result < 0 ? errno : 0, query->ret, query->ret_origin,
               (uint64_t)query->params[0].a, (uint64_t)query->params[0].b, (uint64_t)query->params[0].c);
        if (result == 0 && query->ret == 0 && query->params[0].a <= 1) exit_status = 0;
    }
    struct tee_ioctl_close_session_arg end = {.session=open_arg->session};
    result = ioctl(fd, TEE_IOC_CLOSE_SESSION, &end);
    printf("{\"stage\":\"close\",\"rc\":%d,\"errno\":%d}\n",result,result<0?errno:0);
    if (result < 0) exit_status = 8;
    free(query);
    free(open_arg);
    close(fd);
    alarm(0);
    printf("{\"stage\":\"finished\",\"uid\":%u,\"euid\":%u,\"write_commands\":0}\n",
           (unsigned)getuid(),(unsigned)geteuid());
    return exit_status;
}
