#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3U 读写与频道归并

归并规则 (与主流播放器一致):
    同一个 tvg-id  => 同一个电视台 => 该台的多条线路
    显示名必须完全相同, 否则会被当成另一个台

所以写出时:
  * 每条 EXTINF 都带 tvg-id / tvg-name
  * 同一 tvg-id 的线路连续排列
  * 绝不使用 " #2" 这类后缀 (那会把线路变成新频道)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import channelmeta as cm  # noqa: E402


def parse_m3u(text):
    """返回 [{tvg_id, name, group, logo, url, ename}]"""
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith('#EXTINF'):
            i += 1
            continue
        g = re.search(r'group-title="([^"]*)"', line)
        group = g.group(1) if g else ''
        lg = re.search(r'tvg-logo="([^"]*)"', line)
        logo = lg.group(1) if lg else ''
        tid = re.search(r'tvg-id="([^"]*)"', line)
        tvg_id_raw = tid.group(1) if tid else ''
        # 从最后一个逗号切: tvg-logo 的 URL 里含逗号时, 用第一个逗号会切坏名字
        name = line.rsplit(',', 1)[-1].strip() if ',' in line else ''
        # 去掉历史遗留的 " #2" 后缀
        name = re.sub(r'\s*#\d+$', '', name).strip()
        j = i + 1
        while j < len(lines) and (not lines[j].strip()
                                  or lines[j].lstrip().startswith('#')):
            j += 1
        url = lines[j].strip() if j < len(lines) else ''
        if url.startswith(('http://', 'https://')):
            # 优先按名称推断 (第三方 tvg-id 格式混乱, 如 cctv2.cn@sd);
            # 名称认不出来时才退回用原 tvg-id, 以保留 91kds 的 ename
            tid2, disp, grp = cm.canonical('', name, group)
            if not tid2 or tid2.startswith('n_'):
                tid3, disp3, grp3 = cm.canonical(tvg_id_raw or '', name, group)
                if tid3 and not tid3.startswith('n_'):
                    tid2, disp, grp = tid3, disp3, grp3
            out.append(dict(tvg_id=tid2, name=disp or name,
                            group=group or grp, logo=logo, url=url,
                            ename=tvg_id_raw, order=len(out)))
        i = j + 1
    return out


def dedupe_ids_by_name(items):
    """同一显示名对应多个 tvg_id 时合并到同一个 id。

    91kds 数据里有少量重复条目 (如 潍坊新闻综合 = sdwftv1 / sdwfzw01,
    东方影视 = shdsj / shdsjhd), 不合并的话播放器会显示成两个同名台。
    优先保留不带 hd 后缀、更短的 id。
    """
    best = {}
    for it in items:
        nm = (it.get('name') or '').strip()
        if not nm:
            continue
        tid = it['tvg_id']
        if nm not in best:
            best[nm] = tid
            continue
        cur = best[nm]
        if (tid.endswith('hd'), len(tid)) < (cur.endswith('hd'), len(cur)):
            best[nm] = tid
    for it in items:
        nm = (it.get('name') or '').strip()
        if nm and nm in best:
            it['tvg_id'] = best[nm]
    return items


GROUP_ORDER = {'央视': 0, '卫视': 1, '地方': 2}


def normalize_group(tvg_id, name):
    """统一成 央视 / 卫视 / 地方

    公开源池自带 emoji 分组 (📡卫视频道 / 💰央视付费频道 / ☘️浙江频道 ...),
    混在一起会让人找不到台, 因此在写出前统一归类。
    """
    tid = tvg_id or ''
    if tid.startswith(('cctv', 'cgtn')):
        return '央视'
    if (name or '').endswith('卫视'):
        return '卫视'
    return '地方'


def group_and_sort(items):
    """按 tvg_id 聚合 -> 组内按清晰度降序 -> 组间按频道序

    items 里可带 quality=(height, bandwidth)。
    """
    items = dedupe_ids_by_name(items)
    groups = {}
    for it in items:
        groups.setdefault(it['tvg_id'], []).append(it)

    def line_key(it):
        q = it.get('quality')
        if q is None:
            # 从 m3u 读回的条目没有清晰度信息, 保持原文件中的顺序
            # (原文件已按清晰度排好, 重排反而会把高清线路挤到后面)
            return (0, 0, 0, it.get('order', 0))
        return (0 if it.get('status') in ('ok', 'unverified', None) else 1,
                -q[0], -q[1], it.get('order', 0))

    out = []
    for tid, lst in groups.items():
        seen = set()
        uniq = []
        for it in sorted(lst, key=line_key):
            if it['url'] in seen:
                continue
            seen.add(it['url'])
            uniq.append(it)
        rep = uniq[0]
        grp = normalize_group(rep['tvg_id'], rep['name'])
        for it in uniq:
            d = dict(it)
            d['name'] = rep['name']
            d['group'] = grp
            d['logo'] = d.get('logo') or rep.get('logo') or ''
            out.append(d)

    def sk(it):
        return (cm.sort_key_of(it['tvg_id'], it['name'])
                + (GROUP_ORDER.get(it['group'], 9),))

    out.sort(key=sk)
    return out


def extinf(it):
    bits = ['#EXTINF:-1',
            'tvg-id="%s"' % it['tvg_id'],
            'tvg-name="%s"' % it['name'],
            'group-title="%s"' % it['group']]
    if it.get('logo'):
        bits.append('tvg-logo="%s"' % it['logo'])
    return ' '.join(bits) + ',' + it['name']


def write_m3u(path, items, header='#EXTM3U'):
    items = group_and_sort(items)
    lines = [header]
    for it in items:
        lines.append(extinf(it))
        lines.append(it['url'])
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return items


def write_tvlist(path, items):
    items = group_and_sort(items)
    lines = ['%s|%s,%s' % (it['group'], it['name'], it['url']) for it in items]
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return items
