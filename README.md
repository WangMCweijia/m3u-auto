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

## 分组：央视 / 卫视 / 省份

`group-title` 不再笼统地写「地方」，而是按省份分开：

```
央视 / 卫视 / 北京 / 天津 / … / 山东 / 河南 / … / 新疆 / 影视轮播 / 其他
```

影视轮播（电影、剧场、热播这类没有地域属性的台）从「其他」里单独拎出来了。

省份来源分两级：

1. 91kds 频道表自带 `prov` 字段（31 个省级行政区），直接采用
2. 公开源池没有 `prov`，用频道表反推出的「地名前缀 → 省份」映射兜底
   （`data/prov_map.json`，构建时自动生成）。例如 `潍坊新闻综合` → 山东、
   `青岛新闻` → 山东、`新疆少儿` → 新疆

两级都判不出来的（如 `电影丧尸片`、`乐至综合`）归入 **其他**，不会被硬塞进某个省。

## 节目单（EPG）

`live.m3u` 头部带 `x-tvg-url` / `url-tvg`，指向 fanmingming 的公开 EPG：

```
#EXTM3U x-tvg-url="https://cdn.jsdelivr.net/gh/fanmingming/live@main/e.xml"
```

播放器只按 `tvg-id` 去 xmltv 里找节目单，所以内部 id（`cctv1` / `zjws`）在写出时
会换成 EPG 的 channel id（`CCTV1` / `浙江卫视`）。映射规则是确定性的，不用人工维护：

| 情况 | 结果 |
|---|---|
| 显示名正好等于某个 EPG id | 用它（`浙江卫视` → `浙江卫视`） |
| `tvg_id` 形如 `cctv<数字>` | `CCTV<数字>`（`cctv1` → `CCTV1`） |
| `CCTV5+` 这类带后缀的 | 单独匹配 |
| EPG 里没有这个台 | 保持原 id，不影响播放 |

映射结果缓存在 `data/epg_map.json`（7 天刷新一次，EPG 文件有 6MB+，不必每次拉）。
当前覆盖 17 个央视 + 40 多个卫视及特色频道。

## 数据来源

1. **公开频道表** `sj.91kds.cn` —— 拉取频道清单与线路，无鉴权
2. **gslb 网关** —— 见下节，把 91kds 的 `ename` 转成可用地址
3. **iptv-api / zbefine / iptv-org / fanmingming / vbskycn** —— 补充备选线路

所有线路在构建时逐条 HTTP 实测，**必须真的取到一个分片（.ts）**才保留；
同频道内按解析出的 `RESOLUTION` / `BANDWIDTH` 降序排列，高清线路优先。

### 为什么必须验到分片

只看「能不能取到 m3u8」会放过大量**空壳线路**：播放列表合法、
`#EXT-X-MEDIA-SEQUENCE` 一直在滚、但里面指向的 `.ts` 全部 404。
播放器拿不到画面，用户看到的是 App 自带的垫片/广告，
典型症状就是「CCTV-1 好几条线路都是假的」。

`scripts/hlslive.py` 的做法：

1. 取播放列表；master 则下钻到 media playlist，自引（`a.m3u8` 里指向 `a.m3u8`）直接判死
2. **采样首尾两个分片**，各用 `Range: bytes=0-4095` 拉前 4KB
3. 必须拿到 TS 同步字节 `0x47` / fMP4 `styp|moof` 这类**真媒体魔数**；
   `text/html`、XML、纯文本一律判死（早期版本有「≥1KB 就算活」的兜底，
   HTML 错误页会被误判成好线路，已删掉）
4. 带 `#EXT-X-ENDLIST` 或 `PLAYLIST-TYPE:VOD`（点播）的判死 —— 直播订阅里放点播没有意义

采样两个点是为了对付网关的**间歇性 404**：只测一个分片时撞上畸形响应就漏了。
只要有一个分片是真媒体就算这条线路可播（另一个偶发失败属正常，播放器会重试）。

### 缓存线路也必须重验

gslb 网关会限流，命中结果因此落盘缓存 7 天兜底。但缓存里**陈旧地址不能直接当成活的**：
早期实现把缓存条目直接标成 `ok`，而 `run_probe` 会跳过已带结论的条目，
结果是灰掉一周的死链照样进订阅，还以为「每条都验过」。

