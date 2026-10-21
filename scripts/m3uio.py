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
        # 去掉清晰度标记 (浙江卫视 [1080P] -> 浙江卫视), 否则回读时
        # endswith('卫视') 判不出来, 卫视会被当成地方台
        name = re.sub(r'\s*\[(?:4K|8K|\d{3,4}[Pp])\]\s*$', '', name).strip()
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

    另外把「乐至综合」与「乐至综合频道」这类只差泛称后缀的叫法统一成同一个显示名
    (见 channelmeta.name_key), 否则同一个台会以两个名字各出现一次。
    """
    # 先做泛称后缀归一: key 相同的多个名字, 统一成最短的那个
    by_key = {}
    for it in items:
        nm = (it.get('name') or '').strip()
        if nm:
            k = cm.name_key(nm)
            lst = by_key.setdefault(k, [])
            if nm not in lst:
                lst.append(nm)
    alias = {}
    for k, nms in by_key.items():
        if len(nms) < 2:
            continue
        canon = min(nms, key=len)
        for nm in nms:
            if nm != canon:
                alias[nm] = canon
    if alias:
        for it in items:
            nm = (it.get('name') or '').strip()
            if nm in alias:
                it['name'] = alias[nm]

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


GROUP_ORDER = cm.GROUP_ORDER


def normalize_group(tvg_id, name, hint=''):
    """统一分组: 央视 / 卫视 / 省份 / 其他

    公开源池自带 emoji 分组 (📡卫视频道 / 💰央视付费频道 / ☘️浙江频道 ...),
    混在一起会让人找不到台; 而 91kds 的 prov 字段能给出真实省份。
    这里优先级: 央视 > 卫视 > 已知 hint(省份) > 按名称/前缀推断。
    """
    tid = (tvg_id or '').lower()
    if tid.startswith(('cctv', 'cgtn')):
        return '央视'
    nm = name or ''
    if nm.endswith('卫视') or nm in ('凤凰卫视',):
        return '卫视'
    h = (hint or '').strip()
    if h in cm.GROUP_ORDER and h not in ('央视', '卫视'):
        return h
    return cm.prov_of(nm)


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
        # 同清晰度里, 连续多次验活都通过的排前面
        return (0 if it.get('status') in ('ok', 'unverified', None) else 1,
                -q[0], -q[1], -int(it.get('steady') or 0), it.get('order', 0))

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
        grp = normalize_group(rep['tvg_id'], rep['name'], rep.get('group'))
        for it in uniq:
            d = dict(it)
            d['name'] = rep['name']
            d['group'] = grp
            d['logo'] = d.get('logo') or rep.get('logo') or ''
            out.append(d)

    def sk(it):
        """先按分组(央视/卫视/各省/其他), 组内央视按台号, 其余按名称"""
        g = GROUP_ORDER.get(it['group'], 99)
        tid = it['tvg_id']
        m = re.match(r'cctv(\d{1,2})', tid.lower())
        num = int(m.group(1)) if m else (900 if tid.startswith('cgtn') else 950)
        return (g, num, len(it['name']), it['name'])

    out.sort(key=sk)
    return out


def quality_tag(it):
    """把 quality=(height, bandwidth) 转成 4K/1080P/720P"""
    q = it.get('quality') or (0, 0)
    try:
        h = int(q[0] or 0)
    except Exception:
        return ''
    if h >= 2160:
        return '4K'
    if h >= 1080:
        return '1080P'
    if h >= 720:
        return '720P'
    return ''


def extinf(it, tid=None):
    bits = ['#EXTINF:-1',
            'tvg-id="%s"' % (tid or it['tvg_id']),
            'tvg-name="%s"' % it['name'],
            'group-title="%s"' % it['group']]
    if it.get('logo'):
        bits.append('tvg-logo="%s"' % it['logo'])
    return ' '.join(bits) + ',' + it['name']


def _opt_lines(it):
    """需要 Referer/UA 的线路: 不写出来播放器就播不了

    验活时是带着这些头测通的, 产物里丢掉等于白测。
    """
    out = []
    for k, v in (it.get('headers') or {}).items():
        kl = (k or '').lower()
        if kl in ('referer', 'referrer'):
            out.append('#EXTVLCOPT:http-referrer=%s' % v)
        elif kl in ('user-agent', 'ua'):
            out.append('#EXTVLCOPT:http-user-agent=%s' % v)
    return out


def write_m3u(path, items, header='#EXTM3U', epg=None, epg_url=''):
    """epg: {内部 tvg_id: EPG channel id}; epg_url: xmltv 地址

    内部 id 保持小写 (归并/分组/排序都靠它), 只在 EXTINF 里换成 EPG 的 id,
    这样播放器才找得到节目单。
    """
    items = group_and_sort(items)
    if not isinstance(epg, dict):
        epg = {}                  # 传错类型不该让整条流水线崩掉
    if epg_url:
        header = '#EXTM3U x-tvg-url="%s" url-tvg="%s"' % (epg_url, epg_url)
    lines = [header]
    for it in items:
        tid = (epg or {}).get(it['tvg_id'])
        lines.append(extinf(it, tid))
        lines.extend(_opt_lines(it))
        lines.append(it['url'])
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return items


def write_tvlist(path, items):
    items = group_and_sort(items)
    lines = []
    for it in items:
        tag = quality_tag(it)
        nm = it['name'] + (' [%s]' % tag if tag else '')
        lines.append('%s|%s,%s' % (it['group'], nm, it['url']))
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return items
