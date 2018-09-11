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

"""
This module implements resource contention detection on one workload
"""

import subprocess
from datetime import datetime
import time
from enum import Enum
from collections import deque


class Contention(Enum):
    """ This enumeration defines resource contention type """
    UNKN = 1
    CPU_CYC = 2
    LLC = 3
    MEM_BW = 4
    TDP = 5


class Container:
    """
    This class is the abstraction of one task, container metrics and
    contention detection method are encapsulated in this module
    """

    def __init__(self, cid, cn, pids, verbose, thresh=[], tdp_thresh=[],
                 historyDepth=5):
        self.cid = cid
        self.name = cn
        self.pids = pids
        self.cpu_usage = 0
        self.utils = 0
        self.timestamp = 0.0
        self.thresh = thresh
        self.tdp_thresh = tdp_thresh
        self.verbose = verbose
        self.metrics = dict()
        self.historyDepth = historyDepth + 1
        self.metricsHistory = deque([], self.historyDepth)
        self.cpusets = []

    '''
    add metric data to metrics history
    metrics history only contains the most recent metrics data, defined by
    self.historyDepth if histroy metrics data length exceeds the
    self.historyDepth, the oldest data will be erased
    '''
    def updateMetricsHistory(self):
        self.metricsHistory.append(self.metrics.copy())

    def getHistoryDeltaByType(self, columnname):
        length = len(self.metricsHistory)
        if length == 0:
            return 0

        if length == 1:
            return self.metricsHistory[length - 1][columnname]

        data_sum = 0

        for x in range(length - 1):
            data_sum = data_sum + self.metricsHistory[x][columnname]

        data_delta = self.metricsHistory[length - 1][columnname] -\
            data_sum / (length - 1)

        return data_delta

    def getLLCOccupanyDelta(self):
        return self.getHistoryDeltaByType('L3OCC')

    def getFreqDelta(self):
        return self.getHistoryDeltaByType('NF')

    def getLatestMBT(self):
        return self.metrics['MBL'] + self.metrics['MBR']

    def get_metrics(self):
        """ retrieve container platform metrics """
        return self.metrics

    def update_pids(self, pids):
        """
        update process ids of one Container
            pids - pid list of Container
        """
        self.pids = pids

    def update_cpu_usage(self):
        """ calculate cpu usage of container """
        cur = time.time() * 1e9
        result = subprocess.run(['cat', '/sys/fs/cgroup/cpu/docker/' +
                                 self.cid + '/cpuacct.usage'],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        res = result.stdout.decode('utf-8').strip()
        try:
            usg = int(res)
            if self.cpu_usage != 0:
                self.utils = (usg - self.cpu_usage) * 100 /\
                    (cur - self.timestamp)
            self.cpu_usage = usg
            self.timestamp = cur
        except ValueError:
            pass

    def __detect_in_bin(self, thresh):
        metrics = self.metrics
        if metrics['CPI'] > thresh['cpi']:
            if metrics['L3MPKI'] > thresh['mpki']:
                print('Last Level Cache contention is detected at ' +
                      datetime.now().isoformat(' '))
                print('Latency critical container ' + self.name + ', CPI = ' +
                      str(metrics['CPI']) + ', MKPI = ' +
                      str(metrics['L3MPKI']) + '\n')
                return Contention.LLC
            if metrics['MBL'] + metrics['MBR'] < thresh['mb']:
                print('Memory Bandwidth contention detected at ' +
                      datetime.now().isoformat(' '))
                print('Latency critical container ' + self.name + ', CPI = ' +
                      str(metrics['CPI']) + ', MBL = ' + str(metrics['MBL']) +
                      ', MBR = ' + str(metrics['MBR']) + '\n')
                return Contention.MEM_BW

            print('Performance is impacted at ' +
                  datetime.now().isoformat(' '))
            print('Latency critical container ' + self.name +
                  ' CPI exceeds threshold, value = ', str(metrics['CPI']))
            return Contention.UNKN

        return None

    def tdp_contention_detect(self):
        """ detect TDP contention in container """
        if not self.tdp_thresh:
            return None

        if self.verbose:
            print(self.utils, self.metrics['NF'], self.tdp_thresh['util'],
                  self.tdp_thresh['bar'])

        if self.utils >= self.tdp_thresh['util'] and\
           self.metrics['NF'] < self.tdp_thresh['bar']:
            print('TDP Contention Alert!')
            return Contention.TDP

        return None

    def contention_detect(self):
        """ detect resouce contention after find proper utilization bin """
        if not self.thresh:
            return None

        for i in range(0, len(self.thresh)):
            thresh = self.thresh[i]
            if self.utils < thresh['util_start']:
                if i == 0:
                    return None

                return self.__detect_in_bin(self.thresh[i - 1])

            if self.utils >= thresh['util_start']:
                if self.utils < thresh['util_end'] or\
                   i == len(self.thresh) - 1:
                    return self.__detect_in_bin(thresh)

    def __str__(self):
        metrics = self.metrics
        return metrics['TIME'].isoformat() + ',' + self.cid + ',' +\
            self.name + ',' + str(metrics['INST']) + ',' +\
            str(metrics['CYC']) + ',' + str(metrics['CPI']) + ',' +\
            str(metrics['L3MPKI']) + ',' + str(metrics['L3MISS']) + ',' +\
            str(metrics['NF']) + ',' + str(self.utils) + ',' +\
            str(metrics['L3OCC']) + ',' + str(metrics['MBL']) + ',' +\
            str(metrics['MBR']) + '\n'
