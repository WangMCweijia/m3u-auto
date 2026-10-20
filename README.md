# m3u-auto

自动聚合并实测存活的公开直播源，每 6 小时重建一次。

当前规模：**528 个频道 / 920 条线路 / 159 个多线路频道**（平均 1.74 条/台）。

## 订阅地址

| 格式 | 地址 |
|---|---|
| M3U（推荐） | `https://cdn.jsdelivr.net/gh/WangMCweijia/m3u-auto@main/out/live.m3u` |
| TVLIST | `https://cdn.jsdelivr.net/gh/WangMCweijia/m3u-auto@main/out/live.txt` |
| 索引页 | `https://cdn.jsdelivr.net/gh/WangMCweijia/m3u-auto@main/out/index.html` |

备用（无 CDN 缓存，可能较慢）：
`https://raw.githubusercontent.com/WangMCweijia/m3u-auto/main/out/live.m3u`

## 多线路归并（重要）

同一个电视台往往有多个可用地址。本项目的输出保证**一个台只显示成一个台，
多条线路挂在它下面**，而不会重复成若干个同名频道。

做法：

- 每条 `#EXTINF` 都带 `tvg-id`（如 `cctv1`、`hnws`）与 `tvg-name`
- 同一频道的所有线路共用同一个 `tvg-id`，且显示名逐字相同
- 同一 `tvg-id` 的线路连续排列，播放器内可直接切换
- **绝不**在频道名后加 ` #2`、`(线路2)` 之类后缀 —— 那会让播放器认为是另一个台

频道标识的归一化在 `scripts/channelmeta.py`，处理了这些等价写法：

| 同一频道 | 来源写法 |
|---|---|
| CCTV-1 综合 | `cctv1hd` / `cctv1` / `CCTV-1HD` / `CCTV-1综合` / `CCTV-1 (720p)` |
| 湖南卫视 | `hnwshd` / `hnws` / `湖南卫视HD` / `Hunan TV (2160p)` |
| 央视台球 | `ystq` / `CCTV-Billiards` |

注意 `cctv4k`、`cctv8k` 是独立频道，不会被并入 `cctv4` / `cctv8`；
`cctv4o` / `cctv4m`（欧洲版/美洲版）同理。

## 数据来源

1. **公开频道表** `sj.91kds.cn` —— 拉取频道清单与线路，无鉴权
2. **gslb 网关** —— 见下节，把 91kds 的 `ename` 转成可用地址
3. **iptv-api / zbefine / iptv-org / fanmingming / vbskycn** —— 补充备选线路

所有线路在构建时逐条 HTTP 实测，只保留真正能取到播放列表的；
同频道内按解析出的 `RESOLUTION` / `BANDWIDTH` 降序排列，高清线路优先。

### gslb 网关（央视/卫视线路的主要来源）

91kds 表里央视与卫视的主线路走 `kdsvod://`，真实地址在 `v.91kds.com`，
而该域名已解析到 Linode 美国段且 TCP 不通，4082 条线路全废。

但公开池里存在接受**同一套 ename** 的第三方网关：

```
http://63.141.230.178:82/gslb/zbdq5.m3u8?id=cctv1hd
http://38.75.136.137:98/gslb/dsdqbv/cctv1hd.m3u8?auth=test20251009
http://38.75.136.137:98/gslb/dsdqpub/cctv1hd.m3u8?auth=testpub
```

实测 161 个央视/卫视频道中 96 个可用，每个台可拿到 2–3 条线路。
`scripts/gslb.py` 负责探测，命中结果写入 `data/gslb_cache.json`。

**注意**：这些网关会限流。同一 IP 短时间内反复全量扫描时，命中数会从 96 掉到 24。
因此默认并发只开 4，且扫描结果落盘缓存（7 天 TTL），本次没扫到的频道用缓存线路兜底。

## 结构

```
scripts/channelmeta.py    频道标识归一化 (tvg-id / 规范中文名)
scripts/m3uio.py          M3U 读写 + 按 tvg-id 聚合排序
scripts/gslb.py           gslb 网关采集 (央视/卫视线路主力)
scripts/build.py          拉频道表 -> 解析 -> 验活 -> 生成订阅
scripts/merge_public.py   合并公开源池补充线路
scripts/genindex.py       生成索引页
data/gslb_cache.json      gslb 命中缓存
.github/workflows/update.yml  定时任务
out/                      构建产物
```

## 本地运行

```bash
python scripts/build.py --out out            # 拉表 + gslb + 验活
python scripts/build.py --local table.zip    # 用本地频道表
python scripts/build.py --no-probe           # 跳过验活，快速生成
python scripts/build.py --no-gslb            # 跳过 gslb 网关
python scripts/gslb.py --list                # 单独看 gslb 可用频道
python scripts/merge_public.py --base out/live.m3u --out out/live.m3u
```

## 已知限制

- 91kds 频道表中央视/卫视主线路走 `kdsvod://` 私有协议，依赖的 `v.91kds.com` 域名已失效
  （解析到 Linode 美国段，TCP 不通），因此这两类频道主要依靠公开源池补全。
- 表里同一个电视台常有 HD 与非 HD 两套条目（如 `cctv1hd` 30 条线路 + `cctv1` 17 条线路），
  HD 侧的线路几乎全是 `kdsvod://`，所以合并后可用线路仍以非 HD 侧为主。
- 地方台存活率明显高于央视/卫视（地方广电 CDN 多为公网直连）。
- 线路随时可能失效，订阅重建后会自动剔除。

## 声明

本项目只做公开流地址的索引与可用性测试，不托管、不转码、不分发任何音视频内容。
所有内容版权归原权利人所有。
