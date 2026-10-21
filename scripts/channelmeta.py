#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
频道标识归一化

问题背景
--------
91kds 频道表里, 同一个电视台会存在两套条目:
    cctv1hd / CCTV-1HD   (线路池 A, 多为 kdsvod 私有协议)
    cctv1  / CCTV-1综合   (线路池 B, 多为直连 HLS)
    hnwshd / 湖南卫视HD
    hnws   / 湖南卫视
再加上公开源池 (iptv-org / fanmingming) 的英文名 `CCTV-1 (720p)`。

如果直接拿 name 当频道键, 这三个变体会被播放器当成三个不同的台 ——
用户看到的就是"一个台变成两三个台", 而不是"一个台有三条线路"。

M3U 的归并规则是: tvg-id 相同 或 显示名完全相同 => 同一频道的不同线路。
本模块负责把以上各种写法压到同一个 (tvg_id, display_name)。
"""
import json
import re

# ---------------------------------------------------------------- 央视

CCTV_NAME = {
    'cctv1': 'CCTV-1 综合',
    'cctv2': 'CCTV-2 财经',
    'cctv3': 'CCTV-3 综艺',
    'cctv4': 'CCTV-4 中文国际',
    'cctv5': 'CCTV-5 体育',
    'cctv5p': 'CCTV-5+ 体育赛事',
    'cctv6': 'CCTV-6 电影',
    'cctv7': 'CCTV-7 国防军事',
    'cctv8': 'CCTV-8 电视剧',
    'cctv9': 'CCTV-9 纪录',
    'cctv10': 'CCTV-10 科教',
    'cctv11': 'CCTV-11 戏曲',
    'cctv12': 'CCTV-12 社会与法',
    'cctv13': 'CCTV-13 新闻',
    'cctv14': 'CCTV-14 少儿',
    'cctv15': 'CCTV-15 音乐',
    'cctv16': 'CCTV-16 奥林匹克',
    'cctv17': 'CCTV-17 农业农村',
    'cctv4k': 'CCTV-4K 超高清',
    'cctv8k': 'CCTV-8K 超高清',
    'cctv4o': 'CCTV-4 中文国际（欧洲）',
    'cctv4m': 'CCTV-4 中文国际（美洲）',
    'cctv_p1': 'CCTV+ 1 对外',
    'cctv_p2': 'CCTV+ 2 对外',
    'cgtn': 'CGTN',
}

# 91kds 里非标准 ename 的央视付费频道
CCTV_ALIAS = {
    'hjjc': ('cctv_hjjc', 'CCTV 怀旧剧场'),
    'dyjc': ('cctv_dyjc', 'CCTV 第一剧场'),
    'fyjc': ('cctv_fyjc', 'CCTV 风云剧场'),
    'fyyy': ('cctv_fyyy', 'CCTV 风云音乐'),
    'fyzq': ('cctv_fyzq', 'CCTV 风云足球'),
    'gfjs': ('cctv_gfjs', 'CCTV 兵器科技'),
    'sjdl': ('cctv_sjdl', 'CCTV 世界地理'),
    'nxss': ('cctv_nxss', 'CCTV 女性时尚'),
    'ystq': ('cctv_ystq', 'CCTV 台球'),
    'dszn': ('cctv_dszn', 'CCTV 电视指南'),
    'gefwq': ('cctv_gefwq', 'CCTV 高尔夫网球'),
    'ysjp': ('cctv_ysjp', 'CCTV 文化精品'),
    'wsjk': ('cctv_wsjk', 'CCTV 卫生健康'),
    'zsgw': ('cctv_zsgw', 'CCTV 中视购物'),
    'cctvxq': ('cctv_xq', 'CCTV 戏曲'),
    'cctvyl': ('cctv_yl', 'CCTV 娱乐'),
}

# ---------------------------------------------------------------- 卫视

# 简称 -> 91kds ename
WS_ID = {
    '湖南': 'hnws', '江苏': 'jsws', '浙江': 'zjws', '东方': 'dfws',
    '上海': 'dfws', '广东': 'gdws', '深圳': 'szws', '北京': 'bjws',
    '天津': 'tjws', '山东': 'sdws', '湖北': 'hbws', '黑龙江': 'hljws',
    '河北': 'hews', '安徽': 'ahws', '辽宁': 'lnws', '东南': 'dnws',
    '福建': 'dnws', '江西': 'jxws', '重庆': 'cqws', '四川': 'scws',
    '广西': 'gxws', '云南': 'ynws', '河南': 'haws', '陕西': 'snws',
    '贵州': 'gzws', '吉林': 'jlws', '海南': 'lyws', '甘肃': 'gsws',
    '山西': 'sxws', '厦门': 'xmws', '西藏': 'xzws', '内蒙古': 'nmgws',
    '宁夏': 'nxws', '新疆': 'xjws', '青海': 'qhws', '兵团': 'btws',
    '海峡': 'hxws', '三沙': 'ssws', '延边': 'ybws', '康巴': 'kbws',
    '大湾区': 'nfws', '农林': 'snnl', '安多': 'qhzy', '山东教育': 'sdjy',
}

# ---------------------------------------------------------------- 省份分组

# 播放器侧的分组顺序: 央视 -> 卫视 -> 各省 -> 其他
PROVINCES = [
    '北京', '天津', '河北', '山西', '内蒙古', '辽宁', '吉林', '黑龙江',
    '上海', '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南',
    '湖北', '湖南', '广东', '广西', '海南', '重庆', '四川', '贵州',
    '云南', '西藏', '陕西', '甘肃', '青海', '宁夏', '新疆',
]
TOP_GROUPS = ['央视', '卫视']
OTHER_GROUP = '其他'

GROUP_ORDER = {g: i for i, g in enumerate(TOP_GROUPS + PROVINCES + [OTHER_GROUP])}

# 频道名首 2~3 字 -> 省份, 由频道表(带 prov 字段)自动生成, 存 data/prov_map.json
PROV_MAP = {}


def load_prov_map(path):
    """载入 name_prefix -> 省份 映射 (缺失则忽略)"""
    global PROV_MAP
    try:
        with open(path, encoding='utf-8') as f:
            PROV_MAP = {k: v for k, v in json.load(f).items() if v in PROVINCES}
    except Exception:
        pass                      # 文件缺失时保留内存里已有的映射
    return PROV_MAP


def build_prov_map(data):
    """从频道表生成 前缀->省份 映射

    频道表的 prov 字段只覆盖 91kds 自己的条目, 公开源池没有。
    但公开池的地方台名字通常以城市/地区开头 (潍坊新闻综合、青岛新闻),
    因此用已知 prov 的条目反推「前缀 -> 省份」, 再套到公开池条目上。
    """
    votes = {}
    for ch in data:
        prov = (ch.get('prov') or '').strip()
        if prov not in PROVINCES:
            continue
        name = re.sub(r'[\s\-_]', '', (ch.get('name') or ''))
        for n in (2, 3):
            if len(name) >= n:
                votes.setdefault(name[:n], {}).setdefault(prov, 0)
                votes[name[:n]][prov] += 1
    mapping = {}
    for prefix, cnt in votes.items():
        prov, c = max(cnt.items(), key=lambda kv: kv[1])
        if c >= 2:                       # 至少两条支持, 避免个案噪声
            mapping[prefix] = prov
    global PROV_MAP
    PROV_MAP = mapping
    return mapping


def prov_of(name, prov=''):
    """判定一个频道属于哪个分组

    返回省份名 / 其他。央视、卫视由调用方先行拦截。
    """
    p = (prov or '').strip()
    if p in PROVINCES:
        return p
    n = re.sub(r'[\s\-_]', '', name or '')
    if not n:
        return OTHER_GROUP
    # 称号即省名: 山东新闻 / 北京文艺
    for x in PROVINCES:
        if n.startswith(x):
            return x
    # 城市/地区前缀查表, 先用 3 字再用 2 字
    for ln in (3, 2):
        if len(n) >= ln and n[:ln] in PROV_MAP:
            return PROV_MAP[n[:ln]]
    return OTHER_GROUP


# ---------------------------------------------------------------- 工具

# 英文台名 -> tvg_id (iptv-org 池用英文)
EN_ID = {
    'CCTV1': 'cctv1', 'CCTV2': 'cctv2', 'CCTV3': 'cctv3', 'CCTV4': 'cctv4',
    'CCTV5': 'cctv5', 'CCTV6': 'cctv6', 'CCTV7': 'cctv7', 'CCTV8': 'cctv8',
    'CCTV9': 'cctv9', 'CCTV10': 'cctv10', 'CCTV11': 'cctv11', 'CCTV12': 'cctv12',
    'CCTV13': 'cctv13', 'CCTV14': 'cctv14', 'CCTV15': 'cctv15', 'CCTV16': 'cctv16',
    'CCTV17': 'cctv17',
    'HUNANTV': 'hnws', 'HUNAN': 'hnws',
    'ZHEJIANGTV': 'zjws', 'ZHEJIANG': 'zjws', 'ZHEJIANGINTERNATIONAL': 'zjws',
    'DRAGONTV': 'dfws', 'DRAGON': 'dfws', 'SHANGHAI': 'dfws',
    'BEIJINGTV': 'bjws', 'BEIJING': 'bjws',
    'JIANGSU': 'jsws', 'GUANGDONG': 'gdws', 'SHENZHEN': 'szws',
    'TIANJIN': 'tjws', 'SHANDONG': 'sdws', 'HUBEI': 'hbws',
    'HEILONGJIANG': 'hljws', 'HEBEI': 'hews', 'ANHUI': 'ahws',
    'LIAONING': 'lnws', 'FUJIAN': 'dnws', 'SOUTHEAST': 'dnws',
    'JIANGXI': 'jxws', 'CHONGQING': 'cqws', 'SICHUAN': 'scws',
    'GUANGXI': 'gxws', 'YUNNAN': 'ynws', 'HENAN': 'haws',
    'SHAANXI': 'snws', 'GUIZHOU': 'gzws', 'JILIN': 'jlws',
    'HAINAN': 'lyws', 'GANSU': 'gsws', 'SHANXI': 'sxws',
    'XIAMEN': 'xmws', 'XIZANG': 'xzws', 'TIBET': 'xzws',
    'NEIMONGGOL': 'nmgws', 'INNERMONGOLIA': 'nmgws',
    'NINGXIA': 'nxws', 'XINJIANG': 'xjws', 'QINGHAI': 'qhws',
    'KANGBA': 'kbws', 'GREATBAY': 'nfws', 'ANHUITV': 'ahws',
    # 央视付费/数字频道
    'CCTVBILLIARDS': 'cctv_ystq',
    'CCTVGOLFTENNIS': 'cctv_gefwq',
    'CCTVHEALTH': 'cctv_wsjk',
    'CCTVNOSTALGIATHEATER': 'cctv_hjjc',
    'CCTVSTORMTHEATER': 'cctv_fyjc',
    'CCTVSANDSTORM': 'cctv_fyjc',
    'CCTVSTORMMUSIC': 'cctv_fyyy',
    'CCTVSTORMFOOTBALL': 'cctv_fyzq',
    'CCTTTHEFIRSTTHEATER': 'cctv_dyjc',
    'CCTVTEFIRSTTHEATER': 'cctv_dyjc',
    'CCTVWEAPONTECHNOLOGY': 'cctv_gfjs',
    'CCTVWEAPON': 'cctv_gfjs',
    'CCTVWOMENSFASHION': 'cctv_nxss',
    'CCTVWORLDGEOGRAPHY': 'cctv_sjdl',
    'CCTVCULTUREOFQUALITY': 'cctv_ysjp',
    'CCTVGUIDE': 'cctv_dszn',
    'CCTVSHOPPING': 'cctv_zsgw',
    'CCTVOPERA': 'cctv_xq',
    'CCTVPLUS1': 'cctv_p1',
    'CCTVPLUS2': 'cctv_p2',
}


def _norm(s):
    """比对用: 去空格/连字符/下划线/点, '+' 转 PLUS, 转大写"""
    return re.sub(r'[\s\-_.]', '', s or '').replace('+', 'PLUS').upper()


def name_key(name):
    """归并用的名称键: 剥掉纯泛称后缀

    不同源池对同一个地方台叫法不同: 乐至综合 / 乐至综合频道, 邢台综合 / 邢台综合频道。
    这类后缀只是"频道/综合/高清"这种泛称, 剥掉后是同一个台。
    但 CGTN纪录 / CGTN英语 的差异是真实的不同频道, 因此只认白名单后缀,
    绝不做任意前缀匹配。
    """
    n = re.sub(r'\s+', '', name or '')
    for suf in ('综合频道', '高清频道', '频道', '综合', '高清'):
        if n.endswith(suf) and len(n) - len(suf) >= 3:
            n = n[:-len(suf)]
            break
    return n


def strip_quality(ename):
    """cctv1hd -> cctv1 ; hnwshd/hnws4k -> hnws ; cctv4k 保持不变

    注意 cctv4k / cctv8k 结尾的 'k' 不是清晰度后缀, 必须原样保留。
    md / sd 同样是清晰度变体 (网关的 cctv1md 与 cctv1hd 是同一个台)。
    """
    e = (ename or '').strip().lower()
    if not e:
        return e
    # cctv4k / cctv8k 是独立频道, 不能剥
    if e in ('cctv4k', 'cctv8k'):
        return e
    # 央视: cctv<数字> + 可选清晰度
    m = re.fullmatch(r'(cctv\d{1,2})(?:hd|md|sd)', e)
    if m:
        return m.group(1)
    # 卫视: xxxws / 特色频道 + hd|md|sd|4k
    m = re.fullmatch(r'(.+?)ws(?:hd|md|sd|4k)', e)
    if m:
        return m.group(1) + 'ws'
    m = re.fullmatch(r'(.+?)(?:hd|md|sd)', e)
    if m and m.group(1) in WS_ID.values():
        return m.group(1)
    # 通用: 地方台的 xxxhd / xxxmd 视为同频道的 HD / MD 版
    m = re.fullmatch(r'(.{2,})(?:hd|md|sd)', e)
    if m:
        return m.group(1)
    return e


def id_from_name(name):
    """公开源池的频道名 -> tvg_id, 认不出来返回 ''"""
    n0 = re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', '', name or '').strip()
    n = re.sub(r'\b(HD|SD|FHD|UHD|HEVC|H\.?265)\b', '', n0, flags=re.I).strip()

    # 4K / 8K 必须先判, 否则会被 ^CCTV-?(\d+) 吃掉成 CCTV-4 / CCTV-8
    if re.match(r'^CCTV[-\s]?4K', n, re.I):
        return 'cctv4k'
    if re.match(r'^CCTV[-\s]?8K', n, re.I):
        return 'cctv8k'

    # CCTV + 可选 +
    m = re.match(r'^CCTV[-\s]?(\d{1,2})\s*(\+|PLUS|plus)?', n, re.I)
    if m:
        num = m.group(1)
        if m.group(2):
            return 'cctv5p' if num == '5' else 'cctv%s' % num
        return 'cctv%s' % num

    # 中文卫视: 只有名称里带「卫视」才允许前缀匹配。
    # 否则「浙江新闻」「钱江都市」会被前缀 '浙江' 吃掉, 全部塌进浙江卫视,
    # 地方台成批丢失 —— 这是最危险的一类过归并。
    has_ws = '卫视' in n
    short = n.replace('卫视', '').replace('4K', '').replace('8K', '').replace(' ', '').strip()
    for k in sorted(WS_ID, key=len, reverse=True):
        if short == k or (has_ws and short.startswith(k)):
            return WS_ID[k]

    # CGTN 家族: 纪录/英语/西语/阿语/俄语 是各自独立的台, 不能用前缀一把抓
    key = _norm(n)
    if key.startswith('CGTN'):
        rest = key[4:].replace('记录', '纪录')
        rest = {'JL': '纪录', 'JLP': '纪录', 'DOC': '纪录'}.get(rest, rest)
        return 'cgtn' if not rest else 'cgtn_' + rest.lower()

    # 英文台名: 必须整体相等 —— 用 startswith 会把 Hunan TV 系列全部吃掉
    for k in sorted(EN_ID, key=len, reverse=True):
        if key == k:
            return EN_ID[k]
    return ''


def display_for(tvg_id, fallback=''):
    """tvg_id -> 规范中文显示名"""
    if not tvg_id:
        return fallback
    if tvg_id in CCTV_NAME:
        return CCTV_NAME[tvg_id]
    for e, (tid, nm) in CCTV_ALIAS.items():
        if tid == tvg_id:
            return nm
    for short, tid in WS_ID.items():
        if tid == tvg_id:
            # 同一 id 可能被多个简称映射(福建/东南), 取第一个稳定的
            if short in ('福建', '上海'):
                continue
            return short + '卫视'
    if tvg_id.startswith('cctv_'):
        return fallback
    # CGTN 子频道: 显示名由 id 反推, 保证「记录/纪录」两种写法落到同一个名字
    if tvg_id.startswith('cgtn_'):
        return 'CGTN' + tvg_id[5:]
    return fallback


def canonical(ename, raw_name, group_hint=''):
    """返回 (tvg_id, display_name, group)

    ename 为空时 (公开源池) 走名称推断。
    """
    e = (ename or '').strip().lower()
    raw = (raw_name or '').strip()

    # 公开源池里有拿纯数字当 tvg-id 的 ("1"/"3"), 会把一批毫不相干的台
    # 全部塌进同一个 id, 显示成一个台。这类 id 一律丢弃, 改按名称推断。
    if re.fullmatch(r'\d{1,4}', e or ''):
        e = ''

    if e:
        # 央视付费频道别名
        if e in CCTV_ALIAS:
            tid, nm = CCTV_ALIAS[e]
            return tid, nm, '央视'
        base = strip_quality(e)
        # CCTV
        m = re.fullmatch(r'(cctv\d{1,2})', base)
        if m:
            tid = m.group(1)
            return tid, CCTV_NAME.get(tid, raw), '央视'
        if e in ('cctv4k', 'cctv8k'):
            return e, CCTV_NAME[e], '央视'
        if e in ('cctv5p',):
            return e, CCTV_NAME[e], '央视'
        if e in ('cctv4o', 'cctv4m'):
            return e, CCTV_NAME[e], '央视'
        if re.fullmatch(r'cgtn\w*', e):
            if e == 'cgtn':
                return 'cgtn', 'CGTN', '央视'
            # cgtnrecord / cgtnenglish 等是独立频道, 各自成台
            return 'cgtn_' + e[4:], raw or 'CGTN', '央视'
        # 前缀兜底: 处理 cctv2.cn@sd / cctv9.cn@sd 这类第三方 id
        m = re.match(r'^(cctv\d{1,2})(?:[.\-_@]|$)', base)
        if m:
            tid = m.group(1)
            return tid, CCTV_NAME.get(tid, raw), '央视'
        # 卫视: base 命中 WS_ID 的某个值
        if base in WS_ID.values():
            short = next(s for s, t in WS_ID.items() if t == base and s not in ('福建', '上海'))
            return base, short + '卫视', '卫视'
        # 其它 (地方台等): 用去掉 HD 后缀的 ename 做 id
        nm = re.sub(r'(HD|4K)$', '', raw, flags=re.I).strip() or raw
        return base or e, nm, group_hint or '地方'

    # ---- 无 ename: 从名称推断
    tid = id_from_name(raw)
    if tid:
        nm = display_for(tid, raw)
        grp = '央视' if tid.startswith(('cctv', 'cgtn')) else '卫视'
        return tid, nm, grp
    # 兜底: 用规范化名称自身做 id
    nm = re.sub(r'\s+', ' ', raw).strip()
    tid = 'n_' + re.sub(r'[^0-9a-z\u4e00-\u9fa5]+', '', nm.lower())[:40]
    return tid, nm, group_hint or ''


def sort_key_of(tvg_id, name):
    """央视按台号, 卫视按名称长度+拼音序, 其余按名"""
    g = '央视' if tvg_id.startswith(('cctv', 'cgtn')) else (
        '卫视' if tvg_id.endswith('ws') and len(name) <= 8 else '地方')
    m = re.match(r'cctv(\d{1,2})', tvg_id)
    if m:
        num = int(m.group(1))
        return (0, num, 0, name)
    if tvg_id.startswith('cctv'):
        return (0, 900, 0, name)
    if tvg_id.startswith('cgtn'):
        return (0, 950, 0, name)
    return (GROUP_ORDER.get(g, 9), 0, len(name), name)
