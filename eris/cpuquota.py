# Copyright (C) 2018 Intel Corporation
#  
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#  
# http://www.apache.org/licenses/LICENSE-2.0
#  
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions
# and limitations under the License.
#  
#
# SPDX-License-Identifier: Apache-2.0

""" This module implements CPU cycle control based on CFS quota """

import subprocess
from datetime import datetime
from mresource import Resource


class CpuQuota(Resource):
    """ This class is the resource class of CPU cycle """
    CPU_QUOTA_DEFAULT = -1
    CPU_QUOTA_MIN = 1000
    CPU_QUOTA_CORE = 100000
    CPU_QUOTA_PERCENT = CPU_QUOTA_CORE / 100
    CPU_QUOTA_HALF_CORE = CPU_QUOTA_CORE * 0.5
    CPU_SHARE_BE = 2
    CPU_SHARE_LC = 200000

    def __init__(self, sysMaxUtil, minMarginRatio, verbose):
        super().__init__()
        self.min_margin_ratio = minMarginRatio
        self.update_max_sys_util(sysMaxUtil)
        self.update()
        self.verbose = verbose

    def update(self):
        if self.is_full_level():
            self.cpu_quota = CpuQuota.CPU_QUOTA_DEFAULT
        elif self.is_min_level():
            self.cpu_quota = CpuQuota.CPU_QUOTA_MIN
        else:
            self.cpu_quota = self.quota_level * int(self.quota_step)

    def update_max_sys_util(self, lc_max_util):
        """
        Update quota max and step based on given LC system maximal utilization
        monitored
            lc_max_util - maximal LC workloads utilization monitored
        """
        self.quota_max = lc_max_util * CpuQuota.CPU_QUOTA_PERCENT
        self.quota_step = self.quota_max / Resource.BUGET_LEV_MAX

    @staticmethod
    def __get_cfs_period(container):
        result = subprocess.run(['cat', '/sys/fs/cgroup/cpu/docker/' +
                                 container.cid + '/cpu.cfs_period_us'],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        res = result.stdout.decode('utf-8').strip()
        try:
            period = int(res)
            return period
        except ValueError:
            return 0

    def __set_quota(self, container, quota):
        period = self.__get_cfs_period(container)
        if period != 0 and quota != CpuQuota.CPU_QUOTA_DEFAULT\
           and quota != CpuQuota.CPU_QUOTA_MIN:
            rquota = int(quota * period / CpuQuota.CPU_QUOTA_CORE)
        else:
            rquota = quota
        subprocess.Popen('echo ' + str(rquota) + ' > ' +
                         '/sys/fs/cgroup/cpu/docker/' +
                         container.cid + '/cpu.cfs_quota_us',
                         shell=True)
        print(datetime.now().isoformat(' ') + ' set container ' +
              container.name + ' cpu quota to ' + str(rquota))

    @staticmethod
    def set_share(container, share):
        """
        Set CPU share in container
            share - given CPU share value
        """
        subprocess.Popen('echo ' + str(share) + ' > ' +
                         '/sys/fs/cgroup/cpu/docker/' +
                         container.cid + '/cpu.shares',
                         shell=True)
        print(datetime.now().isoformat(' ') + ' set container ' +
              container.name + ' cpu share to ' + str(share))

    def budgeting(self, containers):
        newq = int(self.cpu_quota / len(containers))
        for con in containers:
            if self.is_min_level() or self.is_full_level():
                self.__set_quota(con, self.cpu_quota)
            else:
                self.__set_quota(con, newq)

    def detect_margin_exceed(self, lc_utils, be_utils):
        """
        Detect if BE workload utilization exceed the safe margin
            lc_utils - utilization of all LC workloads
            be_utils - utilization of all BE workloads
        """
        beq = self.cpu_quota
        margin = CpuQuota.CPU_QUOTA_CORE * self.min_margin_ratio

        if self.verbose:
            print(datetime.now().isoformat(' ') + ' lcUtils: ', lc_utils,
                  ' beUtils: ', be_utils, ' beq: ', beq, ' margin: ', margin)

        exceed = lc_utils == 0 or (lc_utils + be_utils) *\
            CpuQuota.CPU_QUOTA_PERCENT + margin > self.quota_max

        hold = (lc_utils + be_utils) * CpuQuota.CPU_QUOTA_PERCENT +\
            margin + self.quota_step >= self.quota_max

        return (exceed, hold)
