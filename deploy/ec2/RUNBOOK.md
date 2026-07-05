# 上线 Runbook（Phase 15，2026-07-05）

> **怎么用**：按章节顺序做，每步末尾有"✅ 成功标志"——看到它再进下一步。
> §1 是只有你能做的一次性事项；§2 起照抄命令/点击路径即可。
> 预计总耗时：首次 2-3 小时（其中 DNS 生效等待约 10-30 分钟）。

**目标拓扑**（2026-07-05 用户裁决,方案可行,无修改建议——附一条注记见 §3）：

```
pdf.xiao-projects.com      → Vercel（前端，Vercel 自动 SSL）
api.pdf.xiao-projects.com  → EC2 弹性 IP → nginx(443, Let's Encrypt) → api:8000
                                          → celery_worker ↔ redis（容器内网）
```

**月度成本预估**：EC2 t3.medium ~$34 + EBS 50GB gp3 ~$4 + 弹性 IP $0（绑定运行实例免费）
+ CloudWatch ~$1-3 + 域名 ~$1/月摊销 + Vercel Hobby $0 ≈ **$40-42/月**。

---

## §1 一次性事项（只有你能做，约 30 分钟）

### 1.1 AWS 账号
1. https://aws.amazon.com → 创建账号（需信用卡）。
2. 登录后右上角账号名 → **Security credentials** → 给 root 用户启用 **MFA**（手机 Authenticator 扫码）。
3. 左上搜索框输入 **IAM** → 用户 → 创建用户：名 `xiao-admin`，勾选"提供对 AWS 管理控制台的访问权限"→ 附加策略选 **AdministratorAccess** → 创建。之后日常都用这个用户登录，root 收起来。

✅ 成功标志：能用 xiao-admin 登录 Console，右上角显示区域选择器。
**把区域切到你选定的区（建议 ap-northeast-1 东京，后文以此为例）——之后所有操作都在这个区。**

### 1.2 域名购买
1. 任选注册商（Namecheap / Cloudflare Registrar / 阿里云均可）搜索并购买 `xiao-projects.com`（~$10-12/年）。
2. 不需要买任何附加服务（SSL/隐私保护通常免费送）。
3. 找到该域名的 **DNS 管理页**（后面 §3 要在这里加两条记录），先放着。

✅ 成功标志：注册商后台能看到 xiao-projects.com 的 DNS 记录管理界面。

### 1.3 Vercel 账号
1. https://vercel.com → **Sign up with GitHub**（授权你的 GitHub 账号）。

✅ 成功标志：Vercel Dashboard 能看到你的 GitHub 仓库列表。

---

## §2 创建 AWS 资源（Console 点击路径，约 20 分钟）

### 2.1 IAM Role（给 EC2 写 CloudWatch 日志的权限）
1. Console 搜索 **IAM** → 左栏 **角色** → **创建角色**。
2. 可信实体类型：**AWS 服务**；使用案例：**EC2** → 下一步。
3. 权限策略搜索框输入 `CloudWatchLogsFullAccess` → 勾选 → 下一步。
4. 角色名称填 `pdfcompress-ec2-role` → 创建角色。

✅ 成功标志：角色列表出现 pdfcompress-ec2-role。

### 2.2 EC2 实例
1. Console 搜索 **EC2** → 橙色按钮 **启动实例**。
2. 名称：`pdfcompress-prod`。
3. AMI：选 **Ubuntu**，版本 **Ubuntu Server 24.04 LTS (HVM), SSD**（64 位 x86）。
4. 实例类型：搜索选择 **t3.medium**。
5. 密钥对：**创建新密钥对** → 名 `pdfcompress-key`，类型 RSA，格式 .pem → 创建（浏览器自动下载 pdfcompress-key.pem，**存好，丢了进不去**）。
6. 网络设置 → 编辑：
   - 勾选"允许来自…的 SSH 流量"，下拉框改成 **我的 IP**；
   - 勾选"允许来自互联网的 HTTPS 流量"；
   - 勾选"允许来自互联网的 HTTP 流量"。
7. 配置存储：改成 **50** GiB，类型 **gp3**。
8. 高级详细信息 → **IAM 实例配置文件** → 选 `pdfcompress-ec2-role`。
9. 启动实例。

✅ 成功标志：实例列表中 pdfcompress-prod 状态"正在运行"，状态检查 2/2 通过（等 1-2 分钟）。

