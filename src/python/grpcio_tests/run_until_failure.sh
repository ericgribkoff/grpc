#!/bin/bash
i=0
echo $@
eval $@
while [ $? -eq 0 ]; do
  echo $i
  i=$((i+1))
  eval $@
done
echo $i
