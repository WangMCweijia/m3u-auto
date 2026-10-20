#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成订阅索引页 index.html + 机器可读的 index.json"""
import argparse
import json
import os
import sys


TPL = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IPTV 订阅 · 自动更新</title>
<style>
:root{{--bg:#0d1117;--card:#161b22;--bd:#30363d;--fg:#e6edf3;--dim:#8b949e;--ac:#2f81f7;--ok:#3fb950}}
*{{box-sizing:border-box}}
body{{margin:0;padding:32px 20px;background:var(--bg);color:var(--fg);
font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:860px;margin:0 auto}}
h1{{font-size:24px;margin:0 0 6px}}
.sub{{color:var(--dim);margin-bottom:28px;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:12px;margin-bottom:28px}}
.stat{{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:14px}}
.stat b{{display:block;font-size:22px;color:var(--ok)}}
.stat span{{color:var(--dim);font-size:12px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:18px;margin-bottom:16px}}
.card h2{{font-size:15px;margin:0 0 10px}}
code{{display:block;background:#0d1117;border:1px solid var(--bd);border-radius:6px;
padding:10px 12px;font:13px/1.5 ui-monospace,Consolas,monospace;
color:var(--ac);word-break:break-all;margin:6px 0}}
.note{{color:var(--dim);font-size:12px;margin-top:8px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--bd)}}
th{{color:var(--dim);font-weight:500;font-size:12px}}
</style>
</head>
<body><div class="wrap">
<h1>IPTV 订阅</h1>
<div class="sub">自动构建 · 最近更新 {updated} · 频道表版本 {ver}</div>

<div class="grid">
  <div class="stat"><b>{channels}</b><span>可用频道</span></div>
  <div class="stat"><b>{lines}</b><span>可用线路</span></div>
  <div class="stat"><b>{groups}</b><span>分组</span></div>
</div>

<div class="card">
<h2>订阅地址 · M3U（推荐）</h2>
<code id="m3u">{m3u}</code>
<div class="note">适用于：TVBox / 影视仓 / Kodi / TiviMate / Televizo / 电视家 等。</div>
</div>

<div class="card">
<h2>订阅地址 · TVLIST（91kds 系原生格式）</h2>
<code id="txt">{txt}</code>
<div class="note">格式：<code style="display:inline;padding:1px 5px">分类|频道名,url</code></div>
</div>

<div class="card">
<h2>分组构成</h2>
<table><tr><th>分组</th><th>线路数</th></tr>{rows}</table>
</div>

<div class="card">
<h2>说明</h2>
<div class="note">
直播源由公开频道表自动抓取并逐条实测存活，每 6 小时重建一次。<br>
线路会因源站调整而失效，若某频道失效请稍后刷新订阅；多线路频道已按同名编号保留备选。<br>
本页仅索引公开可得的流地址，不托管、不转码、不分发任何音视频内容。
</div>
</div>
</div></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='out')
    ap.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', ''))
    ap.add_argument('--branch', default='main')
    args = ap.parse_args()

    stats_path = os.path.join(args.out, 'stats.json')
    stats = {}
    if os.path.exists(stats_path):
        stats = json.load(open(stats_path, encoding='utf-8'))
    meta = stats.get('meta', {})
    by_group = stats.get('byGroup', {})

    repo = args.repo or 'USER/REPO'
    jsd = 'https://cdn.jsdelivr.net/gh/%s@%s' % (repo, args.branch)
    m3u = '%s/out/live.m3u' % jsd
    txt = '%s/out/live.txt' % jsd

    rows = '\n'.join(
        '<tr><td>%s</td><td>%d</td></tr>' % (g, n)
        for g, n in sorted(by_group.items(), key=lambda x: -x[1]))

    import datetime
    now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

    html = TPL.format(
        updated=now,
        ver=meta.get('sourceVersion', '-'),
        channels=stats.get('totalChannels', 0),
        lines=stats.get('totalLines', 0),
        groups=len(by_group),
        m3u=m3u, txt=txt, rows=rows)

    p = os.path.join(args.out, 'index.html')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(html)
    print('写出', p)

    idx = dict(
        updated=now,
        sourceVersion=meta.get('sourceVersion'),
        channels=stats.get('totalChannels', 0),
        lines=stats.get('totalLines', 0),
        byGroup=by_group,
        urls=dict(m3u=m3u, tvlist=txt,
                  rawM3u='https://raw.githubusercontent.com/%s/%s/out/live.m3u'
                         % (repo, args.branch)),
    )
    with open(os.path.join(args.out, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    print('写出 index.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
