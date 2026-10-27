#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一个频道**全部**线路的画面拼成一张联系表 (不像 audit.py 按主机去重)

为什么需要它: audit.py 为了"同一主机只留一条"会按 netloc 去重, 遇到「整个台被
顶成广告、所有线路都指向同几个主机」时只会抽 3 条, 看着像"只有 3 条是广告"。
实测大湾区卫视 13 条线路分属 3 个主机, 去重后只抽 3 条, 而实际 7 条能解码的
线路**全是**广告 —— 必须逐条看才知道整台没救。

用法:
    python chansheet.py out/live.m3u 大湾区卫视 verify/大湾区.png
    python chansheet.py out/live.m3u 青海卫视,甘肃卫视 verify/  # 多频道
    python chansheet.py out/live.m3u 大湾区卫视 out.png --jobs 12

依赖: pip install av pillow  (CI 主流程不装, 这是人工审计工具)

输出: 一张 4 列网格图, 每格标 #序号 / 分辨率 / URL; 终端同时打印序号对照表。
判定要点: 同一频道里出现「保健品/药品大字 + 400 电话 + 立即拨打」的格子就是广告。
"""
import argparse
import io
import os
import sys

import av
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import adcheck as ac  # noqa: E402  复用它的取分片 / 拉字节
import m3uio  # noqa: E402

TILE = 320
COLS = 4


def grab(item, timeout):
    """抓一条线路的首帧, 返回 (item, PIL.Image|None, 错误名|None)"""
    try:
        seg = ac._last_segment(item['url'], item.get('headers') or {}, timeout)
        data, _ = ac._get(seg, item.get('headers') or {}, timeout,
                          600000, 'bytes=0-599999')
        if data[:1] == b'\x47':                      # 对齐到 TS 包边界
            for i in range(0, min(len(data), 4000) - 187, 188):
                if data[i] == 0x47:
                    data = data[i:]
                    break
        fmt = 'mpegts' if data[:1] == b'\x47' else None
        with av.open(io.BytesIO(data), format=fmt) as c:
            for f in c.decode(video=0):
                return item, f.to_image(), None
        return item, None, 'noframe'
    except Exception as e:
        return item, None, type(e).__name__


def sheet_for(channel, items, out, jobs=10, timeout=10):
    from concurrent.futures import ThreadPoolExecutor
    res = list(ThreadPoolExecutor(jobs).map(
        lambda it: grab(it, timeout), items))
    rows = (len(res) + COLS - 1) // COLS
    img_all = Image.new('RGB', (COLS * TILE, rows * TILE), (24, 24, 24))
    d = ImageDraw.Draw(img_all)
    for i, (it, img, err) in enumerate(res):
        x, y = (i % COLS) * TILE, (i // COLS) * TILE
        if img is not None:
            img = img.convert('RGB')
            img.thumbnail((TILE - 4, TILE - 24))
            img_all.paste(img, (x + 2, y + 20))
            d.text((x + 4, y + 4), '#%d %dx%d' % (i + 1, *img.size),
                   fill=(0, 255, 0))
        else:
            d.text((x + 4, y + 4), '#%d %s' % (i + 1, err), fill=(255, 80, 80))
        d.text((x + 4, y + TILE - 12), it['url'][:46], fill=(200, 200, 200))
    img_all.save(out)
    print('  %s: %d 条 -> %s' % (channel, len(items), out), flush=True)
    for i, (it, img, err) in enumerate(res):
        print('    #%-2d %-52s %s' % (i + 1, it['url'][:52],
                                      ('%dx%d' % img.size) if img else err),
              flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('m3u')
    ap.add_argument('channels', help='频道名, 多个用逗号分隔')
    ap.add_argument('out', help='输出 png 路径; 多频道时当作目录')
    ap.add_argument('--jobs', type=int, default=10)
    ap.add_argument('--timeout', type=int, default=10)
    args = ap.parse_args(argv)

    with open(args.m3u, encoding='utf-8') as f:
        items = m3uio.parse_m3u(f.read())

    names = [x.strip() for x in args.channels.split(',') if x.strip()]
    multi = len(names) > 1
    if multi:
        os.makedirs(args.out, exist_ok=True)

    for i, name in enumerate(names):
        sub = [it for it in items if it.get('name') == name]
        if not sub:
            print('  跳过 %s: 订阅里没有这个频道' % name, flush=True)
            continue
        path = (os.path.join(args.out, '%s.png' % name) if multi
                else args.out)
        sheet_for(name, sub, path, args.jobs, args.timeout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
