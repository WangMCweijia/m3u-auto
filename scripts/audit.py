#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""线路内容审计: 把一个频道每条线路的画面拼成一张联系表, 一眼看出哪些是广告

用法:
    python audit.py <m3u 文件> <频道名> <输出 png> [最多几条]

同一频道常常混着真直播和广告垫片。逐条点开太慢, 这里每条线路抓一帧,
缩略后拼成网格并标注序号, 配合打印出来的序号->地址对照表即可判定。
"""
import io
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import av
from PIL import Image, ImageDraw

UA = {'User-Agent': 'okhttp/3.12.0'}
TILE = 320


def fetch(url, n=6_000_000):
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
    if not segs:
        raise RuntimeError('no-segment')
    return urllib.parse.urljoin(final, segs[-1])


def first_frame(url):
    seg = last_segment(url)
    data, _ = fetch(seg)
    if data[:1] == b'\x47':
        for i in range(0, len(data) - 187, 188):
            if data[i] == 0x47:
                data = data[i:]
                break
    with av.open(io.BytesIO(data), format='mpegts') as c:
        for f in c.decode(video=0):
            return f.to_image(), seg
    raise RuntimeError('no-frame')


def main():
    m3u, chan, out = sys.argv[1], sys.argv[2], sys.argv[3]
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 12

    lines = open(m3u, encoding='utf-8').read().splitlines()
    urls = []
    for i, l in enumerate(lines):
        if l.startswith('#EXTINF') and l.rsplit(',', 1)[-1].strip() == chan:
            urls.append(lines[i + 1].strip())
    if not urls:
        print('没找到频道:', chan)
        return 1

    # 同主机只留一条, 避免联系表被同一后端占满
    picked, hosts = [], set()
    for u in urls:
        h = urllib.parse.urlparse(u).netloc
        if h in hosts:
            continue
        hosts.add(h)
        picked.append(u)
        if len(picked) >= limit:
            break
    print('%s: 共 %d 条线路, 取 %d 个不同主机' % (chan, len(urls), len(picked)))

    def work(u):
        try:
            img, seg = first_frame(u)
            return u, img, seg, None
        except Exception as e:
            return u, None, None, type(e).__name__

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(work, picked))

    cols = 4
    rows = (len(res) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * TILE, rows * TILE), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for idx, (u, img, seg, err) in enumerate(res):
        x, y = (idx % cols) * TILE, (idx // cols) * TILE
        if img is not None:
            img = img.convert('RGB')
            img.thumbnail((TILE - 4, TILE - 24))
            sheet.paste(img, (x + 2, y + 20))
            d.text((x + 4, y + 4), '#%d %dx%d' % (idx + 1, *img.size), fill=(0, 255, 0))
        else:
            d.text((x + 4, y + 4), '#%d %s' % (idx + 1, err), fill=(255, 80, 80))
        d.text((x + 4, y + TILE - 12), u[:44], fill=(200, 200, 200))
    sheet.save(out)
    print('联系表:', out)
    for idx, (u, img, seg, err) in enumerate(res):
        print('  #%-2d %-58s %s' % (idx + 1, u[:58],
                                    ('%dx%d' % img.size) if img else err))
    return 0


if __name__ == '__main__':
    sys.exit(main())
