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
#include <pqos.h>
#include <stdio.h>

#define MAX_PID_GROUP 100
typedef struct pqos_config pqos_config;
typedef struct pqos_mon_data pqos_mon_data;
typedef struct pqos_event_values pqos_event_values;

struct pqos_mon_data data[MAX_PID_GROUP];
int idx = 0;
int pgos_mon_start_pids(unsigned pid_num, pid_t *pids) {
    if (idx >= MAX_PID_GROUP) {
        return -1;
    }
	int ret = pqos_mon_start_pids(pid_num, pids, PQOS_MON_EVENT_L3_OCCUP | PQOS_MON_EVENT_LMEM_BW |PQOS_MON_EVENT_RMEM_BW  , NULL, &data[idx]);
    return idx ++;
}

struct pqos_event_values pgos_mon_poll(int index) {
    if (index < 0 || index > idx) {
        pqos_event_values zero_ret;
        memset(&zero_ret, 0, sizeof(pqos_event_values));
        return zero_ret;
    }
    struct pqos_mon_data *data_addr = &data[index];
    int ret = pqos_mon_poll(&data_addr, 1);
    return data[index].values;
}

void pgos_mon_stop() {
    int i;
    for (i = 0;i < idx;i ++) {
        pqos_mon_stop(&data[i]);
    }
}