### 2.3 弹性 IP
1. EC2 左栏 **弹性 IP** → **分配弹性 IP 地址** → 分配。
2. 选中新 IP → **操作** → **关联弹性 IP 地址** → 实例选 pdfcompress-prod → 关联。
3. **记下这个 IP**（下文记作 `<EIP>`）。

✅ 成功标志：实例详情页"弹性 IP 地址"一栏显示 <EIP>。

---

## §3 DNS（在你的域名注册商 DNS 管理页，约 5 分钟 + 生效等待）

| 类型 | 主机名 | 值 | TTL |
|------|--------|-----|-----|
| A | `api.pdf` | `<EIP>` | 自动/300 |
| CNAME | `pdf` | `cname.vercel-dns.com` | 自动/300 |

> 注记（域名方案审查,你的关注点 4）：pdf.xiao-projects.com + api.pdf.xiao-projects.com
> 方案没有问题——api.pdf 是普通三级域名,A 记录即可;Let's Encrypt 单域名证书
> 免费无限续;apex 域留给未来主页。唯一提醒:未来若子域数量多,可换 Cloudflare
> 托管 DNS 统一管理（免费,注册商改 NS 即可,不影响本次部署）。

✅ 成功标志（本机终端）：`nslookup api.pdf.xiao-projects.com` 返回 <EIP>。
（未生效就等 10-30 分钟,期间可以先做 §4。）

---

## §4 SSH 首次配置（约 10 分钟）

本机 PowerShell（.pem 所在目录）：

```powershell
# Windows 需先收紧密钥权限，否则 ssh 拒绝使用
icacls .\pdfcompress-key.pem /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i .\pdfcompress-key.pem ubuntu@<EIP>
```

登进去之后：

```bash
curl -fsSL https://raw.githubusercontent.com/Xiao-Y-09/Portfolio_PDF_Compressor/main/deploy/ec2/setup.sh | bash
# 脚本做五件事：apt 更新+Docker / 2GB swap / clone 到 /opt/pdfcompress / 生成 .env / nginx 引导配置
exit   # 必须退出重登，docker 组权限才生效
ssh -i .\pdfcompress-key.pem ubuntu@<EIP>
docker ps   # 能执行不报权限错误即可
```

✅ 成功标志：`docker ps` 正常输出（空列表也算）；`ls /opt/pdfcompress` 能看到仓库内容。

---

## §5 配置 .env（2 分钟）

```bash
nano /opt/pdfcompress/deploy/.env
```

改成：

```
CORS_ORIGINS=["https://pdf.xiao-projects.com"]
AWS_REGION=ap-northeast-1
```

✅ 成功标志：`cat /opt/pdfcompress/deploy/.env` 内容如上。

---

## §6 部署后端（约 15 分钟，含镜像构建）

### 6.1 首次启动（HTTP 引导模式）
```bash
cd /opt/pdfcompress/deploy
docker compose up -d --build          # 首次构建 3-5 分钟
docker compose ps                     # 四个服务 running/healthy
curl http://localhost:8000/api/v1/health
```
✅ 成功标志：health 返回 `{"status":"ok","redis_connected":true,"storage_writable":true}`；
浏览器访问 `http://api.pdf.xiao-projects.com/api/v1/health`（注意 http）同样返回。
（这一步失败先查 §3 DNS 是否已生效。）

### 6.2 签发 SSL 证书（Let's Encrypt）
```bash
docker compose --profile certs run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d api.pdf.xiao-projects.com \
  --email 你的邮箱 --agree-tos --no-eff-email
```
✅ 成功标志：输出 `Successfully received certificate`。

### 6.3 切换到 SSL 配置
```bash
cp /opt/pdfcompress/deploy/nginx/api.ssl.conf /opt/pdfcompress/deploy/nginx/active.conf
docker compose restart nginx
curl https://api.pdf.xiao-projects.com/api/v1/health
```
✅ 成功标志：**https** 的 health 返回 ok；浏览器访问显示锁形图标。

### 6.4 启用 CloudWatch 日志
```bash
docker compose -f docker-compose.yml -f docker-compose.cloudwatch.yml up -d
```
✅ 成功标志：Console → CloudWatch → 日志组，出现 `/pdfcompress/api`、
`/pdfcompress/worker`、`/pdfcompress/nginx` 三个组且有日志流入。
（随后按 monitoring.md 把每组 Retention 设为 30 天。）

