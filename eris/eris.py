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

""" This module implements resource monitor and control agent """


import os
import argparse
import subprocess
from datetime import datetime
import threading
import time
import sys
import traceback
import pandas as pd
import docker
import numpy as np
from container import Contention, Container
from mresource import Resource
from cpuquota import CpuQuota
from llcoccup import LlcOccup
from naivectrl import NaiveController
from prometheus import PrometheusClient


class Context:
    """ This class encapsulate all configuration and args """

    def __init__(self):
        self.interrupt = False
        self.args = None
        self.tdp_file = 'tdp_thresh.csv'
        self.sysmax_file = 'lcmax.txt'
        self.sysmax_util = 0
        self.lc_set = {}
        self.be_set = {}
        self.cpuq = None
        self.llc = None
        self.controllers = {}
        self.util_cons = dict()
        self.metric_cons = dict()
        self.thresh_map = dict()
        self.tdp_thresh_map = dict()
        self.prometheus = None


def set_metrics(ctx, data):
    """
    This function collect metrics from pgos tool and trigger resource
    contention detection and control
        ctx - agent context
        data - metrics data collected from pgos
    """
    timestamp = datetime.now()
    for line in data:
        items = line.split('\t')
        if len(items) >= 4:
            cid = items[0]
            metric_name = items[1]
            val = items[3]
            container = ctx.metric_cons[cid]
            metrics = container.get_metrics()
            if metric_name == 'cycles':
                metrics['CYC'] = int(val)
            elif metric_name == 'instructions':
                metrics['INST'] = int(val)
            elif metric_name == 'LLC misses':
                metrics['L3MISS'] = int(val)
            elif metric_name == 'LLC occupancy':
                metrics['L3OCC'] = int(val)
            elif metric_name == 'Memory bandwidth local':
                metrics['MBL'] = float(val)
            elif metric_name == 'Memory bandwidth remote':
                metrics['MBR'] = float(val)

    contention = {Contention.LLC: False, Contention.MEM_BW: False,
                  Contention.UNKN: False}
    contention_map = {}
    bes = []
    findbe = False
    for cid, con in ctx.metric_cons.items():
        if ctx.args.key_cid:
            key = con.cid
        else:
            key = con.name

        if key in ctx.lc_set:
            con.update_cpu_usage()
            metrics = con.get_metrics()
            if metrics:
                metrics['TIME'] = timestamp
                if metrics['INST'] == 0:
                    metrics['CPI'] = 0
                    metrics['L3MPKI'] = 0
                else:
                    metrics['CPI'] = metrics['CYC'] / metrics['INST']
                    metrics['L3MPKI'] = metrics['L3MISS'] * 1000 /\
                        metrics['INST']
                if con.utils == 0:
                    metrics['NF'] = 0
                else:
                    metrics['NF'] = int(metrics['CYC'] /
                                        ctx.args.metric_interval /
                                        10000 / con.utils)
                if ctx.args.detect:
                    con.update_metrics_history()

                if ctx.args.record:
                    with open('./metrics.csv', 'a') as metricf:
                        metricf.write(str(con))

                    if ctx.args.enable_prometheus:
                        ctx.prometheus.send_metrics(con.name, con.utils,
                                                    metrics['CYC'],
                                                    metrics['L3MISS'],
                                                    metrics['INST'],
                                                    metrics['NF'],
                                                    metrics['MBR'] +
                                                    metrics['MBL'],
                                                    metrics['L3OCC'], 0)

                if ctx.args.detect:
                    contend = con.contention_detect()
                    if_contended = False

                    if contend is not None:
                        if_contended = True
                        contention[contend] = True

                    tdp_contend = con.tdp_contention_detect()
                    if tdp_contend is not None:
                        if_contended = True
                        contention[tdp_contend] = True

                    if if_contended:
                        contention_map[con] = contention.copy()

        if key in ctx.be_set:
            findbe = True
            bes.append(con)

    if ctx.args.detect:
        for container_contended, contention_list in contention_map.items():
            for contention_type, contention_type_if_happened\
                    in contention_list.items():
                if contention_type_if_happened and\
                   contention_type != Contention.UNKN:
                    resource_delta_max = -np.Inf
                    suspect = "unknown"

                    for cid, container in ctx.metric_cons.items():
                        delta = 0
                        if cid == container_contended.cid:
                            continue
                        if contention_type == Contention.LLC:
                            delta = container.get_llcoccupany_delta()
                        elif contention_type == Contention.MB:
                            delta = container.get_latest_mbt()
                        elif contention_type == Contention.TDP:
                            delta = container.get_freq_delta()

                        if delta > 0 and delta > resource_delta_max:
                            resource_delta_max = delta
                            suspect = container.name

                    print('Contention %s for container %s: Suspect is %s' %
                          (contention_type, container_contended.name, suspect))

    if findbe and ctx.args.control:
        for contention, flag in contention.items():
            if contention in ctx.controllers:
                ctx.controllers[contention].update(bes, flag, False)


