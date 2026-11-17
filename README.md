# 禾序 · 报销异常分类复核

财务共享组把历史人工复核标签训练成可解释的逻辑回归版本，再按异常概率排定待核票据；最终判断和签核由人员完成。原生 HTML/CSS/JS 五个业务页面：样本池、训练配置、分类评估、风险复核、漂移监测。

## 真实模拟场景

模拟客户禾序咨询有 180 名顾问，财务共享组 6 人每周核对差旅报销。原流程是导出差旅明细、在工作簿标记疑点、随机抽取票据、在群里确认处理。三个痛点是复核顺序依赖个人经验，模型指标和业务阈值混为一谈，最终判断难以追溯到当时的样本和模型。上游输入报销金额、出行里程、节假日、报销日期和人工历史标签；本项目替代样本整理、风险排序、版本评估、复核记录与分布漂移监测；下游由财务负责人结合原票据作最终处理。本项目不连接真实财务系统，也不自动拒付。

| 角色 | 主要页面 | 权限 | 典型动作 |
|---|---|---|---|
| admin | 全部、签核 | 读写、训练、复核、签核和审计 | 签核已填人工判断的任务 |
| analyst | 样本、训练、评估、复核、漂移 | 读写业务和提交训练，不能签核 | 补标签并生成复核候选 |
| viewer | 五页只读 | 可看与下载，不能写入或训练 | 查看模型、冻结评分与漂移窗口 |

## 一日剧本

1. 08:50 viewer 查看 `HX-1115-001` 等历史样本，初始 318 笔、260 笔有人工标签、58 笔待标注；其中 18 笔 `HX-DRIFT-*` 为近 7 日新票据，金额、里程与节假日占比明显高于历史。
2. 09:00 analyst 录入 `HX-QA-NEW`：报销日期今天、金额 2600 元、里程 180 km、节假日“是”、标签“待标注”；所在窗口随即按“迟到记录”重算。
3. 09:10 对已标注记录填记录 ID 与完整字段，可修正人工标签；已有模型不变。仅改标签不重算窗口，改金额/里程/节假日/日期会重算受影响窗口。
4. 09:20 创建“11月差旅风险基线”，复核阈值 0.5，Redis worker 执行固定 seed 的分层留出训练；此前无基线的窗口以新基线重算。
5. 09:30 查看 Precision/Recall/F1、混淆矩阵及 0.3/0.5/0.7 阈值敏感度。
6. 09:40 生成当前版本高风险待标注样本的复核任务，重复生成不会重复添加。
7. 09:50 漂移监测页可见近七日窗口连续告警，自动触发一次“漂移自动重训”；持续告警不重复触发。
8. 10:00 analyst 复核原票据并填“核验正常”或“需进一步核实”及理由。
9. 10:10 admin 签核人工结论；10:20 viewer 下载 `claim-triage-<版本ID>.json` 留档。
10. 10:30 analyst 发现某笔漂移样本金额录入错误并更正：触发依据失效，排队/运行中的自动重训停止发布；已发布的历史训练不变。

## 算法与口径

输入金额（0–100000元）、里程（0–10000km）、节假日布尔、报销日期（不晚于今天）与人工标签 0/1/空。派生特征“每公里金额”为 amount/(distance+1)，加一避免零里程除零。标签与日期不进入特征矩阵；日期只用于漂移监测窗口归集。

已标注样本至少40笔且每类至少10笔，按标签分层分成75%训练/25%留出（seed=3106）。StandardScaler **只在训练集拟合**；Pipeline 后接 L2 LogisticRegression（C=1，max_iter=1000）。概率为 sigmoid(w·标准化特征+b)，阈值以上预测异常。Precision=TP/(TP+FP)，Recall=TP/(TP+FN)，F1=2PR/(P+R)，分母为零时指标为0。混淆矩阵合计等于留出集样本量。每个版本保留完整输入快照、训练/测试编号、训练集标准化均值、系数、留出概率与待标注概率、sha256 checksum。

阈值敏感度在同一留出集计算。反复选择阈值后，该集合不能继续当成最终独立验收集。本项目用于小规模业务演示，不包含额外生产验收集。标准化系数可解释模型关联，不作为因果结论。类别不足会以 failed 状态保存明确错误；重复编号返回409，无效金额/标签/未来日期返回422。输入修订后须新建训练版本，旧快照不变。任务只对其快照里的待标注样本评分，不提供模型在线部署端点。评分仅排定人工复核顺序。

## 漂移监测与自动重训

**口径（可解释、可复算）**：基线为窗口首次计算时锁定的最新已完成训练快照；窗口为截至结束日的连续 7 天，按日滚动。金额/里程比较均值相对变化 `|(窗口均值-基线均值)/基线均值|`，节假日比较占比差绝对值。门槛：金额 ±35%、里程 ±35%、节假日 ±20 个百分点；任一指标越限即告警。窗口内报销不足 5 笔时不判定漂移（样本不足），记为正常。门槛与方法随每次计算结果保存，任何人可用同一快照与窗口数据复算。

**窗口生命周期**：`pending`（待计算）→ `done` / `failed`。worker 每轮补齐截至今日的窗口并计算待算窗口。失败窗口可在页面重试（置回 pending），重试只产生新的计算版本，不重复触发重训。每次计算写入 `drift_runs`（版本号、原因、基线、指标、告警），窗口重算后旧告警记录仍可追溯。

