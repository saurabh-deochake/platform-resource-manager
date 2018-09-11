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

""" This module defines general resource control methods """


class Resource:
    """ Resource Class is abstraction of resource """
    BUGET_LEV_FULL = -1
    BUGET_LEV_MIN = 0
    BUGET_LEV_MAX = 20

    def __init__(self, init_level=BUGET_LEV_MIN):
        self.quota_level = init_level

    def is_min_level(self):
        """ is resource controled in lowest level """
        return self.quota_level == Resource.BUGET_LEV_MIN

    def is_full_level(self):
        """ is resource controled in full level """
        return self.quota_level == Resource.BUGET_LEV_FULL

    def set_level(self, level):
        """ set resource in given level """
        self.quota_level = level
        self.update()

    def increase_level(self):
        """ increase resource to next level """
        self.quota_level = self.quota_level + 1
        if self.quota_level == Resource.BUGET_LEV_MAX:
            self.quota_level = Resource.BUGET_LEV_FULL
        self.update()

    def update(self):
        """ update resource level to real value of concrete resource class """
        pass

    def budgeting(self, containers):
        """ control resouce based on current resource level """
        pass
