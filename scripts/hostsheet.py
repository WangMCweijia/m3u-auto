#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按主机抽样出联系表: 一屏看完哪些站是广告

逐条线路点开太慢, 按主机各取一条, 拼成网格即可快速分类。
配合 audit.py (按频道) 使用: 这个看「哪个站有问题」, 那个看「这个台哪条线路有问题」。

用法:
    python hostsheet.py out/live.m3u sheet.png [起始序号] [数量]
"""
import collections
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw

sys.path.insert(0, __file__.rsplit('\\', 1)[0].rsplit('/', 1)[0])
import audit as A  # noqa: E402

TILE = 300


def main():
    m3u, out = sys.argv[1], sys.argv[2]
    start = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    count = int(sys.argv[4]) if len(sys.argv) > 4 else 16

    urls = [l.strip() for l in open(m3u, encoding='utf-8')
            if l.strip() and not l.startswith('#')]
    hosts = collections.OrderedDict()
    for u in urls:
        hosts.setdefault(urllib.parse.urlparse(u).netloc, []).append(u)
    pick = [(h, us[0], len(us)) for h, us in hosts.items() if len(us) >= 2]
    pick = pick[start:start + count]
    print('共 %d 个主机, 本次看第 %d-%d 个'
          % (len(hosts), start + 1, start + len(pick)))

    def work(t):
        h, u, n = t
        try:
            img, _ = A.first_frame(u)
            return h, u, n, img, None
        except Exception as e:
            return h, u, n, None, type(e).__name__

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(work, pick))

    cols = 4
    rows = (len(res) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * TILE, rows * TILE), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    for i, (h, u, n, img, err) in enumerate(res):
        x, y = (i % cols) * TILE, (i // cols) * TILE
        if img is not None:
            im = img.convert('RGB')
            im.thumbnail((TILE - 4, TILE - 26))
            sheet.paste(im, (x + 2, y + 22))
        d.text((x + 4, y + 4), '%d %s (%d)' % (i + 1, h[:24], n),
               fill=(0, 255, 120) if img else (255, 90, 90))
    sheet.save(out)
    print('联系表:', out)
    for i, (h, u, n, img, err) in enumerate(res):
        print('  #%-2d %-24s %2d条 %s' % (i + 1, h, n, 'ok' if img else err))


if __name__ == '__main__':
    main()