**迟到记录与更正**：新增报销落入已计算的窗口，或金额/里程/节假日/报销日期被更正时，覆盖该日期的全部七日窗口在同一事务内同步重算（原因分别为 `late-record` / `correction`）。仅修改标签或说明不重算。重算后窗口仍锁定首次计算时的基线版本。

**触发规则**：连续三个按日窗口告警且无开放轮次时，自动提交一次重训（来源标记 `auto`，阈值沿用基线版本，快照为触发时全部样本）。持续告警期间不重复触发；最新已完成窗口恢复正常后轮次闭环，之后可开启新一轮。

**失效取消**：更正或重算使触发依据的任一窗口不再告警时，触发标记 `cancelled`，其排队（queued）或运行中（running）的重训一并置为 `cancelled` 并停止发布结果；已完成的历史训练与已发布触发不受影响。

**并发设计**：MySQL 使用 READ COMMITTED；`drift_state` 单行互斥锁串行化“评估触发”和“发布重训”；worker 以条件更新认领（`queued→running`）与发布（`running→done`），取消同样以条件更新（`queued/running→cancelled`）生效。跨窗更正与重训完成的竞争由数据库行锁定序，两种终态必居其一：取消生效则发布放弃，发布生效则更正不再回滚历史。测试见 `test-artifacts/gb-3106/`。

## 快速启动

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 自动 seed，也可显式执行两次验证
 docker compose exec -T backend python -m scripts.seed_demo
```

前端 http://localhost:18205 ，后端 http://localhost:19205/docs ，MySQL 映射17705。停止并删除演示数据：`docker compose down -v --remove-orphans`。Compose 顶层 name=claim-triage，五个服务各有健康检查；backend/worker 等待db+Redis健康，frontend等待backend健康。数据库、Redis队列、worker产物均有独立命名卷。worker单进程，启动时恢复 queued/running 版本；不承诺多worker exactly-once。

账号为 `admin@demo.local` / `analyst@demo.local` / `viewer@demo.local`，密码均 `Demo123!`，JWT 8小时。密码PBKDF2使用每账号随机盐；JWT_SECRET为演示环境值。按角色后端强制检查，前端只读账号按钮禁用。

## 技术栈和目录

| 层 | 实现 |
|---|---|
| UI | 原生 ES modules、HTML、CSS；Noto Serif SC/Noto Sans SC 双字体 |
| API | Python3.11、FastAPI、Pydantic、SQLAlchemy、PyJWT |
| 算法 | scikit-learn1.6.0、NumPy2.2.0 |
| 异步 | Redis列表队列、独立Python worker与心跳 |
| 数据 | MySQL8.0、Redis7；Nginx反代 `/api/` 去前缀 |

`frontend/src` 下 api/stores/types/components/common/hooks/pages/router/utils/constants 分离网络、状态、契约、组件、表单、页面和枚举。`backend/src` 下 routes/controllers/services/models/schemas/middlewares/utils/constants/config 分离HTTP、查询、算法、持久化、校验、授权和配置；漂移口径与触发状态机位于 `backend/src/services/drift.py`，worker 的窗口滚动与条件发布位于 `backend/src/worker.py`。`backend/scripts/seed_demo.py` 用自然键只插入缺失账号/报销，不覆盖人工修订。`backend/migrations/001_initial.sql` 记录初始架构，`002_drift_monitoring.sql` 记录漂移监测表结构；实际初始DDL由SQLAlchemy metadata建立。

共享枚举 ROLES/STATES/DECISIONS/LABELS/WINDOW_STATES/TRIGGER_STATES/TRAINING_SOURCES 位于后端 `backend/src/constants/enums.py` 和前端 `frontend/src/constants/enums.js`；Pydantic枚举校验位于 `backend/src/schemas/business.py`，权限检查位于 `backend/src/middlewares/auth.py`。状态徽章/表格/表单字段为共享组件，两种中间件提供request ID和安全响应头；全局异常处理与审计记录横切业务。

本地开发：启动Compose的db/redis，将DATABASE_URL和REDIS_URL改指宿主映射；Python3.11虚拟环境安装 backend/requirements.txt，在backend目录 `uvicorn src.main:app --reload`。前端建议继续用Compose Nginx以保留同源API代理，也可使用可代理 `/api` 的本地静态服务器。单独开放静态文件不能替代API代理。

环境变量：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO、NETWORK_SUBNET，均见.env.example。NETWORK_SUBNET默认10.253.106.0/24，可改为本机未用子网。

## 已知范围

逐条表单/API录入和完整字段修订；无CSV/Excel导入、删除、分页或外部审批通知。原始seed为确定性模拟数据，不能推断真实风险。风险复核任务不自动回写训练标签；须人工在样本页确认标签后重新训练。无报销日期的历史记录不进入漂移窗口，补录日期后参与后续重算。漂移窗口基线在首次计算时锁定，不随新训练版本回溯更换。Google Fonts联网加载，离线字体回退。测试、截图和报告在源码外 `test-artifacts/gb-3106/`。

License：MIT，见 LICENSE。
