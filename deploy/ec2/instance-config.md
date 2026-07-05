# EC2 实例配置（手册 Phase 15 第六项 + 2026-07-05 用户裁决）

## 起步配置（用户选定）

| 项 | 值 | 说明 |
|----|----|------|
| 实例类型 | **t3.medium**（2 vCPU / 4GB） | 约 $34/月（东京区，按需）；能跑但性能预算不宽裕 |
| AMI | Ubuntu Server 24.04 LTS (64-bit x86) | setup.sh 按 Ubuntu 编写 |
| EBS | gp3 **50GB** | 系统 ~10GB + Docker 镜像 ~2GB + 临时工作区（单任务峰值 ~3GB×并发数） |
| 安全组 | 22（仅你的 IP）/ 80/443（0.0.0.0/0） | SSH 永不对公网开放 |
| IAM Role | CloudWatch Logs 写权限 | awslogs 日志驱动直连（无需装 Agent） |
| 弹性 IP | 1 个（绑定实例） | api 子域 A 记录指向它；实例重启 IP 不变 |

## worker 并发与内存预算

- t3.medium：celery `--concurrency=1`（默认值，celery.Dockerfile 已写死）。
  单份 100MB 级作品集处理峰值内存可达 1.5-2GB（extract 像素采样 + 整页渲染），
  4GB 内存跑 2 并发有 OOM 风险。setup.sh 配了 2GB swap 作溢出保险。
- 手册原建议 worker 2 副本——按 t3.medium 起步档下调为 1（披露性偏离）；
  升级实例后用 `docker compose up -d --scale celery_worker=2` 扩容。

## 升级触发条件（用户关注点 3）

| 症状 | 判定方法 | 动作 |
|------|---------|------|
| 处理 200MB+ 文件 OOM | worker 日志出现 Killed / CloudWatch 内存告警；`dmesg | grep -i oom` | 升级 **t3.large**（2 vCPU / 8GB，约 $67/月） |
| 并发用户多、任务排队久 | CloudWatch：CPU 长期 >80%；任务从提交到完成远超性能基线（p2≈53s 单任务） | 升级 **c6i.xlarge**（4 vCPU / 8GB CPU 优化，约 $156/月）+ worker 扩到 2-3 |
| 磁盘 >80% | CloudWatch 磁盘告警 | EBS 在线扩容（不停机）：Console → Volumes → Modify |

升级实例类型操作：Console → EC2 → 选中实例 → 实例状态 → 停止 →
操作 → 实例设置 → 更改实例类型 → 启动。弹性 IP 不变，无需改 DNS。

## 性能对照基线（Phase 13，本地开发机，作相对参考）

| 样本 | 大小 | 全流水线单轮 |
|------|------|------|
| portfolio_1 | 74.8MB | 100.3s |
| portfolio_2 | 103.8MB | 53.4s |
| portfolio_3 | 34.7MB | 10.6s |

t3.medium 单核性能低于开发机，预期 1.5-2.5 倍耗时；若实测超 3 倍，检查
swap 是否被频繁使用（内存不足的信号）。
