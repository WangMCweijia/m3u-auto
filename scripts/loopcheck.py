#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判定一条 HLS 线路是否在循环播放同一段内容 (广告垫片的典型特征)

做法: 每隔 12s 取一次当前最后一个分片, 记录 md5。
真实直播内容几乎不会重复; 循环垫片在若干分钟内必然出现重复 md5。
"""
import hashlib
import re
import sys
import time
import urllib.parse
import urllib.request

UA = {'User-Agent': 'okhttp/3.12.0'}


def fetch(url, n=200000):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10)
    return r.read(n), r.geturl()


def last_segment(playlist):
    body, final = fetch(playlist)
    txt = body.decode('utf-8', 'ignore')
    depth = 0
    while '#EXT-X-STREAM-INF' in txt and depth < 2:
        v = [x for x in txt.splitlines() if x and not x.startswith('#')][0]
        body, final = fetch(urllib.parse.urljoin(final, v))
        txt = body.decode('utf-8', 'ignore')
        depth += 1
    segs = [x for x in txt.splitlines() if x and not x.startswith('#')]
    if not segs:
        return None, None
    return urllib.parse.urljoin(final, segs[-1]), txt


def main():
    url = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    gap = float(sys.argv[3]) if len(sys.argv) > 3 else 12
    seen = {}
    for i in range(rounds):
        try:
            seg, _ = last_segment(url)
            if not seg:
                print('[%02d] 无分片' % i, flush=True)
            else:
                seg, _ = last_segment(url) if False else (seg, None)
                body, _ = fetch(seg, 400000)
                h = hashlib.md5(body).hexdigest()[:10]
                tag = '重复!' if h in seen else ''
                print('[%02d] %-10s %7d bytes %s %s'
                      % (i, h, len(body), seg.rsplit('/', 1)[-1], tag), flush=True)
                if h in seen:
                    print('  >>> 与第 %d 轮内容完全相同, 判定为循环播放' % seen[h], flush=True)
                    return
                seen[h] = i
        except Exception as e:
            print('[%02d] ERR %s' % (i, str(e)[:50]), flush=True)
        if i < rounds - 1:
            time.sleep(gap)
    print('未发现重复, 更接近真实直播')


if __name__ == '__main__':
    main()