### 6.5 自动续签（做一次，永久生效）
```bash
crontab -e   # 首次选 1 (nano)，文件末尾加一行：
0 4 * * 1 cd /opt/pdfcompress/deploy && docker compose --profile certs run --rm certbot renew && docker compose exec nginx nginx -s reload
```
✅ 成功标志：`crontab -l` 显示该行。手动演练：把上面 cron 命令整行复制执行一次，
输出包含 `not yet due for renewal` 即链路通畅（证书 90 天有效，30 天内自动续）。

---

## §7 部署前端到 Vercel（约 10 分钟）

1. Vercel Dashboard → **Add New… → Project** → 选 `Portfolio_PDF_Compressor` 仓库 → Import。
2. **Root Directory** 点 Edit → 选 `frontend`。
3. 展开 **Environment Variables** → 添加：
   - Name `NEXT_PUBLIC_API_BASE`，Value `https://api.pdf.xiao-projects.com`。
4. **Deploy**（约 2 分钟）。
5. 部署完成 → 项目 **Settings → Domains** → 输入 `pdf.xiao-projects.com` → Add。
   （§3 的 CNAME 已配好,Vercel 会自动验证并签自己的 SSL。）

✅ 成功标志：浏览器访问 https://pdf.xiao-projects.com 显示"作品集 PDF 压缩"首页。

---

## §8 公网端到端验证（上线判定）

1. 打开 https://pdf.xiao-projects.com。
2. 选 10MB / 屏幕查看，上传一份真实作品集（建议先用 30-70MB 的）。
3. 进度条走到 40% 出现 review 网格（缩略图能显示 = 后端会话正常）。
4. 随意改一页分类、标一页跳过 → 确认并继续。
5. 等待完成 → 显示最终大小与保真等级 → 下载,PDF 能正常打开。
6. Console → CloudWatch → Logs Insights → 选 `/pdfcompress/worker`,跑
   monitoring.md 的"tier 分布"查询,能看到刚才这单的 task-metrics。

✅ 成功标志：全程无错误页;下载文件有效;CloudWatch 查得到指标。**到这里即上线。**

### 告警（monitoring.md 详表，两条必配）
1. Console → CloudWatch → 告警 → 创建告警 → 选择指标 → EC2 → 按实例 →
   pdfcompress-prod 的 **CPUUtilization** → 阈值 静态 > 90,持续 5 分钟 →
   新建 SNS 主题填你的邮箱（会收确认邮件，要点确认）。
2. 同路径再建 **StatusCheckFailed** ≥ 1,告警操作附加"恢复此实例"。

---

## §9 日常运维

| 事项 | 命令/路径 |
|------|----------|
| 发布新版本 | SSH 后：`cd /opt/pdfcompress && git pull && cd deploy && docker compose -f docker-compose.yml -f docker-compose.cloudwatch.yml up -d --build` |
| 看实时日志 | `docker compose logs -f celery_worker`（或 api/nginx） |
| 服务异常重启 | `docker compose restart <服务名>`；全部重来 `docker compose down && up -d` |
| 磁盘占用检查 | `df -h /` + `docker system df`；镜像清理 `docker image prune -f` |
| 升级实例 | instance-config.md（停机 → 改类型 → 启动,EIP 不变） |
| 内存疑似不足 | `free -h` 看 swap 使用;持续吃 swap → 升 t3.large |

### 故障速查

| 症状 | 先查 |
|------|------|
| 前端能开、上传报"无法连接服务器" | CORS：`.env` 的 CORS_ORIGINS 是否 https 且与前端域一致 → `docker compose up -d` 重载 |
| health 的 redis_connected=false | `docker compose ps` redis 是否 healthy → `docker compose restart redis` |
| 上传 413 | 500MB 上限（预期行为）;若文件确实 <500MB,查 nginx active.conf 是否 ssl 版 |
| review 缩略图全部"预览不可用" | 会话已超时（30 分钟）,重新上传;或 worker 挂了看日志 |
| 证书到期告警 | 手动执行 §6.5 的 cron 行,看报错;LE 每 90 天续,cron 每周一 4 点跑 |

### CI/CD（手册第九项——只记录,暂不实现）
GitHub Actions：push main → build 镜像 → 推 ECR → SSH 到 EC2 拉取重启。
当前手动 `git pull + compose up --build` 已够用;等发布频率上来再上。
