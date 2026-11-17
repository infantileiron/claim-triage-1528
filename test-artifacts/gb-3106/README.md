# 漂移监测测试报告（gb-3106）

## 运行方式

```bash
pip install -r ../../backend/requirements.txt pytest httpx
cd test-artifacts/gb-3106 && python3 -m pytest -v
```

测试使用 SQLite 文件库与进程内假 Redis（`FakeRedis`），不依赖 MySQL/Redis 服务；
生产环境的行锁与 READ COMMITTED 行为由 `backend/src/services/drift.py` 与
`backend/src/worker.py` 中的条件更新保证，本套件验证其状态机与终态不变量。

## 覆盖范围

| 需求 | 测试 |
|---|---|
| 补录报销日期（必填、不晚于今天） | `test_claim_requires_claimed_on` |
| 新记录落入窗口即重算 | `test_claim_window_recompute_on_create` |
| 金额/里程/节假日分布与阈值可复算 | `test_window_metrics_recomputable`（独立复算均值、变化率、门槛并比对存储结果） |
| 无基线窗口待算，基线就绪后重算 | `test_window_without_baseline_then_ready` |
| 连续三窗告警触发一次重训；持续告警不重复触发 | `test_three_consecutive_alerts_trigger_once` |
| 恢复正常后可开启新一轮 | `test_recovery_then_new_round` |
| 更正使依据失效：已排队重训停止发布，旧告警可追溯 | `test_correction_invalidates_queued_retrain` |
| 更正使依据失效：运行中重训停止发布（条件发布失效） | `test_correction_invalidates_running_retrain` |
| 跨窗更正与重训完成的并发竞争（线程级，更正先提交） | `test_race_execute_training_vs_correction` |
| 竞争另一时序：重训先发布则历史不变 | `test_publish_first_then_correction_keeps_history` |
| 窗口 待计算→完成/失败，失败重试不重复触发 | `test_failed_window_retry_no_duplicate_trigger` |
| 迟到记录重算受影响窗口，未覆盖窗口不受影响 | `test_late_record_recomputes_affected_windows` |
| 仅改标签不重算；改金额重算 | `test_label_only_edit_skips_recompute` / `test_amount_correction_recomputes` |
| 页面数据：窗口依据、重算历史、关联重训状态、角色权限 | `test_drift_api_surface` |
| 种子数据带日期且幂等 | `test_seed_demo_dated_and_idempotent` |
| 端到端：seed→手动基线→滚动窗口→自动重训发布→不重复触发 | `test_e2e.py::test_full_demo_day` |

## 并发竞争验证说明

`execute_training`（worker 真实代码路径）以条件更新认领（`queued→running`）与发布
（`running→done`）；更正路径的 `evaluate` 以条件更新取消（`queued/running→cancelled`），
两者在 `drift_state` 单行互斥锁内串行。测试用 `monkeypatch` 放慢 `train()`，
主线程在确认 `running` 后执行跨窗更正，断言终态为 `(cancelled, cancelled)` 且结果未发布；
反向时序断言 `(done, published)` 且更正不再回滚历史。两种时序均不允许出现
“训练已发布但触发已取消”或“训练已取消但触发已发布”的混合态。

## 最近结果

17 passed（含 5 次重复运行的竞争测试，无不稳定）。
