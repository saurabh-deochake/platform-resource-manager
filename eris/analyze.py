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

""" This module implements platform metrics data analysis. """
import argparse
import numpy as np
import pandas as pd
from scipy import stats
from gmmfense import GmmFense


def get_quartile(args, mdf, is_upper):
    """
    Get turkey fense based on quartile statistics.
        args - arguments from command line input
        mdf - platform metrics dataframe
        is_upper - True if upper fense is needed,
                   False if lower fense is needed
    """
    mdf = mdf.sort_values()
    quar1 = mdf.iloc[int(mdf.size / 4)]
    quar3 = mdf.iloc[int(mdf.size * 3 / 4)]
    iqr = quar3 - quar1

    if args.verbose:
        print('min: ', mdf.iloc[0], ' q1: ', quar1, ' q3: ',
              quar3, ' max: ', mdf.iloc[mdf.size - 1])

    val = iqr * (args.thresh * 3 / 4 - 2 / 3)
    if is_upper:
        return quar3 + val

    return quar1 - val


def get_normal(args, mdf, is_upper):
    """
    Get fense based on three-sigma statistics.
        args - arguments from command line input
        mdf - platform metrics dataframe
        is_upper - True if upper fense is needed,
                   False if lower fense is needed
    """
    mean = mdf.mean()
    std = mdf.std()
    if args.verbose:
        print('mean: ', mean, ' std: ', std)
    if is_upper:
        return mean + args.thresh * std

    return mean - args.thresh * std


def get_fense(args, mdf, is_upper):
    """
    Get fense based on predefined fense type.
        args - arguments from command line input
        mdf - platform metrics dataframe
        is_upper - True if upper fense is needed,
                   False if lower fense is needed
    """
    fense = args.fense_type
    if fense == 'quartile':
        return get_quartile(args, mdf, is_upper)
    elif fense == 'normal':
        return get_normal(args, mdf, is_upper)
    elif fense == 'gmm-strict':
        gmm_fense = GmmFense(mdf.values.reshape(-1, 1), verbose=args.verbose)
        return gmm_fense.get_strict_fense(is_upper)
    elif fense == 'gmm-normal':
        gmm_fense = GmmFense(mdf.values.reshape(-1, 1), verbose=args.verbose)
        return gmm_fense.get_normal_fense(is_upper)
    else:
        print('unsupported fence type ', fense)


def partition_utilization(cpu_number, step=50):
    """
    Partition utilizaton bins based on requested CPU number and step count
        cpu_number - processor count assigned to workload
        step - bin range of one partition, default value is half processor
    """
    utilization_upper = (cpu_number + 1) * 100
    utilization_lower = cpu_number * 50

    utilization_bar = np.arange(utilization_lower, utilization_upper, step)

    return utilization_bar


def init_wl(args):
    """
    Initialize and return workload information from configuration file
        args - arguments from command line input
    """
    wl_df = pd.read_csv(args.workload_conf_file)
    workloadinfo = {}
    for row_turple in wl_df.iterrows():
        row = row_turple[1]
        workload_name = row['CNAME']
        workloadinfo[workload_name] = row['CPUS']
    return workloadinfo


