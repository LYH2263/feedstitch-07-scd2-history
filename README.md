# 01-feedstitch（信汇）

Feedstitch — 多源目录/价目 Feed 归并落库：解析 → 业务键去重/冲突隔离 → 按源水位线增量或全量落库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5100 |
| API | http://localhost:10100 |

## 主链

配置源与夹具 → 触发 Run → 解析 / 去重 / 冲突隔离 → Canonical（SCD2 版本链）+ 运行报告对账

## 种子说明

- **Alpha Catalog（clean）**：JSON，三件干净货号，增量可落库。
- **Beta Offers（dirty）**：JSONL，含与 Alpha 同键但价格不同的冲突行，以及迟到低 revision 行（底座增量会因水位线跳过）。
- **Gamma Shelf（xml）**：简易 XML，两件独立货号。
- **Alpha Catalog Refresh（同组刷新）**：JSON，与 Alpha 同 `authority_group`。SKU-1001 非键字段变化 → 关闭旧 SCD2 版本、开新版本；SKU-1002 同快照高 revision → 仅刷新开放版本；SKU-1003 无变化 → skip_stale。

打开“运行台”选源跑一次增量，再看 Staging / 冲突 / Canonical 三页计数是否与本次 Run 报告一致。
Canonical 页为当前有效视图（每业务键一行），点“历史版本”可查看该业务键的完整 SCD2 版本链
（半开区间 `[valid_from, valid_to)`，开放版本 `valid_to` 为空）。

### SCD2 与冲突边界

- 只有同源或同 `authority_group` 的写入允许演进业务键：非键字段相对当前有效版本变化时，旧版本写入
  `valid_to` 闭合、插入新开放版本，当前有效行同步前移——不做原地覆盖。
- 跨组同键非键分歧仍按原冲突规则进冲突队列（quarantine）：不写当前主数据、不伪造历史版本。
- Run 报告的插入 / 新开版本 / 刷新 / 冲突 / 跳过均从实际表重新计数并附 `reconciliation` 对账结果；
  当前有效主数据条数恒等于开放版本数。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口（未实现）

`lookback_backfill` / `schema_drift_guard` / `dead_letter_replay` / `webhook_push_source` /
`source_authority_rank` / `tombstone_retire` /
`watermark_commit_gate` / `field_map_transform` / `lineage_provenance`
