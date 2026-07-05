# 监控与业务指标（手册 Phase 15 第七项 + 用户关注点 6）

日志经 awslogs 驱动进 CloudWatch（`docker-compose.cloudwatch.yml` 叠加层）：
`/pdfcompress/api`、`/pdfcompress/worker`、`/pdfcompress/nginx` 三个日志组。

## 业务指标锚点（后端已埋，worker 日志组）

| 锚点 | 格式 | 用途 |
|------|------|------|
| `stage-metric` | `stage-metric task=<id> stage=<split\|extract\|classify\|preprocess\|converge\|assemble> percent=<p>` | 相邻两条时间差 = 阶段耗时 |
| `task-metrics` | `task-metrics session=<id> tier_used=<tier> final_mb=<mb> rounds=<n> elapsed_s=<s>` | tier 分布 / 处理时长 / 收敛轮数 |
| `tier %s boundary` | orchestrator INFO | 每 Tier 真实装配大小与 gap |
| PhaseError | `[phase:CODE] message` | 错误率统计 |

## Logs Insights 查询（Console → CloudWatch → Logs Insights → 选 /pdfcompress/worker）

**tier 分布（近 7 天）**：
```
fields @message | filter @message like /task-metrics/
| parse @message "tier_used=* " as tier
| stats count(*) by tier
```

**平均处理时长与收敛轮数**：
```
fields @message | filter @message like /task-metrics/
| parse @message "rounds=* elapsed_s=*" as rounds, elapsed
| stats avg(elapsed), pct(elapsed, 95), avg(rounds), count(*)
```

**错误率（按错误码）**：
```
fields @message | filter @message like /PhaseError|CONVERGENCE_FAILED|INVALID_PDF|SESSION_EXPIRED/
| parse @message "[*:*]" as phase, code
| stats count(*) by code
```

**阶段耗时（单任务追踪，替换 task id）**：
```
fields @timestamp, @message | filter @message like /stage-metric task=<TASK_ID>/
| sort @timestamp asc
```

## CloudWatch 告警（RUNBOOK §8 有创建步骤）

| 告警 | 阈值 | 动作 |
|------|------|------|
| CPU 高 | CPUUtilization > 90% 持续 5 分钟 | 邮件（SNS）；反复触发 → 见 instance-config 升级 |
| 磁盘高 | 已用 > 80%（需 CWAgent 磁盘指标或用 EBS 剩余字节替代方案：df cron 报警脚本） | 检查临时文件清理是否正常（铁律 5 三重保障） |
| 状态检查失败 | StatusCheckFailed ≥ 1 | 自动恢复（告警动作选 Recover） |

## 日志保留

Console → CloudWatch → 日志组 → 每组设置 Retention = 30 天（默认永久会累费）。
容器内 stdout 日志由 Docker 管理，无需 logrotate（手册的 logrotate 建议
适用于文件日志方案，本部署走 awslogs 驱动，不适用——披露性偏离）。
