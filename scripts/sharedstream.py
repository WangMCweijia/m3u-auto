# -*- coding: utf-8 -*-
"""广告池识别: 多条不相干的频道收敛到同一个最终流。

为什么这条判据可靠
------------------
正常的电视信号是「一个频道一路流」。除了少数同频道的不同码率
(md/hd/4K 都还属于同一个频道), 不可能出现「CCTV-1、CCTV-5、内蒙古卫视、
高尔夫网球」全部落到同一个 m3u8 上 —— 那只能是农场站自己搭的一个
大杂烩流池, 里面轮播保健品广告。

2026-11 实测: `204.12.234.154:88/applive/{102,yss,mkt}.m3u8` 三个流
分别被 71 / 61 / 51 个频道共用, 抽帧确认画面全是 400 电话广告。
而真源 `204.12.241.18:82/live/cctv6md.m3u8` 只服务 1 个频道。

单帧抽帧有漏(广告轮播), 但「多频道共用一个流」是结构性的, 不会漏。

用法
----
    import sharedstream
    n = sharedstream.apply(items)      # 就地剔除, 返回剔除条数

每个 item 需要 `final` 字段(302 后的最终地址)或 `url`。
"""
import json
import os
import re
import urllib.parse
from collections import defaultdict

# 一个流被 >= 4 个不同频道共用就判定为广告池。
# 真联播不会超过 3 个频道, 而实测的广告池是 51~71 个, 阈值留了很大余量。
MIN_CHANNELS = 4
# 再要求该组至少 6 条线路, 免得把 4 个频道各 1 条的正常小源误杀
MIN_LINES = 6


# 路径里这几种段不携带频道信息, 归一化时直接丢掉
#   - 码率/清晰度: 1500k / 720p / hd / 4k
#   - 通用文件名: index.m3u8 / playlist / tzwj_video ...
# 实测河北县级台是 .../live/<台代号>/1500k/tzwj_video.m3u8, 不丢掉码率段
# 就会把 22 个不同的县台塌成一个"广告池"。
_NOISE = re.compile(
    r'^(\d+[kKpP]|hd|sd|fhd|uhd|4k|8k|2k|'
    r'index|playlist|master|hls|tvlist|channels|list|play|now|out|main|'
    r'video|stream|tv|vod|live|tzwj_video)$',
    re.I)
# 这些目录名太通用, 下面跟纯数字时不足以区分站点 (/live/1.m3u8 到处都是)
_GENERIC_DIR = {'live', 'tv', 'hls', 'vod', 'channel', 'channels', 'play', 'www'}


def stream_key(url):
    """归一化成一个「流标识」, 目标是把「同一个流的不同写法」并到一起。

    广告池会轮换边缘 IP (实测同一个 applive/yss 出现在 69.197.181.106、
    142.54.178.42、204.12.234.154 上), 所以主机一般不参与 key —— 否则
    一组被拆散, 每组都凑不够阈值。

    但主机也不能无条件丢: /live/index.m3u8、/live/1.m3u8 这种到处都有,
    丢了就把不同站点混成一个。所以只有"带辨识度"的路径段才配当 key。

    http://204.12.234.154:88/applive/yss.m3u8?t=1&k=2 -> applive/yss
    http://69.197.181.106:88/applive/102.m3u8         -> applive/102
    http://x/live/rqtv1/1500k/tzwj_video.m3u8         -> live/rqtv1
    http://69.30.245.50/live/cctv6.m3u8               -> live/cctv6
    http://a.com/live/index.m3u8                      -> a.com/live/index
    """
    if not url:
        return None
    if '://' not in url:            # 容忍已归一化的 'host/path' 形式
        url = 'http://' + url
    p = urllib.parse.urlparse(url)
    if not p.netloc:
        return None
    path = p.path or '/'
    path = re.sub(r'/segment_\d+\.ts$', '', path, flags=re.I)
    path = re.sub(r'/\d+\.(ts|m4s)$', '', path, flags=re.I)
    path = re.sub(r'\.(m3u8|m3u|ts|mp4)$', '', path, flags=re.I)
    path = re.sub(r'_\d+$', '', path)
    host = p.netloc.lower()

    seg = [s for s in path.split('/') if s and not _NOISE.match(s)]
    if len(seg) < 2:
        return host + path
    tail, parent = seg[-1], seg[-2]
    if tail.isdigit() and parent.lower() in _GENERIC_DIR:
        return host + path
    return parent + '/' + tail


def _blocklist_candidates(path):
    """按 cwd / 仓库根 依次找, 与 adfilter 的查找顺序一致"""
    yield path
    here = os.path.dirname(os.path.abspath(__file__))
    yield os.path.join(here, '..', 'data', 'ad_blocklist.json')


def load_blocked(path='data/ad_blocklist.json'):
    """读黑名单的 stream 段: {流标识: {reason, seen}}

    这里是「直接点名」的流, 不依赖频道数阈值 —— 用于确认过的固定广告池
    (如 applive/{102,yss,mkt}), 以及只挂了两三个频道、数量上够不到阈值的小池。
    """
    for p in _blocklist_candidates(path):
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        out = {}
        for k, v in (d.get('stream') or {}).items():
            key = stream_key(k) or k
            out[key] = v or {}
        return out
    return {}


def analyze(items, blocked=None):
    """返回 [(key, 频道数, 线路数, [items])], 只含判定为广告池的组"""
    blocked = load_blocked() if blocked is None else blocked
    groups = defaultdict(list)
    for it in items:
        k = stream_key(it.get('final') or it.get('url'))
        if k:
            groups[k].append(it)

    bad = []
    for k, lst in groups.items():
        chans = {it.get('tvg_id') or it.get('name') for it in lst}
        named = k in blocked
        if named or (len(chans) >= MIN_CHANNELS and len(lst) >= MIN_LINES):
            bad.append((k, len(chans), len(lst), lst))
    bad.sort(key=lambda x: -x[2])
    return bad


def apply(items, verbose=True, blocked=None):
    """就地剔除广告池线路, 返回剔除条数

    blocked: 直接点名的流标识集合/dict; 不传则从 data/ad_blocklist.json 读
    """
    bad = analyze(items, blocked)
    if not bad:
        return 0
    drop = set()
    for k, nch, nline, lst in bad:
        if verbose:
            names = sorted({it['name'] for it in lst if it.get('name')})
            print('  广告池 %-46s %3d 频道 / %3d 条  (%s%s)'
                  % (k[:46], nch, nline, ', '.join(names[:3]),
                     '...' if len(names) > 3 else ''), flush=True)
        for it in lst:
            drop.add(id(it))
    items[:] = [it for it in items if id(it) not in drop]
    if verbose:
        print('  广告池剔除 %d 条 (共 %d 个流池)' % (len(drop), len(bad)), flush=True)
    return len(drop)
