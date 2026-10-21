#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
线路验活的历史记录

两件事需要跨次运行记忆:
  1. 稳定性排序 —— 连续多次验活都通过的线路, 排在同清晰度的前面
     (同样的 1080p, 一条连挂三次和一条次次都活, 不该等价)
  2. 主机熔断 —— 某个主机连续失败时提前短路, 不再把整批候选耗在它身上

只做"排序依据"和"熔断依据", **不用来跳过验活**。
跳过验活就等于把陈旧结论当新结论发出去, 那是空壳线路回流的根源。
"""
import json
import os

MAX_ENTRIES = 20000


def load(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save(path, st):
    """只留还活着的, 失败记录保留但限制总量"""
    items = sorted(st.items(), key=lambda kv: -int(kv[1].get('ok') or 0))
    items = items[:MAX_ENTRIES]
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(dict(items), f, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, path)


def mark(st, url, ok, ts=''):
    """记录一次验活结果, 返回稳定度 (连续成功次数)"""
    rec = st.setdefault(url, {'ok': 0, 'fail': 0})
    if ok:
        rec['ok'] = int(rec.get('ok') or 0) + 1
        rec['lastOk'] = ts
    else:
        rec['ok'] = 0            # 断一次就重新计数
        rec['fail'] = int(rec.get('fail') or 0) + 1
        rec['lastFail'] = ts
    return rec['ok']


def steady(st, url):
    try:
        return int((st.get(url) or {}).get('ok') or 0)
    except Exception:
        return 0
