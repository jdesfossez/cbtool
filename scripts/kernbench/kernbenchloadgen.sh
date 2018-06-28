#!/usr/bin/env bash

dir=$1
NRJOBS=$2

cd ${dir}/linux
${dir}/ltp/utils/benchmark/kernbench-0.42/kernbench -n 1 -H -M $NRJOBS
mv kernbench.log ${dir}