def remove_finish_containers(containers, consmap):
    """
    remove finished containers from cached container map
        containers - container list from docker
        mon_cons - cached container map
    """
    idset = set()
    for container in containers:
        idset.add(container.id)

    for cid in consmap.copy():
        if cid not in idset:
            del consmap[cid]


def list_docker_containers():
    """ list all containers from docker """
    client = docker.from_env()
    return client.containers.list()


def list_pids(con):
    """
    list all process id of one container
        con - container object listed from docker
    """
    res = con.top()
    procs = res['Processes']
    pids = []
    if procs:
        for pid in procs:
            pids.append(pid[1])
    return pids


def mon_util_cycle(ctx):
    """
    CPU utilization monitor timer function
        ctx - agent context
    """
    findbe = False
    lc_utils = 0
    be_utils = 0
    date = datetime.now().isoformat()
    bes = []
    containers = list_docker_containers()
    remove_finish_containers(containers, ctx.util_cons)

    for container in containers:
        cid = container.id
        name = container.name
        pids = list_pids(container)
        if ctx.args.key_cid:
            key = cid
        else:
            key = name
        if cid in ctx.util_cons:
            con = ctx.util_cons[cid]
        else:
            con = Container(cid, name, pids, ctx.args.verbose)
            ctx.util_cons[cid] = con
            if ctx.args.control:
                if key in ctx.be_set:
                    ctx.cpuq.budgeting([con])
                    ctx.cpuq.set_share(con, CpuQuota.CPU_SHARE_BE)
                else:
                    ctx.cpuq.set_share(con, CpuQuota.CPU_SHARE_LC)
        con.update_cpu_usage()
        if ctx.args.record:
            with open('./util.csv', 'a') as utilf:
                utilf.write(date + ',' + cid + ',' + name +
                            ',' + str(con.utils) + '\n')

        if key in ctx.lc_set:
            lc_utils = lc_utils + con.utils

        if key in ctx.be_set:
            findbe = True
            be_utils = be_utils + con.utils
            bes.append(con)

    loadavg = os.getloadavg()[0]
    if ctx.args.record:
        with open('./util.csv', 'a') as utilf:
            utilf.write(date + ',,lcs,' + str(lc_utils) + '\n')
            utilf.write(date + ',,loadavg1m,' + str(loadavg) + '\n')

    if lc_utils > ctx.sysmax_util:
        update_sysmax(ctx, lc_utils)
        if ctx.args.control:
            ctx.cpuq.update_max_sys_util(lc_utils)

    if findbe and ctx.args.control:
        exceed, hold = ctx.cpuq.detect_margin_exceed(lc_utils, be_utils)
        if not ctx.args.enable_hold:
            hold = False
        ctx.controllers[Contention.CPU_CYC].update(bes, exceed, hold)


