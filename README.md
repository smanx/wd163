# wd-panel — 问道 1.63 服务端控制面板

单文件 HTTP 控制面板（`app.py`，兼容 **Python 2.6**/CentOS 6 与 **Python 3**，零第三方依赖），
用于管理问道 1.63 服务端各组件：**mysqld / driver / aaa / dba / ccs / csa / gs**。

功能：
- 面板自身默认启动 HTTP 服务（端口 `8080`），游戏服务由用户在网页上手动启停
- 网页功能：每个服务的 **启动 / 停止**、**运行状态监控**（3 秒自动刷新，绿点=运行中）、
  **日志查看**（实时拉取末尾 200 行）
- **服务器运行状态**：CPU / 内存 / 磁盘 / 负载 / 运行时间 / 进程数 / 监听端口（读 /proc，每 3 秒刷新）
- **服务网络信息**：展示 MySQL `dl_adb_all` 库 9 张服务注册表的 IP/端口（含异常 IP 黄色警告）
- **外部端口配置**：展示各端口用途、是否需要映射到宿主机、实时监听状态
- **查看教程**：内置完整使用教程（启动流程 / IP 配置 / 测试账号 / 数据库 / GM 操作 / 常见问题）
- 依赖检查：未满足依赖的服务不允许启动（如 gs 依赖 ccs/csa/dba/aaa/mysqld）
- 一键"按顺序全部启动 / 全部停止"（按配置文件里的定义顺序处理）

## 1. 目录结构

```
wd-panel/
├── app.py                  # 面板程序（唯一核心文件）
├── services.json           # 生产配置：7 个服务定义 + 端口配置（ports 数组）
├── Dockerfile.game         # 游戏镜像（centos:6，需先提取 VM 文件到 rootfs/）
├── rootfs/                 # 游戏服务端 + MySQL 数据（构建镜像用）
├── _start_chain.py         # 容器内按依赖顺序启动全部服务
├── _restart_all.py         # 容器内重启全部服务
├── _fix_mysql.sh           # MySQL root 密码重置 / 删匿名行（--init-file 方案）
├── _findip.sh / _fixdbip*.sh  # 服务注册表老 IP 扫描与修复（192.168.200.128 → 127.0.0.1）
└── logs/                   # 运行日志与状态文件（自动创建）
```

## 2. 服务定义（services.json）

```json
{
  "title": "问道 1.63 服务端控制面板",
  "services": [
    {
      "name": "aaa",
      "title": "AAA 账号认证 (客户端入口 8101)",
      "deps": ["mysqld"],              // 依赖的服务名
      "dir": "/home/1.6/aaa",          // 启动工作目录
      "start": "bash runaaa",          // 启动命令
      "match": "magic_Linux32.*/aaa/", // 状态探测用的进程匹配（pgrep -f 正则）
      "log": "aaa.log"                 // 日志文件（相对 logs/ 或绝对路径）
    }
    // ... 其余同理
  ],
  "ports": [
    {"port": 8101, "name": "...", "level": "required"}  // required/optional/internal
  ]
}
```

## 3. 运维脚本说明（容器内已内置 _start_chain.py，其余按需 docker cp）

```bash
docker exec wd163 python /opt/wdpanel/_start_chain.py            # 按依赖顺序启动全部服务（AUTO_START=1 时容器启动会自动执行，无需手动）
docker cp _restart_all.py wd163:/tmp/ && docker exec wd163 python /tmp/restart_all.py   # 重启全部服务
docker cp _restart_all.py wd163:/tmp/ && docker exec wd163 python /tmp/restart_all.py   # 重启全部服务
docker cp _fix_mysql.sh  wd163:/tmp/ && docker exec wd163 bash /tmp/_fix_mysql.sh       # 重置 MySQL root 密码（仅数据未持久化时需要）
docker cp _findip.sh _fixdbip.sh _fixdbip2.sh wd163:/tmp/                                # 扫描/修复注册表老 IP（仅数据未持久化时需要）
```

