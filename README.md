# 地质勘探数据管理平台

面向地质勘探的钻孔编录、岩心取样、物探数据、化探分析、测绘资料与储量估算的综合数据管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 钻孔编录 | `borehole` | 钻孔 | 钻孔编号、勘探区、孔口坐标 |
| 岩心管理 | `core` | 岩心样本 | 岩心编号、所属钻孔、取样深度起 |
| 地层划分 | `stratigraphy` | 地层单元 | 单元编号、钻孔编号、地层名称 |
| 地球物理 | `geophysics` | 物探测线 | 测线编号、勘探区、物探方法 |
| 化探分析 | `geochem` | 化探样品 | 样品编号、样品类型、采样点位 |
| 化验数据 | `assay` | 化验结果 | 化验编号、样品编号、元素名称 |
| 地质填图 | `mapping` | 填图单元 | 图幅编号、图幅名称、比例尺 |
| 测绘控制 | `survey_point` | 控制点 | 点号、点类型、坐标X |
| 钻探日志 | `drilling_log` | 钻探记录 | 日志编号、钻孔编号、钻进深度 |
| 储量估算 | `reserve` | 矿体块段 | 块段编号、矿体名称、面积 |
| 样品登记 | `sample_registry` | 送检样品 | 送检编号、样品名称、采样位置 |
| 勘探设备 | `equipment` | 勘探仪器 | 仪器编号、仪器名称、型号规格 |
| 水文地质 | `hydro` | 水文观测点 | 观测编号、观测类型、所在钻孔 |
| 剖面编录 | `section` | 实测剖面 | 剖面编号、剖面名称、剖面长度 |
| 地质报告 | `geological_report` | 勘探报告 | 报告编号、勘探区、报告类型 |
| 遥感解译 | `remote` | 遥感数据 | 数据编号、数据源、分辨率 |
| 矿产评价 | `mineral` | 矿化线索 | 线索编号、勘探区、矿种 |
| 环境地质 | `environmental` | 环境调查点 | 调查编号、调查区域、灾害类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 化探样品三级权限（项目组—采样区—样品包）

化探样品的归属链为「采样区 → 项目组 → 样品包」，权限规则集中在
`backend/app/access/`：

- **授权矩阵**：只有区间所属单位的账号能在矩阵里调整档位；跨单位账号一律只读。
  项目组默认继承父级采样区权限，单个样品包可挂 `readonly` 只读例外。
- **字段保护**：跨单位账号读取化探样品时，样品类型、分析元素、检测方法、检出限、
  分析日期统一脱敏为 `******`；无授权样品按 404 处理，不泄露其存在与字段。
- **点位写保护**：越权修改采样点位直接返回拒绝；只读例外与跨单位账号都不能改。
- **三处同源**：化探台账、点位图、样品袋追溯待办由
  `GET /api/geochem/access-view` 的同一份快照投影（同一 `decision_version`）。
- **回填与缓存**：授权调整后自动回填历史共享引用（经手人 `shared_by` 保留原账号），
  并递增 `decision_version`、清空授权结论缓存，可通过
  `GET /api/access/cache-status` 观测。
- **调班/撤权定序**：`/api/access/handover` 与 `/api/access/grants/{id}/revoke`
  按请求携带的 `op_time` 单调定序，迟到操作判 409 冲突并立即关闭其旧会话，
  旧会话再提交一律 401。时序日志见 `GET /api/access/audit`。

所有化探接口与授权接口都要带 `X-Session-Token`，令牌由
`POST /api/access/sessions`（登录）获取。种子账号：

| 账号 | 单位 | 角色 |
| --- | --- | --- |
| `admin.a` | UNIT-A 第一地质大队 | 本单位管理员 |
| `li.gong` | UNIT-A | 采样员，甲山采样区读写 |
| `wang.gong` | UNIT-A | 采样员，甲山读写但北坡1号包只读例外 |
| `partner.b` | UNIT-B 兄弟协作单位 | 跨单位（对 UNIT-A 只读脱敏） |
| `inspector.c` | UNIT-C 上级督查单位 | 第三方，对 A/B 均跨单位只读 |

### 后端测试

```bash
cd backend
.venv/bin/python -m unittest tests.test_access -v
```

