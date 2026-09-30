# 化探样品三级权限域

化探样品权限升级为 **项目组（group）— 采样区（area）— 样品包（package）** 三级归属。
样品（`geochem` 表）挂在样品包上，所有读写都先经过授权结论判定。

## 归属与种子数据

| 层级 | 表 | 种子 |
| --- | --- | --- |
| 项目组 | `geochem_group` | G1 北山项目组、G2 南泥沟项目组（归属单位 U-A 甲地质大队） |
| 采样区 | `geochem_area` | A1 北山Ⅰ、A2 北山Ⅱ（G1）、A3 南泥沟Ⅰ（G2） |
| 样品包 | `geochem_package` | P1/P2（A1）、P3（A2）、P4（A3） |

账号：方研 `a-fang`、李勘 `a-li`（U-A）；赵测 `a-zhao`、钱检 `a-qian`（U-B）。

种子授权：赵测在 A1 只读；李勘在 P3 只读（本单位默认读写下的单包只读例外）。

## 规则口径

1. **矩阵调整权**：只有节点归属单位的账号能在授权矩阵里调整区间（PUT
   `/api/security/matrix`）。跨单位账号一律只读：调矩阵返回 `403 cross_unit_readonly`；
   即便矩阵配置成 `read_write`，解析结论也封顶为 `read`（尝试直接授予外单位读写返回 422）。
2. **继承与例外**：沿「包 → 区 → 组」归属链取最近的显式授权。
   - 本单位账号默认 `read_write`，显式区间只能收窄（只读/无权限例外）；
   - 跨单位账号默认 `none`，显式授权封顶只读；
   - 单个样品包可挂只读例外（种子 P3 对李勘即为只读）。
3. **越权写拒绝**：修改采样点位（PUT `/api/geochem/{id}/point`）和样品动作都需要包级写权限。
   - 无任何授权：按 404 处理（样品整行不可见）；
   - 只读/跨单位：403，错误文案只含包号，**不回显样品类型、点位提交值等字段**；
   - 跨单位只读可见时，`样品类型/分析元素/检测方法/检出限` 统一打码或移除。
4. **三处同源**：`effective_access()` 是唯一事实源，三处投影都从它派生：
   - 台账 `GET /api/security/projections/ledger`
   - 点位图 `GET /api/security/projections/map`
   - 样品袋追溯待办 `GET /api/security/projections/bag-todo`

   `GET /api/security/projections/consistency` 对每个样品包核对三处结论一致；
   同一包在三处的 `fingerprint` 完全相同。
5. **回填与缓存**：矩阵调整成功后，命中区间（含下级继承链路）的历史共享引用
   （`geochem_share_reference`）回填 `current_level/backfill_*/stale`；
   `created_by/created_at/snapshot_level` 永不改写——**历史经手关系仍归原账号**；
   同时定向失效子树的授权缓存（返回 `cache_invalidated: true`）。
6. **调班/撤权定序**：两类操作共享目标账号的 `lifecycle` 时间线，按 `op_at` 排序，
   迟到操作返回 `409 order_stale`（定序判定先于单位校验）。成功后抬升目标账号
   `auth_epoch`，其旧会话写操作一律 `409 session_stale`，必须先调用
   `POST /api/security/sessions/resync` 重新同步才能继续提交；撤权则直接终止全部会话。
   账号不得给自己调班。

## 会话模型

- `POST /api/security/sessions` 登录，返回 `session_id` 与 `auth_epoch`；
- 所有化探/权限接口都要带 `X-Session-Id`；读接口允许旧快照，写接口强制校验 epoch；
- 前端 `writeJson()` 遇到 `409 session_stale` 会自动 resync 并重试一次，其余错误码透传。

## 主要接口

| 方法/路径 | 说明 |
| --- | --- |
| `POST /api/security/sessions` / `.../resync` | 登录 / 旧会话重新同步 |
| `GET  /api/security/hierarchy` | 三级归属树 + 单位 + 账号 |
| `GET/PUT /api/security/matrix` | 授权矩阵（跨单位只读；PUT 支持 `expected_version` 乐观锁、`delete` 撤销） |
| `GET  /api/security/references` | 历史共享引用（含回填列） |
| `POST /api/security/reassign` / `.../revoke` | 调班 / 撤权（`op_at` 定序） |
| `GET  /api/security/projections/{ledger,map,bag-todo,consistency}` | 三处同源投影与校验 |
| `GET  /api/security/audit` | 权限审计轨迹（调整/拒绝/调班/撤权/越权点位尝试） |
| `GET  /api/geochem`、`GET /api/geochem/{id}` | 台账列表/明细（过滤 + 打码） |
| `POST /api/geochem/{id}/actions` | 送样检测、登记结果、发起复检（需写权限） |
| `PUT  /api/geochem/{id}/point` | 修改采样点位（需写权限，越权 403） |

## 测试

```bash
cd backend
PYTHONPATH=. python3 -m unittest tests.test_security -v
```

17 个用例覆盖：未登录拒绝、默认归属、矩阵跨单位 403、外单位封顶只读、
三级继承、单包只读例外、越权改点位、字段打码/404 不泄露、三处同源、
回填与缓存失效、乐观锁冲突、调班/撤权定序与旧会话失效。