现在缓存条目带 `status='cached'`，**必须重新验活**，最多带 60 个频道进队列（控制构建时长）。

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
scripts/channelmeta.py    频道标识归一化 (tvg-id / 规范中文名 / 省份)
scripts/m3uio.py          M3U 读写 + 按 tvg-id 聚合排序
scripts/hlslive.py        直播性判定 (必须取到分片才算活)
scripts/adfilter.py       广告主机黑名单过滤
scripts/gslb.py           gslb 网关采集 (央视/卫视线路主力)
scripts/build.py          拉频道表 -> 解析 -> 验活 -> 生成订阅
scripts/merge_public.py   合并公开源池补充线路
scripts/genindex.py       生成索引页
scripts/epg.py            EPG 频道表拉取 + tvg-id 对齐
scripts/verifystate.py    验活历史 (稳定性排序 / 主机熔断)
scripts/local_run.py      本地一键跑完整流水线 (Actions 兜底)
tests/                    单元测试 (24 个用例, CI 每次跑)
data/gslb_cache.json      gslb 命中缓存
data/prov_map.json        地名前缀 -> 省份 (构建时自动生成)
data/ad_blocklist.json    已确认的广告主机黑名单
data/ad_candidates.json   画面聚类出的可疑线路 (仅候选, 不生效)
data/epg_map.json         内部 tvg-id -> EPG channel id (7 天刷新)
data/verify_state.json    每条线路的连续成功次数 (排序 + 熔断依据)
.github/workflows/update.yml   每 6 小时构建
.github/workflows/audit-ads.yml 每周广告审计 (出联系表)

# 需要 av / pillow 的辅助工具, CI 主流程不用
scripts/audit.py          某频道所有线路的画面联系表
scripts/adcheck.py        按画面聚类找可疑线路
scripts/grab.py           抓一帧
scripts/loopcheck.py      判断是否循环垫片
```

## 广告线路怎么处理

能播但有广告的线路分两类，处理方式不同：

| 类型 | 特征 | 处理 |
|---|---|---|
| 空壳 | m3u8 正常、分片 404 | `hlslive.py` 自动判死，不需人工 |
| 广告插播 | 分片正常、画面是广告 | 人工看联系表确认 → 写进 `data/ad_blocklist.json` |

第二类没法靠代码自动判定：这些站**间歇插播**，同一主机这次抓到真节目、
下次抓到广告；而且它们会对热播频道塞广告、对其它频道照常转发，
按「主机」或「单条 URL」一刀切都会误伤。所以流程是：

```bash
pip install av pillow
python scripts/audit.py out/live.m3u "CCTV-1 综合" sheet.png   # 看一眼联系表
python scripts/adcheck.py --m3u out/live.m3u --only priority    # 出候选清单
```

确认后把主机写进 `data/ad_blocklist.json`：

```json
{"host": {"74.91.26.218:82": {"reason": "广告插播(400-179-9565)", "seen": "2026-10-21"}}}
```

已知广告主机（2026-10-21 画面实测）：`74.91.26.218:82`、`107.150.60.122`、
`207.56.13.146:81`。

## 本地运行

```bash
python scripts/local_run.py                  # 一键: 构建 + 合并 + 索引页
python scripts/local_run.py --push           # 再提交并推回远端
python -m unittest discover -s tests -v      # 单元测试

python scripts/build.py --out out            # 拉表 + gslb + 验活
python scripts/build.py --local table.zip    # 用本地频道表
python scripts/build.py --no-probe           # 跳过验活，快速生成
python scripts/build.py --no-gslb            # 跳过 gslb 网关
python scripts/gslb.py --list                # 单独看 gslb 可用频道
python scripts/merge_public.py --base out/live.m3u --out out/live.m3u
```

## 防回归

过归并的坑（一个台变多个台 / 多个台塌成一个）连续踩过三轮，所以现在有两道闸：

1. **单元测试** `tests/`（24 个用例）覆盖：CCTV-4K 不等于 CCTV-4、`浙江新闻` 不能被
   `浙江` 前缀吞进浙江卫视、纯数字 tvg-id 必须丢弃、`EXTINF` 与 URL 之间的
   `#EXTVLCOPT` 不能被当成分片、带 `[1080P]` 的名字回读后仍归到卫视……
   每一条都对应一次真实事故。
2. **CI 环比门禁**：与上一版 `out/stats.json` 对比，频道数或线路数跌超 30% 直接中止发布；
   单个 gslb 网关命中数腰斩会打 warning（多半是 auth 过期或限流）。

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

代码以 MIT 许可发布（见 `LICENSE`），但**许可证不覆盖 `out/` 下的频道数据** ——
那些数据来自公开网络接口，本项目不对其主张任何权利。

本仓库是自用工具，不提供任何形式的可用性保证。订阅地址没有访问控制，
知道链接的人都能取用，请勿对外传播。若权利人认为内容不当，提 issue 即可删除相关条目。
