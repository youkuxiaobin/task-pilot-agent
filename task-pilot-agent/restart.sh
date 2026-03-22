#!/bin/bash

pid=`ps -e -o pid,comm,args | grep taskpilotagent | grep -v grep | awk '{print $1}'`
if [ -n "$pid" ] 
then

	kill -9 $pid
fi

nohup  uv run main.py 2>&1 &