环境变量：
| 变量 | 默认 | 说明 |
|---|---|---|
| `PANEL_PORT` | 8080 | HTTP 端口 |
| `SERVICES_CONF` | ./services.json | 服务定义文件 |
| `LOG_DIR` | ./logs | 日志目录 |
| `PANEL_DB_USER` / `PANEL_DB_PASS` | root / 123456 | 「服务网络信息」查询 MySQL 用的凭据 |

## 4. 游戏服务端镜像（生产）

前提：`rootfs/` 已包含从 VMware 磁盘提取的全部文件（原始提取脚本基于
`../_wd163/_vmdk.py + _extfs.py`），且已直接入库——**克隆仓库即可构建，无需任何额外准备**：

```
rootfs/
├── home/1.6/          # 整个游戏服务端目录
└── var/lib/mysql/     # MySQL datadir（8 个 dl_* 库）
```

构建与运行（**8160 必须映射，客户端进游戏走这个端口**；容器启动后自动拉起全部 7 个服务，无需任何手动步骤）：
```bash
docker build -f Dockerfile.game -t wd163-server .
docker run -d --name wd163 \
  -p 8080:8080 -p 8101:8101 -p 8160:8160 -p 3306:3306 -p 6666:6666 \
  wd163-server
# 不想自动启动服务时：docker run 时加 -e AUTO_START=0，再从网页面板手动启动
```

然后在网页上按顺序启动：**mysqld → driver → aaa → dba → ccs → csa → gs**
（或直接点"按顺序全部启动"），等 gs 日志出现
`Game server allows user to login now.` 后即可进游戏。
注意：gs 就绪后还要再等约 1 分钟 8160 才开始监听。

## 5. 部署说明（生产）

### 5.1 端口需求——哪些端口需要映射

| 端口 | 用途 | 是否映射 | 说明 |
|---|---|---|---|
| **8080** | Web 控制面板 | **必须** | 面板管理入口 |
| **8101** | AAA 账号认证 | **必须** | 客户端登录入口，Login.ini 指向它 |
| **8160** | GS 游戏服务 | **必须** | 客户端选服后连它进游戏，漏映射=永远进不去 |
| 3306 | MySQL | 可选 | 仅外部用 Navicat 等工具管库时映射；不映射更安全 |
| 6666 | Driver 管理 | 可选 | 一般无需映射 |
| 8110 / 8120 / 6101 | CCS / DBA / CSA | **不要映射** | 容器内部经 127.0.0.1 互连，暴露无意义 |
| 9977 / 40225 | driver 内部监听 | **不要映射** | 进程自动开的选择性端口 |

精简映射示例（只保留必需）：
```bash
docker run -d --name wd163 -p 8080:8080 -p 8101:8101 -p 8160:8160 wd163-server
```

### 5.2 部署后验证清单

按顺序逐项验证，全部通过才算部署成功：

1. **容器与端口**：`docker ps` 显示容器 Up，且 `-p` 映射包含 8080/8101/8160
2. **面板可访问**：浏览器打开 `http://服务器IP:8080`，页面正常渲染
3. **服务链启动**：点「按顺序全部启动」，最终 7 个服务全部绿点（约 2~3 分钟）
4. **GS 就绪标志**：gs 日志出现 `Game server allows user to login now.`
5. **端口监听**：「外部端口配置」表中 8101/8160 显示"监听中"（8160 在 GS 就绪后约 1 分钟才监听，属正常）
6. **服务注册表 IP**：「服务网络信息」中 server 表 = 对外 IP（本机玩为 127.0.0.1），内部表（aaa/ccs/csa/dba 等）全部为 127.0.0.1、无黄色记录
7. **TCP 连通性**：客户端机器上 `telnet 服务器IP 8101` / `8160` 能通（防火墙放行）
8. **客户端登录**：启动登录器 → 登录（测试账号见教程）→ 选服（显示"爆满"属正常现象，直接点进入）→ 进游戏
9. **持久化验证**：建个角色 → `docker restart wd163` → 重新启动服务链 → 该角色仍在，即持久化生效

