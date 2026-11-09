# -*- coding: utf-8 -*-
"""按 URL 逐条多次抽帧, 拼成时序联系表。

广告源是轮播的, 单帧抽样有漏 —— 所以每条线路隔几秒连抽 N 次,
N 帧并排放, 同一格能看到"这次是节目 / 下次是广告"的切换。

用法:
    python scripts/probeurls.py _now.m3u "CCTV-6" verify/out.png --rounds 3 --gap 6

    # 或直接给 url 列表
    python scripts/probeurls.py urls.txt verify/out.png

输出每格左上角标 `#序 主机`, 方便定位到具体线路。
"""
import sys
import os
import io
import re
import json
import time
import argparse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m3uio
import adcheck as ac

TILE = 300


def collect(args):
    """返回 [(label, url)]"""
    src = args.source
    if os.path.exists(src) and src.lower().endswith(('.m3u', '.m3u8', '.txt')):
        raw = open(src, encoding='utf-8', errors='ignore').read()
        if '#EXTINF' in raw:
            items = m3uio.parse_m3u(raw)
        else:
            items = [{'name': os.path.basename(u.strip()), 'url': u.strip()}
                     for u in raw.splitlines() if u.strip().startswith('http')]
        if args.match:
            items = [i for i in items if args.match in i['name']]
    else:
        items = [{'name': os.path.basename(u.strip()), 'url': u.strip()}
                 for u in src.splitlines() if u.strip().startswith('http')]

    out = []
    for i, it in enumerate(items, 1):
        m = re.match(r'https?://([^/]+)', it['url'])
        out.append(('#%d %s' % (i, (m.group(1) if m else '?')), it['url']))
    return out


def one_frame(url, timeout):
    """取当前位置最后一个分片的首帧, 返回 PIL Image 或 None"""
    import av
    seg = ac._last_segment(url, {}, timeout)
    if not seg:
        return None, 'noseg'
    data, _ = ac._get(seg, {}, timeout, 600000, 'bytes=0-599999')
    if not data:
        return None, 'nosegdata'
    if data[:1] == b'\x47':
        for i in range(0, min(len(data), 4000) - 187, 188):
            if data[i] == 0x47:
                data = data[i:]
                break
    fmt = 'mpegts' if data[:1] == b'\x47' else None
    with av.open(io.BytesIO(data), format=fmt) as c:
        for f in c.decode(video=0):
            return f.to_image(), None
    return None, 'noframe'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source')
    ap.add_argument('out')
    ap.add_argument('--match', default=None, help='按频道名过滤')
    ap.add_argument('--rounds', type=int, default=3)
    ap.add_argument('--gap', type=float, default=6.0, help='轮次间隔秒')
    ap.add_argument('--timeout', type=int, default=10)
    args = ap.parse_args()

    targets = collect(args)
    if not targets:
        print('没有匹配到线路')
        return
    print('%d 条线路 × %d 轮, 间隔 %.0fs' % (len(targets), args.rounds, args.gap), flush=True)

    # 每条线路开一个线程跑完所有轮次, 保证同一线路的帧在时间上连续
    def run(pair):
        label, url = pair
        frames = []
        for r in range(args.rounds):
            try:
                img, err = one_frame(url, args.timeout)
            except Exception as e:
                img, err = None, type(e).__name__
            frames.append((img, err))
            if r != args.rounds - 1:
                time.sleep(args.gap)
        return label, url, frames

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(run, targets))

    from PIL import Image, ImageDraw
    cols = args.rounds
    rows = len(res)
    sheet = Image.new('RGB', (cols * TILE, rows * TILE), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    for r, (label, url, frames) in enumerate(res):
        for c, (img, err) in enumerate(frames):
            x, y = c * TILE, r * TILE
            if img is not None:
                im = img.convert('RGB')
                im.thumbnail((TILE - 4, TILE - 34))
                sheet.paste(im, (x + 2, y + 18))
                d.text((x + 4, y + 4), '%s T%d %dx%d' % (label, c + 1, *im.size),
                       fill=(120, 255, 120))
            else:
                d.text((x + 4, y + 4), '%s T%d %s' % (label, c + 1, err), fill=(255, 120, 120))
            d.text((x + 4, y + TILE - 14), url[:44], fill=(170, 170, 170))
    sheet.save(args.out)
    print('拼图 ->', args.out)
    for label, url, frames in res:
        print('  %-24s %-46s %s' % (
            label, url[:46],
            ' '.join(('ok' if img else err) for img, err in frames)))
    json.dump([{'label': l, 'url': u, 'frames': [bool(i) for i, _ in f]}
               for l, u, f in res],
              open(args.out + '.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
