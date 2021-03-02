#!/bin/bash

sudo varnishd -d -a :8090 -T localhost:6082 \
    -f `pwd`/cache-config.vcl -S /etc/varnish/secret -s malloc,256m