### 5.3 数据持久化——哪些数据需要保存

| 路径 | 内容 | 是否必须持久化 |
|---|---|---|
| `/var/lib/mysql` | **全部游戏数据**：账号、角色、宠物、物品、日志（8 个 dl_* 库），以及我们手工修好的 root 密码和服务注册表 IP | **必须**。丢了=所有玩家数据清零，且重建后要重跑全部数据库修复脚本 |
| `/opt/wdpanel/logs` | 服务运行日志 + `.panel_state.json`（面板进程状态） | 建议保留，丢了只是日志清空、需重新点启动 |
| `/home/1.6` | 游戏服务端程序文件 | 不需要。只读不写，已固化在镜像里 |
| `/etc/my.cnf` | MySQL 配置 | 不需要。已固化在镜像里 |

**重要**：持久化 `/var/lib/mysql` 后，**重建容器无需重跑 `_fix_mysql.sh` 和 `_fixdbip*.sh`**（修复结果已在数据文件里），只需重新 docker cp `app.py` + 启动服务链。

持久化操作（**推荐 named volume 或 WSL 原生路径，不要挂 /mnt/c**——Windows 盘的权限语义会让 mysqld 拒绝启动）：

```bash
# ① 首次从镜像导出 datadir 做种子（镜像里已带提取好的数据）
docker create --name seed wd163-server
docker cp seed:/var/lib/mysql ./mysql-data
docker rm seed
chown -R 27:27 ./mysql-data          # 27 = CentOS6 mysql 用户的 uid

# ② 带持久化运行
docker run -d --name wd163 \
  -p 8080:8080 -p 8101:8101 -p 8160:8160 \
  -v $(pwd)/mysql-data:/var/lib/mysql \
  -v $(pwd)/panel-logs:/opt/wdpanel/logs \
  wd163-server
```

数据备份（建议定期执行）：
```bash
docker exec wd163 bash -c 'mysqldump -uroot -p123456 --all-databases --flush-logs' > backup_$(date +%F).sql
# 或直接停库后整目录打包 mysql-data/
```

### 5.4 跨机器部署的 IP 配置

本机玩无需任何改动；局域网/公网部署要改**两处 IP**（详见面板「查看教程」第二节）：

| 位置 | 用途 | 改成 |
|---|---|---|
| 客户端 `data\Login.ini`（+ `1.63版登录器.bat`） | 客户端连登录入口 8101 | 服务器对外 IP |
| MySQL `dl_adb_all.server` 表 `ip` 字段 | AAA 下发给客户端连 8160 的地址 | 服务器对外 IP（`UPDATE dl_adb_all.server SET ip='对外IP' WHERE id=1;` 改完重启 aaa、gs） |
| `dl_adb_all` 内部注册表（aaa/ccs/csa/dba 等） | 容器内服务互连 | **保持 127.0.0.1 不动**，改了反而全挂 |

### 5.5 容器重建 / 迁移 Checklist

`docker cp` 进容器的补丁**不进镜像**，容器重建后会丢失。重建后按序执行：

```bash
# 1. 恢复面板补丁（若镜像里已是最新版 app.py 则跳过）
docker cp app.py wd163:/opt/wdpanel/app.py && docker restart wd163

# 2. 数据库修复 —— 仅当 mysql-data 未持久化时需要：
#    docker cp _fix_mysql.sh wd163:/tmp/ && docker exec wd163 bash /tmp/_fix_mysql.sh
#    docker cp _findip.sh _fixdbip.sh _fixdbip2.sh（修复注册表老 IP 192.168.200.128）

# 3. 启动服务链（AUTO_START=1 时容器启动已自动完成，此步可跳过）
docker exec wd163 python /opt/wdpanel/_start_chain.py

# 4. 按 5.2 验证清单复查
```

