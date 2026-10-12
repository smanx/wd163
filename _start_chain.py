#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""容器内启动整个游戏服务链（按依赖顺序），等 mysqld 就绪后再起游戏组件"""
import urllib2
import json
import time
import sys

BASE = 'http://127.0.0.1:8080'


def post(path, name):
    req = urllib2.Request(BASE + path, data=json.dumps({'name': name}),
                          headers={'Content-Type': 'application/json'})
    return json.loads(urllib2.urlopen(req, timeout=30).read().decode('utf-8'))


def get(path):
    return json.loads(urllib2.urlopen(BASE + path, timeout=30).read().decode('utf-8'))


order = ['mysqld', 'driver', 'aaa', 'dba', 'ccs', 'csa', 'gs']
for name in order:
    r = post('/api/start', name)
    print(name.ljust(8), '->', r.get('message', '?'))
    if not r.get('ok') and u'\u4f9d\u8d56' in r.get('message', ''):
        # 依赖未满足，等 3 秒重试一次
        time.sleep(3)
        r = post('/api/start', name)
        print(name.ljust(8), '-> retry:', r.get('message', '?'))
    if name == 'mysqld' and r.get('ok'):
        print('waiting for mysqld to accept connections...')
        time.sleep(5)
    time.sleep(2)

time.sleep(4)
st = get('/api/status')
print('===== status =====')
for s in st['services']:
    print(s['name'].ljust(8), 'RUNNING' if s['running'] else 'STOPPED')