def process_by_partition(args, workloadinfo):
    """
    Process single bin and generate anomaly threshold data
        args - arguments from command line input
        workloadinfo - workload information of LC workload
    """
    with open('./thresh.csv', 'w') as threshf:
        threshf.write('CID,CNAME,UTIL_START,UTIL_END,' +
                      'CPI_THRESH,MPKI_THRESH,MB_THRESH\n')

    with open('./tdp_thresh.csv', 'w') as tdpf:
        tdpf.write('CID,CNAME,UTIL,MEAN,STD,BAR\n')

    mdf = pd.read_csv(args.metric_file)
    cids = mdf['CID'].unique()

    for cid in cids:
        jdata = mdf[mdf['CID'] == cid]
        job = jdata['CNAME'].values[0]
        cpu_no = workloadinfo[job]

        # TODO: make step configurable
        utilization_partition = partition_utilization(cpu_no, 50)
        length = len(utilization_partition)

        utilization_threshold = cpu_no * 100 * 0.95
        tdp_data = jdata[jdata['UTIL'] >= utilization_threshold]

        util = tdp_data['UTIL']
        freq = tdp_data['NF']

        if not util.empty:
            mean, std = stats.norm.fit(freq)

            min_freq = min(freq)
            fbar = mean - 3 * std
            if min_freq < fbar:
                fbar = min_freq

            with open('./tdp_thresh.csv', 'a') as tdpf:
                tdpf.write(cid + ',' + job + ',' + str(utilization_threshold) +
                           ',' + str(mean) + ',' + str(std) +
                           ',' + str(fbar) + '\n')

        for index, util in enumerate(utilization_partition):
            lower_bound = util
            if index != length - 1:
                higher_bound = utilization_partition[index + 1]
            else:
                higher_bound = lower_bound + 50
            try:
                jdataf = jdata[(jdata['UTIL'] >= lower_bound) &
                               (jdata['UTIL'] <= higher_bound)]

                cpi = jdataf['CPI']
                cpi_thresh = get_fense(args, cpi, True)

                mpki = jdataf['L3MPKI']
                mpki_thresh = get_fense(args, mpki, True)

                memb = jdataf['MBL'] + jdataf['MBR']
                mb_thresh = get_fense(args, memb, False)
            except:
                continue

            print('Job: {job}, UTIL: [{util_lower}, {util_higher}],\
                  CPI Threshold: {cpi_thres}, MKPI Threshold: {mkpi_thres},\
                  MB Threshold: {mb_thresh}'.format(job=job,
                                                    util_lower=lower_bound,
                                                    util_higher=higher_bound,
                                                    cpi_thres=cpi_thresh,
                                                    mkpi_thres=mpki_thresh,
                                                    mb_thresh=mb_thresh))

            with open('./thresh.csv', 'a') as threshf:
                threshf.write(cid + ',' + job + ',' + str(lower_bound) + ',' +
                              str(higher_bound) + ',' + str(cpi_thresh) + ',' +
                              str(mpki_thresh) + ',' + str(mb_thresh) + '\n')


def process_lc_max():
    """ Record maximal CPU utilization of all LC workloads """
    udf = pd.read_csv('util.csv')
    lcu = udf[udf['CNAME'] == 'lcs']
    lcu = lcu['UTIL']
    maxulc = int(lcu.max())
    print('Maxmium LC utilization: ', maxulc)
    with open('./lcmax.txt', 'w') as lcmaxf:
        lcmaxf.write(str(maxulc) + '\n')


def process(args):
    """
    General procedure of analysis
        args - arguments from command line input
    """
    workloadinfo = init_wl(args)
    process_by_partition(args, workloadinfo)
    process_lc_max()


def main():
    """ Script entry point. """
    parser = argparse.ArgumentParser(description='This tool analyzes CPU\
                                     utilization and platform metrics\
                                     collected from eris agent and build data\
                                     model for contention detect and resource\
                                     regulation.')

    parser.add_argument('workload_conf_file', help='workload configuration\
                        file describes each task name, type, id, request cpu\
                        count', type=argparse.FileType('rt'), default='wl.csv')

    parser.add_argument('-v', '--verbose', help='increase output verbosity',
                        action='store_true')
    parser.add_argument('-t', '--thresh', help='threshold used in outlier\
                        detection', type=int, default=4)
    parser.add_argument('-f', '--fense-type', help='fense type used in outlier\
                        detection', choices=['quartile', 'normal',
                                             'gmm-strict', 'gmm-normal'],
                        default='gmm-strict')
    parser.add_argument('-m', '--metric-file', help='metrics file collected\
                        from eris agent', type=argparse.FileType('rt'),
                        default='metrics.csv')

    args = parser.parse_args()
    if args.verbose:
        print(args)

    process(args)

if __name__ == '__main__':
    main()
