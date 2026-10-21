#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓一帧画面, 用来人工确认线路播的到底是节目还是广告

用法: python grab.py <playlist-url> <out.png> [第几秒]
"""
import sys
import urllib.parse
import urllib.request

import av

UA = {'User-Agent': 'okhttp/3.12.0'}


def fetch(url, n=4_000_000):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15)
    return r.read(n), r.geturl()


def last_segment(playlist):
    body, final = fetch(playlist, 200000)
    txt = body.decode('utf-8', 'ignore')
    depth = 0
    while '#EXT-X-STREAM-INF' in txt and depth < 2:
        v = [x for x in txt.splitlines() if x and not x.startswith('#')][0]
        body, final = fetch(urllib.parse.urljoin(final, v), 200000)
        txt = body.decode('utf-8', 'ignore')
        depth += 1
    segs = [x for x in txt.splitlines() if x and not x.startswith('#')]
    return urllib.parse.urljoin(final, segs[-1])


def main():
    url, out = sys.argv[1], sys.argv[2]
    seg = last_segment(url)
    print('分片:', seg[:110])
    data, _ = fetch(seg, 6_000_000)
    print('大小: %.2f MB' % (len(data) / 1048576))
    if data[:1] == b'\x47':
        for i in range(0, len(data) - 187, 188):
            if data[i] == 0x47:
                data = data[i:]
                break
    with av.open(__import__('io').BytesIO(data), format='mpegts') as c:
        for frame in c.decode(video=0):
            frame.to_image().save(out)
            print('已保存:', out, frame.width, 'x', frame.height)
            return
    print('没解出视频帧')


if __name__ == '__main__':
    main()