### 5.6 已知注意事项

- 选服时显示「服务器处于爆满状态」是**正常现象**，直接点击仍可登录（选服状态不上报）
- GS 就绪消息 `allows user to login` 出现后，8160 还要约 1 分钟才监听，勿急着判失败
- 容器重启（docker restart / 宿主机重启）后**所有游戏服务是停止状态**，需重跑启动链
- 面板无鉴权，公网部署建议加反向代理 + 密码，或用防火墙限制 8080 来源 IP
- 首次启动若 8101 bind 失败，多为 TIME_WAIT 残留，等 30 秒重试即可

## 6. CI 构建（GitHub Actions → ghcr.io）

`.github/workflows/docker-image.yml` 提供**手动触发**的镜像构建流水线，
构建后自动推送到 GitHub Container Registry。

### 6.1 说明

`rootfs/`（约 227MB 游戏数据，无单文件超过 GitHub 100MB 限制）**已直接提交到仓库**，
CI checkout 后即可构建，不需要上传 Release 附件或任何额外素材。

> 仓库建议设为 **Private**（含闭源游戏文件）。

### 6.2 触发构建

仓库页面 → **Actions** → **Build & Push Image** → **Run workflow**（可填 tag，默认 latest）。

### 6.3 使用镜像

```bash
# 任意 x86 机器上（需 docker login ghcr.io，密码用 GitHub PAT，勾选 read:packages）
docker pull ghcr.io/<用户名>/<仓库名>:latest
docker run -d --name wd163 -p 8080:8080 -p 8101:8101 -p 8160:8160 \
  ghcr.io/<用户名>/<仓库名>:latest
```

说明：
- 仅构建 **linux/amd64**（游戏二进制是 ELF32 i386，ARM 只能 QEMU 模拟，见前述评估）
- 构建缓存用 GitHub Actions Cache，第二次起 yum 层基本秒过
- 首次推送后 ghcr 的包默认 **Private**，别的机器拉取前先 `docker login ghcr.io`
- 数据不随镜像走：部署后按 5.3 节挂载持久化，启动链等按 5.5 节执行

## 7. 实现要点

- 服务进程以**独立进程组**（`setsid`）启动，停止时先 `SIGTERM` 整组、5 秒超时后 `SIGKILL`，
  再用 `pgrep -f <match>` 兜底清理，确保 magic_Linux32 的子进程不残留
- 面板重启不丢状态：pid 记录在 `logs/.panel_state.json`
- 日志：服务 stdout/stderr 统一追加到 `logs/<name>.log`，网页只读、可随时刷新
- 系统信息：直接读 `/proc`（stat/meminfo/loadavg/uptime/net/tcp），CPU 占用率由前端两次采样差值计算
- 网络信息：通过 `mysql` 客户端子进程查询 `dl_adb_all` 注册表，GBK 输出转码后返回 JSON
- API：`GET /api/status`、`GET /api/sysinfo`、`GET /api/netinfo?all=0|1`、`GET /api/log?name=x&lines=200`、
  `POST /api/start|stop|start_all|stop_all`（body `{"name": "...", "force": false}`）

## 8. 已验证（Windows 10/11 + WSL2 Docker + centos:6 容器内实测）

- [x] 面板 HTTP 服务、状态监控、依赖拦截、日志查看（py2.6 容器内运行）
- [x] 按顺序启动 7 个真实服务，GS 完全就绪（`Game server allows user to login now.`）
- [x] 全端口监听验证：6666/8101/8110/8120/6101/8160/9977
- [x] 服务注册表 IP 修复后客户端可正常登录（本机 127.0.0.1 全链路）
- [x] 服务器状态（CPU/内存/磁盘/负载）、网络信息（GBK 服务名解码正常）、端口配置显示
- [x] Windows → 8101/8160 TCP 连通性
- [x] 数据库 IP 修复脚本 / MySQL 密码重置脚本实测有效
