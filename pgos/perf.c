// Copyright (C) 2018 Intel Corporation
// 
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
// 
// http://www.apache.org/licenses/LICENSE-2.0
// 
// Unless required by applicable law or agreed to in writing,
// software distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions
// and limitations under the License.
// 
// 
// SPDX-License-Identifier: Apache-2.0
// 
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <sys/ioctl.h>
#include <linux/perf_event.h>
#include <asm/unistd.h>
#include <stdint.h>
#include <fcntl.h>


static long perf_event_open(struct perf_event_attr *hw_event, pid_t pid, int cpu, int group_fd, unsigned long flags)
{
    int ret;
    ret = syscall(__NR_perf_event_open, hw_event, pid, cpu, group_fd, flags);
    return ret;
}

static int open_perf_fd(pid_t pid, int cpu, uint64_t metric) {
    struct perf_event_attr pe;
    memset(&pe, 0, sizeof(struct perf_event_attr));
    pe.type = PERF_TYPE_HARDWARE;
    pe.size = sizeof(struct perf_event_attr);
    pe.config = metric;
    pe.disabled = 1;

    int fd = perf_event_open(&pe, pid, cpu, -1, PERF_FLAG_PID_CGROUP);
    return fd;
}

static void collect(pid_t* pids, int pid_count, int cpus, uint64_t* metrics, int metrics_count, uint64_t* result, unsigned period)  {
    int64_t count = 0; 
    int i, j, k;
    int fds[10000], fd_index = 0, result_index = 0;
    for (i = 0;i < pid_count;i ++) {
        for (j = 0;j < metrics_count;j ++) {
            for (k = 0;k < cpus;k ++) {
                fds[fd_index] = open_perf_fd(pids[i], k, metrics[j]);
                if (fds[fd_index] == -1) {
                    printf("fail to open perf event\n");
                    fflush(stdout);
                    return;
                }
                fd_index ++;

            }
        }
    }

    for (i = 0;i < fd_index;i ++) {
        ioctl(fds[i], PERF_EVENT_IOC_RESET, 0);
        ioctl(fds[i], PERF_EVENT_IOC_ENABLE, 0);
    }
    sleep(period);
    fd_index = 0;
    for (i = 0;i < pid_count;i ++) {
        for (j = 0;j < metrics_count;j ++) {
            for (k = 0;k < cpus;k ++) {
                ioctl(fds[fd_index], PERF_EVENT_IOC_DISABLE, 0);
                int n = read(fds[fd_index], &count, sizeof(int64_t));
                if (n == -1) {
                    continue;
                }
                close(fds[fd_index]);
                fd_index ++;
                result[result_index] += count;
            }
            result_index ++;
        }
    }
    return;
}

