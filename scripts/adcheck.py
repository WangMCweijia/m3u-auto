#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""广告线路候选识别: 同一频道内按画面聚类, 把「内容对不上」的线路揪出来

注意: 这里只产出**候选**, 不等于判定。网关的不同后端会播相差几十秒的画面,
聚类会把它们误判成广告; 反向也可能漏。确认必须靠 scripts/audit.py 看联系表。
确认后的主机写进 data/ad_blocklist.json 才会真正生效。

原理
----
广告农场不是按主机整站替换的 —— 同一个 IP 对热播频道塞广告、对其它频道照常转发,
所以按主机拉黑一定会误伤。真正稳定的判据是**画面**:

    同一个频道的多条线路, 在同一时刻应该播同一档节目。
    少数线路画面和大多数完全对不上 -> 那几条就是垫片广告。

做法
----
1. 每个频道取所有线路, 各自用 Range 只拉分片前 400KB (够解出首帧)
2. 首帧缩到 9x8 灰度, 算 dHash (64 bit 感知哈希)
3. 按汉明距离聚类 (<=12 视为同画面), 最大簇视为真直播
4. 落在其它簇里的线路 -> 写入 data/ad_blocklist.json

只有 >=2 条线路的频道才能判定; 单线路频道原样保留。

用法:
    python adcheck.py --m3u out/live.m3u --out data/ad_blocklist.json
    python adcheck.py --m3u out/live.m3u --only priority --jobs 8
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import av
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import channelmeta as cm  # noqa: E402

UA = 'okhttp/3.12.0'
SNIFF = 400 * 1024          # 只拉这么多就够解首帧
HASH_BITS = 64
SAME = 12                   # 汉明距离阈值


def _get(url, headers, timeout, max_bytes, rng=None):
    h = dict(headers or {})
    h.setdefault('User-Agent', UA)
    if rng:
        h['Range'] = rng
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes), (r.geturl() or url)


def _last_segment(url, headers, timeout):
    body, final = _get(url, headers, timeout, 200000)
    txt = body.decode('utf-8', 'ignore')
    depth = 0
    while '#EXT-X-STREAM-INF' in txt and depth < 2:
        vs = [x for x in txt.splitlines() if x and not x.startswith('#')]
        if not vs:
            break
        body, final = _get(urllib.parse.urljoin(final, vs[0]), headers,
                           timeout, 200000)
        txt = body.decode('utf-8', 'ignore')
        depth += 1
    segs = [x for x in txt.splitlines() if x and not x.startswith('#')]
    if not segs:
        raise RuntimeError('no-segment')
    return urllib.parse.urljoin(final, segs[-1])


def dhash(img):
    """64 bit 感知哈希"""
    g = img.convert('L').resize((9, 8), Image.BILINEAR)
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left = px[row * 9 + col]
            right = px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a, b):
    return bin(a ^ b).count('1')


def frame_hash(url, headers, timeout):
    seg = _last_segment(url, headers, timeout)
    data, _ = _get(seg, headers, timeout, SNIFF,
                   'bytes=0-%d' % (SNIFF - 1))
    if data[:1] == b'\x47':
        for i in range(0, len(data) - 187, 188):
            if data[i] == 0x47:
                data = data[i:]
                break
    with av.open(io.BytesIO(data), format='mpegts') as c:
        for f in c.decode(video=0):
            return dhash(f.to_image())
    raise RuntimeError('no-frame')


def parse_channels(path):
    """{频道名: [(url, headers), ...]} —— 与 m3uio 的归并口径一致"""
    chans, cur = {}, None
    lines = open(path, encoding='utf-8').read().splitlines()
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith('#EXTINF'):
            cur = l.rsplit(',', 1)[-1].strip()
            cur = re.sub(r'\s*#\d+$', '', cur)
        elif l.strip() and not l.startswith('#') and cur:
            chans.setdefault(cur, []).append(l.strip())
        i += 1
    return chans


def check_channel(name, urls, jobs, timeout):
    hdr = {}
    def work(u):
        try:
            return u, frame_hash(u, hdr, timeout), None
        except Exception as e:
            return u, None, type(e).__name__

    with ThreadPoolExecutor(min(jobs, max(1, len(urls)))) as ex:
        res = list(ex.map(work, urls))
    ok = [(u, h) for u, h, e in res if h is not None]
    if len(ok) < 2:
        return []

    # 以每条线路为种子, 找它的近邻, 取最大簇
    best = []
    for u, h in ok:
        grp = [(u2, h2) for u2, h2 in ok if hamming(h, h2) <= SAME]
        if len(grp) > len(best):
            best = grp
    keep = {u for u, _ in best}
    if len(keep) == len(ok):
        return []
    # 只有一个簇能站住脚时才敢判 —— 否则说明画面本来就分散, 不下结论
    if len(keep) < 2:
        return []
    return [(u, h) for u, h in ok if u not in keep]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--m3u', default='out/live.m3u')
    ap.add_argument('--out', default='data/ad_candidates.json')
    ap.add_argument('--only', default='all', choices=('all', 'priority'))
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--timeout', type=int, default=10)
    ap.add_argument('--max-channels', type=int, default=0)
    args = ap.parse_args()

    chans = parse_channels(args.m3u)
    if args.only == 'priority':
        pick = {}
        for nm, us in chans.items():
            tid = cm.id_from_name(nm) or ''
            if tid.startswith(('cctv', 'cgtn')) or nm.endswith('卫视'):
                pick[nm] = us
        chans = pick
    if args.max_channels:
        chans = dict(list(chans.items())[:args.max_channels])

    print('待判定频道 %d 个' % len(chans), flush=True)
    found = {}
    done = 0
    for name, urls in chans.items():
        done += 1
        try:
            bad = check_channel(name, urls, args.jobs, args.timeout)
        except Exception as e:
            print('  [%d/%d] %s 判定失败 %s' % (done, len(chans), name,
                                               type(e).__name__), flush=True)
            continue
        if bad:
            print('  [%d/%d] %s -> %d 条疑似广告' % (done, len(chans), name,
                                                     len(bad)), flush=True)
            for u, _ in bad:
                found[u] = dict(channel=name, seen=time.strftime('%Y-%m-%d'))

    # 合并已有黑名单 (跨轮次累积, 只增不减)
    old = {}
    if os.path.exists(args.out):
        try:
            old = json.load(open(args.out, encoding='utf-8'))
        except Exception:
            old = {}
    merged = dict(old)
    for u, v in found.items():
        prev = merged.get(u) or {}
        merged[u] = dict(channel=v['channel'],
                         seen=prev.get('seen') or v['seen'],
                         lastSeen=v['seen'],
                         hits=int(prev.get('hits') or 0) + 1)

    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=1, sort_keys=True)
    print('本次新增 %d 条, 黑名单累计 %d 条 -> %s'
          % (len(found), len(merged), args.out), flush=True)
    for u, v in list(found.items())[:10]:
        print('  %-60s %s' % (u[:60], v['channel']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
