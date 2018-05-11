#!/usr/bin/env bash

SUDOCMD=$(which sudo)
python -V 2>&1 | grep 2.7.*
if [[ $? -ne 0 ]]
then
    $SUDOCMD cat /etc/*release* | grep Ubuntu
    if [[ $? -eq 0 ]]
    then
        $SUDOCMD apt-get update; $SUDOCMD apt-get -y install python2.7; $SUDOCMD ln -s /usr/bin/python2.7 /usr/bin/python
    fi
fi

python -V 2>&1 | grep 2.7.*

if [[ $? -ne 0 ]]
then
    exit 1
else
    exit 0
fi