#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""广告线路黑名单

有些源不是「假的播不出来」, 而是**能播、但内容是广告**:
播放列表正常、分片正常、码率正常, 只是画面是保健品广告 (常见「咨询热线 400-xxx-xxxx」
大红色字幕)。这类线路逃得过分片级验活, 只能靠画面审计确认后拉黑。

实测这些站会**间歇插播** —— 同一主机这次抓到真节目、下次抓到广告,
所以按主机整站拉黑, 不做「这条看着还行就留着」的豁免。

黑名单存 data/ad_blocklist.json:
    {"host":    {"74.91.26.218:82": {"reason": "...", "seen": "2026-10-21"}},
     "url":     {"http://...":      {"reason": "...", "seen": "..."}},
     "channel": {"大湾区卫视":        {"reason": "...", "seen": "..."}}}

三级优先: url > host > channel。channel 用于「整个台都拿不到真信号、所有线路都是
垫片广告」的情况 (实测大湾区卫视 13 条线路 100% 是广告, 一个真源都没有), 这种台
留着只会让用户每次点开都看到药品广告, 不如整台不发布。

新发现的主机用 scripts/audit.py 抽样看画面确认后再加, 不要凭猜测写。
"""
import json
import os
import urllib.parse


def _candidates(path):
    """按 cwd / 仓库根 依次找, 免得从 scripts/ 里调用时读不到"""
    yield path
    here = os.path.dirname(os.path.abspath(__file__))
    yield os.path.join(here, '..', 'data', 'ad_blocklist.json')


def load(path='data/ad_blocklist.json'):
    for p in _candidates(path):
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
            return ((d.get('host') or {}), (d.get('url') or {}),
                    (d.get('channel') or {}))
        except Exception:
            continue
    return {}, {}, {}


def should_block(url, host_map, url_map, name='', ch_map=None):
    """返回 (是否拉黑, 原因)"""
    if url in url_map:
        return True, url_map[url].get('reason') or 'url-blocklist'
    try:
        host = urllib.parse.urlparse(url).netloc
    except Exception:
        host = ''
    if host and host in host_map:
        return True, host_map[host].get('reason') or 'host-blocklist'
    if name and ch_map and name in ch_map:
        return True, ch_map[name].get('reason') or 'channel-blocklist'
    return False, ''


def apply(items, path='data/ad_blocklist.json', verbose=True):
    """就地剔除命中黑名单的线路, 返回被剔除的条数"""
    host_map, url_map, ch_map = load(path)
    if not host_map and not url_map and not ch_map:
        return 0
    keep, dropped = [], []
    for it in items:
        hit, reason = should_block(it.get('url') or '', host_map, url_map,
                                   it.get('name') or '', ch_map)
        if hit:
            dropped.append((it, reason))
        else:
            keep.append(it)
    if dropped and verbose:
        from collections import Counter
        c = Counter(reason[:24] for _, reason in dropped)
        print('  广告黑名单剔除 %d 条 (%s)'
              % (len(dropped), ', '.join('%s×%d' % kv for kv in c.most_common(4))),
              flush=True)
    items[:] = keep
    return len(dropped)