def mon_metric_cycle(ctx):
    """
    Platform metrics monitor timer function
        ctx - agent context
    """
    cgps = []
    new_bes = []
    containers = list_docker_containers()
    remove_finish_containers(containers, ctx.metric_cons)

    for container in containers:
        cid = container.id
        name = container.name
        pids = list_pids(container)
        if ctx.args.key_cid:
            key = cid
        else:
            key = name
        if cid in ctx.metric_cons:
            con = ctx.metric_cons[cid]
            con.update_pids(pids)
        else:
            thresh = ctx.thresh_map.get(key, [])
            tdp_thresh = ctx.tdp_thresh_map.get(key, [])
            con = Container(cid, name, pids, ctx.args.verbose,
                            thresh, tdp_thresh)
            ctx.metric_cons[cid] = con
            con.update_cpu_usage()
            if key in ctx.be_set and ctx.args.control\
               and not ctx.args.disable_cat:
                new_bes.append(con)

        if key in ctx.lc_set:
            cgps.append('/sys/fs/cgroup/perf_event/docker/' + cid)

    if new_bes:
        ctx.llc.budgeting(new_bes)

    if cgps:
        result = subprocess.run(['./pgos', '-cgroup', ','.join(cgps),
                                 '-period', '18', '-frequency', '18',
                                 '-cycle', '1', '-core', str(os.cpu_count())],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        data = result.stdout.decode('utf-8').splitlines()
        set_metrics(ctx, data)


def monitor(func, ctx, interval):
    """
    wrap schedule timer function
        ctx - agent context
        interval - timer interval
    """
    next_time = time.time()
    while not ctx.interrupt:
        func(ctx)
        while True:
            next_time += interval
            delta = next_time - time.time()
            if delta > 0:
                break
        time.sleep(delta)


def init_threshbins(jdata):
    """
    Initialize thresholds in all bins for one workload
        jdata - thresholds data for one workload
    """
    threshbins = []
    for row_turple in jdata.iterrows():
        row = row_turple[1]
        thresh = dict()
        thresh['util_start'] = row['UTIL_START']
        thresh['util_end'] = row['UTIL_END']
        thresh['cpi'] = row['CPI_THRESH']
        thresh['mpki'] = row['MPKI_THRESH']
        thresh['mb'] = row['MB_THRESH']
        threshbins.append(thresh)
    return threshbins


def init_tdp_map(ctx):
    """
    Initialize thresholds for TDP contention for all workloads
        ctx - agent context
    """
    if ctx.args.key_cid:
        key = 'CID'
    else:
        key = 'CNAME'
    tdp_df = pd.read_csv(ctx.tdp_file)
    cids = tdp_df[key].unique()
    for cid in cids:
        tdpdata = tdp_df[tdp_df[key] == cid]

        for row_turple in tdpdata.iterrows():
            row = row_turple[1]
            thresh = dict()
            thresh['util'] = row['UTIL']
            thresh['mean'] = row['MEAN']
            thresh['std'] = row['STD']
            thresh['bar'] = row['BAR']
            ctx.tdp_thresh_map[cid] = thresh

    if ctx.args.verbose:
        print(ctx.tdp_thresh_map)


def init_threshmap(ctx):
    """
    Initialize thresholds for other contentions for all workloads
        ctx - agent context
    """
    if ctx.args.key_cid:
        key = 'CID'
    else:
        key = 'CNAME'

    thresh_file = 'thresh.csv'
    if ctx.args.thresh_file is not None:
        thresh_file = ctx.args.thresh_file
    thresh_df = pd.read_csv(thresh_file)
    cids = thresh_df[key].unique()
    for cid in cids:
        jdata = thresh_df[thresh_df[key] == cid].sort_values('UTIL_START')
        bins = init_threshbins(jdata)
        ctx.thresh_map[cid] = bins

    if ctx.args.verbose:
        print(ctx.thresh_map)


def init_wlset(ctx):
    """
    Initialize workload set for both LC and BE
        ctx - agent context
    """
    if ctx.args.key_cid:
        key = 'CID'
    else:
        key = 'CNAME'
    wl_df = pd.read_csv(ctx.args.workload_conf_file)
    lcs = []
    bes = []
    for row_turple in wl_df.iterrows():
        row = row_turple[1]
        workload = row[key]
        if row['TYPE'] == 'LC':
            lcs.append(workload)
        else:
            bes.append(workload)
    ctx.lc_set = set(lcs)
    ctx.be_set = set(bes)
    if ctx.args.verbose:
        print(ctx.lc_set)
        print(ctx.be_set)


def update_sysmax(ctx, lc_utils):
    """
    Update system maximal utilization based on utilization of LC workloads
        ctx - agent context
        lc_utils - monitored LC workload utilization maximal value
    """
    ctx.sysmax_util = int(lc_utils)
    subprocess.Popen('echo ' + str(ctx.sysmax_util) + ' > ' + ctx.sysmax_file,
                     shell=True)


def init_sysmax(ctx):
    """
    Initialize historical system maximal utilization from model file
        ctx - agent context
    """
    result = subprocess.run(['cat', ctx.sysmax_file],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    res = result.stdout.decode('utf-8').strip()
    try:
        ctx.sysmax_util = int(res)
    except ValueError:
        ctx.sysmax_util = os.cpu_count() * 100
    if ctx.args.verbose:
        print(ctx.sysmax_util)


def parse_arguments():
    """ agent command line arguments parse function """

    parser = argparse.ArgumentParser(description='eris agent monitor\
                                     container CPU utilization and platform\
                                     metrics, detect potential resource\
                                     contention and regulate best-efforts\
                                     tasks resource usages')
    parser.add_argument('workload_conf_file', help='workload configuration\
                        file describes each task name, type, id, request cpu\
                        count', type=argparse.FileType('rt'), default='wl.csv')
    parser.add_argument('-v', '--verbose', help='increase output verbosity',
                        action='store_true')
    parser.add_argument('-g', '--collect-metrics', help='collect platform\
                        performance metrics (CPI, MPKI, etc..)',
                        action='store_true')
    parser.add_argument('-d', '--detect', help='detect resource contention\
                        between containers', action='store_true')
    parser.add_argument('-c', '--control', help='regulate best-efforts task\
                        resource usages', action='store_true')
    parser.add_argument('-r', '--record', help='record container CPU\
                        utilizaton and platform metrics in csv file',
                        action='store_true')
    parser.add_argument('-i', '--key-cid', help='use container id in workload\
                        configuration file as key id', action='store_true')
    parser.add_argument('-e', '--enable-hold', help='keep container resource\
                        usage in current level while the usage is close but\
                        not exceed throttle threshold ', action='store_true')
    parser.add_argument('-n', '--disable-cat', help='disable CAT control while\
                        in resource regulation', action='store_true')
    parser.add_argument('-p', '--enable_prometheus', help='allow eris send\
                        metrics to prometheus', action='store_true')
    parser.add_argument('-u', '--util-interval', help='CPU utilization monitor\
                        interval', type=int, default=2)
    parser.add_argument('-m', '--metric-interval', help='platform metrics\
                        monitor interval', type=int, default=20)
    parser.add_argument('-l', '--llc-cycles', help='cycle number in LLC\
                        controller', type=int, default=6)
    parser.add_argument('-q', '--quota-cycles', help='cycle number in CPU CFS\
                        quota controller', type=int, default=7)
    parser.add_argument('-k', '--margin-ratio', help='margin ratio related to\
                        one logical processor used in CPU cycle regulation',
                        type=float, default=0.5)
    parser.add_argument('-t', '--thresh-file', help='threshold model file build\
                        from analyze.py tool', type=argparse.FileType('rt'))

    args = parser.parse_args()
    if args.verbose:
        print(args)
    return args


def main():
    """ Script entry point. """
    ctx = Context()
    ctx.args = parse_arguments()
    init_wlset(ctx)
    init_sysmax(ctx)

    if ctx.args.enable_prometheus:
        ctx.prometheus = PrometheusClient()
        ctx.prometheus.start()

    if ctx.args.detect:
        init_threshmap(ctx)
        init_tdp_map(ctx)

    if ctx.args.control:
        ctx.cpuq = CpuQuota(ctx.sysmax_util, ctx.args.margin_ratio,
                            ctx.args.verbose)
        quota_controller = NaiveController(ctx.cpuq, ctx.args.quota_cycles)
        ctx.llc = LlcOccup()
        llc_controller = NaiveController(ctx.llc, ctx.args.llc_cycles)
        if ctx.args.disable_cat:
            ctx.llc = LlcOccup(init_level=Resource.BUGET_LEV_FULL)
            ctx.controllers = {Contention.CPU_CYC: quota_controller}
        else:
            ctx.controllers = {Contention.CPU_CYC: quota_controller,
                               Contention.LLC: llc_controller}
    if ctx.args.record:
        with open('./util.csv', 'w') as utilf:
            utilf.write('TIME,CID,CNAME,UTIL\n')

    util_thread = threading.Thread(target=monitor,
                                   args=(mon_util_cycle, ctx,
                                         ctx.args.util_interval))
    util_thread.start()
    if ctx.args.collect_metrics:
        if ctx.args.record:
            with open('./metrics.csv', 'w') as metricf:
                metricf.write('TIME,CID,CNAME,INST,CYC,CPI,L3MPKI,' +
                              'L3MISS,NF,UTIL,L3OCC,MBL,MBR\n')

        metric_thread = threading.Thread(target=monitor,
                                         args=(mon_metric_cycle, ctx,
                                               ctx.args.metric_interval))
        metric_thread.start()
    print('eris agent version', __version__, 'is started!')
    try:
        util_thread.join()
        if ctx.args.collect_metrics:
            metric_thread.join()
    except KeyboardInterrupt:
        print('Shutdown eris agent ...exiting')
        ctx.interrupt = True
    except Exception:
        traceback.print_exc(file=sys.stdout)
    sys.exit(0)

__version__ = 0.8
if __name__ == '__main__':
    main()
