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

""" This module implements last level cache control based on pqos tool """

import subprocess
from datetime import datetime
from mresource import Resource


class LlcOccup(Resource):
    """ This class is the resource class of LLC occupancy """
    LLC_BMP_LG = ['0x7', '0x1f', '0x7f', '0x1ff', '0x7ff',
                  '0x1fff', '0x7fff', '0x1ffff', '0x7ffff', '0xfffff']
    LLC_BMP = ['0x1', '0x3', '0x7', '0xf', '0x1f', '0x3f', '0x7f', '0xff',
               '0x1ff', '0x3ff', '0x7ff', '0xfff', '0x1fff', '0x3fff',
               '0x7fff', '0xffff', '0x1ffff', '0x3ffff', '0x7ffff', '0xfffff']

    USE_PQOS = True

    def budgeting(self, containers):
        cpids = []
        cns = []
        for con in containers:
            cpids.append(','.join(con.pids))
            cns.append(con.name)

        if LlcOccup.USE_PQOS:
            # in POC, assume only eris controls CAT, use fixed CLOS number 1
            cml = 'pqos -I -a' + '\'pid:1=' + ','.join(cpids) + '\''
            subprocess.Popen(cml, shell=True)

        if self.is_full_level() or self.quota_level >= len(LlcOccup.LLC_BMP):
            if LlcOccup.USE_PQOS:
                cml = 'pqos -e' + '\'llc:1=' +\
                    LlcOccup.LLC_BMP[len(LlcOccup.LLC_BMP) - 1] + '\''
            else:
                cml = 'rdtset -t ' + '\'l3=' +\
                        LlcOccup.LLC_BMP[len(LlcOccup.LLC_BMP) - 1] + '\''\
                        ' -I -p ' + ','.join(cpids)
            subprocess.Popen(cml, shell=True)

            print(datetime.now().isoformat(' ') +
                  ' set best effort container ' + ','.join(cns) +
                  ' llc occupancy to ' +
                  LlcOccup.LLC_BMP[len(LlcOccup.LLC_BMP) - 1])
        else:
            if LlcOccup.USE_PQOS:
                cml = 'pqos -e' + '\'llc:1=' +\
                    LlcOccup.LLC_BMP[self.quota_level] + '\''
            else:
                cml = 'rdtset -t ' + '\'l3=' +\
                    LlcOccup.LLC_BMP[self.quota_level] + '\''\
                    ' -I -p ' + ','.join(cpids)
            subprocess.Popen(cml, shell=True)

            print(datetime.now().isoformat(' ') +
                  ' set best effort container ' +
                  ','.join(cns) + ' llc occupancy to ' +
                  LlcOccup.LLC_BMP[self.quota_level])
