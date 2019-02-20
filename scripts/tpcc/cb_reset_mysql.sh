#!/usr/bin/env bash

#/*******************************************************************************
# Copyright (c) 2012 IBM Corp.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#/*******************************************************************************

source ~/.bashrc
source $(echo $0 | sed -e "s/\(.*\/\)*.*/\1.\//g")/cb_common.sh

MYSQL_CONF_FILE=`get_my_ai_attribute_with_default mysql_conf_file /etc/mysql/mysql.conf.d/mysqld.cnf`
MYSQL_RAM_PERCENTAGE=`get_my_ai_attribute_with_default mysql_ram_percentage 70`

SUDO_CMD=`which sudo`

# Check if mysql has the right cache size or update it.
kb=$(cat /proc/meminfo  | sed -e "s/ \+/ /g" | grep MemTotal | cut -d " " -f 2) 
mb=$(echo "$kb / 1024 * ${MYSQL_RAM_PERCENTAGE} / 100" | bc)
if ! grep "innodb_buffer_pool_size = ${mb}M" ${MYSQL_CONF_FILE} >/dev/null; then
    syslog_netcat "Updating MySQL buffer pool size to ${mb}M..."
	${SUDO_CMD} su -c "sed -i 's/innodb_buffer_pool_size.*/innodb_buffer_pool_size = ${mb}M/' /etc/mysql/mysql.conf.d/mysqld.cnf"
	service mysql restart
fi
