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

package main

// #include <stdint.h>
// #include <sys/types.h>
// #include <linux/perf_event.h>
// #cgo LDFLAGS: -lpqos -lm
// #include <pqos.h>
// #include "perf.c"
// #include "pgos.c"
// int LOG_VER_SUPER_VERBOSE = 2;
import "C"
import (
	"flag"
	"fmt"
	"os"
	"strings"
	"time"
	"unsafe"
)

var coreCount = flag.Int("core", 1, "core count")
var cycle = flag.Int("cycle", 1, "monitor time")
var frequency = flag.Int64("frequency", 5, "sample frequency")
var period = flag.Int64("period", 1, "sample period")
var cgroupPath = flag.String("cgroup", "", "cgroups to be monitored")

var metrics = []C.uint64_t{C.PERF_COUNT_HW_INSTRUCTIONS, C.PERF_COUNT_HW_CPU_CYCLES, C.PERF_COUNT_HW_CACHE_MISSES}
var metricsDescription = []string{"instructions", "cycles", "LLC misses"}

type Cgroup struct {
	Path        string
	Name        string
	Pid         uint32
	File        *os.File `json:"-"`
	PgosHandler C.int
}

func NewCgroup(path string) (*Cgroup, error) {
	cgroupFile, err := os.Open(path)
	if err != nil {
		println(path)
		return nil, err
	}
	cgroupNames := strings.Split(strings.Trim(path, string(os.PathSeparator)), string(os.PathSeparator))
	cgroupName := cgroupNames[len(cgroupNames)-1]
	return &Cgroup{
		Path: path,
		Name: cgroupName,
		File: cgroupFile,
	}, nil
}

func (this *Cgroup) GetPgosHandler() {
	f, err := os.OpenFile(this.Path+"/tasks", os.O_RDONLY, os.ModePerm)
	if err != nil {
		println(err.Error())
		return
	}
	defer f.Close()
	pids := []C.pid_t{}
	for {
		var pid uint16
		n, err := fmt.Fscanf(f, "%d\n", &pid)
		if n == 0 || err != nil {
			break
		}
		pids = append(pids, C.pid_t(pid))
	}
	this.PgosHandler = C.pgos_mon_start_pids(C.unsigned(len(pids)), (*C.pid_t)(unsafe.Pointer(&pids[0])))

	return
}

func (this *Cgroup) Close() error {
	err := this.File.Close()
	if err != nil {
		return err
	}
	return nil
}

func main() {
	pqosLog, err := os.OpenFile("/tmp/pqos.log", os.O_CREATE|os.O_WRONLY|os.O_TRUNC, os.ModePerm)
	if err != nil {
		println(err.Error())
		return
	}
	defer pqosLog.Close()

	config := C.pqos_config{
		fd_log:     C.int(pqosLog.Fd()),
		verbose:    C.LOG_VER_SUPER_VERBOSE,
		_interface: C.PQOS_INTER_OS,
	}
	C.pqos_init(&config)

	flag.Parse()
	cgroupsPath := strings.Split(*cgroupPath, ",")

	cgroups := make([]*Cgroup, 0, len(cgroupsPath))
	fds := make([]int32, 0, len(cgroupsPath))
	for i := 0; i < len(cgroupsPath); i++ {
		c, err := NewCgroup(cgroupsPath[i])
		if err != nil {
			println(err.Error())
			continue
		}
		c.GetPgosHandler()
		cgroups = append(cgroups, c)
		fds = append(fds, int32(c.File.Fd()))
	}

	frequencyDuration := fmt.Sprintf("%ds", *frequency-(*period))
	d, err := time.ParseDuration(frequencyDuration)
	if err != nil {
		println(err.Error())
		return
	}
	for i := 0; i < *cycle; i++ {
		result := make([]uint64, len(fds)*len(metrics))
		now := time.Now().Unix()
		C.collect((*C.pid_t)(unsafe.Pointer((&fds[0]))), C.int(len(fds)), C.int(*coreCount), &metrics[0], C.int(len(metrics)), (*C.uint64_t)(unsafe.Pointer(&result[0])), C.unsigned(*period))
		for j := 0; j < len(cgroups); j++ {
			for k := 0; k < len(metrics); k++ {
				fmt.Printf("%s\t%s\t%d\t%d\n", cgroups[j].Name, metricsDescription[k], now, result[j*len(metrics)+k])
			}
			pgosValue := C.pgos_mon_poll(cgroups[j].PgosHandler)
			fmt.Printf("%s\t%s\t%d\t%+v\n", cgroups[j].Name, "LLC occupancy", now, pgosValue.llc/1024)
			fmt.Printf("%s\t%s\t%d\t%+v\n", cgroups[j].Name, "Memory bandwidth local", now, float64(pgosValue.mbm_local_delta)/1024.0/1024.0/float64(*period))
			fmt.Printf("%s\t%s\t%d\t%+v\n", cgroups[j].Name, "Memory bandwidth remote", now, float64(pgosValue.mbm_remote_delta)/1024.0/1024.0/float64(*period))
		}
		time.Sleep(d)
	}
	C.pgos_mon_stop()
	C.pqos_fini()
	return
}
