# 临盘齐套后自动打包：循环恢复与历史漏推补打

这里纳入原先只放在 box 上、不在 Git 里的 `auto-cn-push` 脚本，并加上两项保护。齐套口径没有改。采集、赛果、预测、双写和 `LIVE_CAPTURE` 都没有动。

本目录现在还没有安装到现网，也没有对大陆站做过推送。看门狗还没有上线，不能把保护说成已经交付。2026-11-21 21:57 机器重启后旧循环停止，22:11 的例程是按原脚本拉起的。

## 根因

2026-11-11 之后打包循环退出。它只靠自己活着，没有外部看门狗。例程看到 `pending_push.json` 不存在就安静结束，所以网站停更，也没有告警。

`--prepare` 原来只检查昨天和今天。更早的日期里，已经齐套的会被 `stale_incomplete` 跳过，不打包也不告警。已推边界如果取「最大已推日」，中间没标过的日子会被吞掉。2026-11-21 只是用原脚本临时补打了当时最新的齐套日 11-20，并手工拉起循环，这两处都还在。

## 两项保护

看门狗在循环外面。`ensure_auto_cn_push_watch.sh` 由 cron 每 5 分钟调用，不放进 `ensure_auto_cn_push_loop.sh`。

- 循环是否还在，看它有没有拿着 `logs/ensure_auto_cn_push.lock`。pid 文件上的 `kill -0` 不能单独当证据。
- 锁已经放开：循环已死，拉起一份。pid 文件指向的进程如果还活着但启动时间和身份记录不一致，那是 pid 被复用，不向那个进程发信号。
- 锁还被打包循环拿着，心跳新鲜：什么都不做，避免两份循环。
- 锁还在，心跳已经旧，但阶段是打包且子进程还在、没有超过长操作上限：视为合法长打包，不结束、不另起。
- 锁还在但心跳陈旧，或者持锁的不是打包循环：只写 `markers/ALERT_LOOP_WATCHDOG.json`。不结束进程，不拉第二份。

历史补打在 `--prepare` 里，扫描规则单独放在 `protect.py`，看门狗在拉起前也会用同一规则写 `markers/ALERT_COMPLETE_UNPUSHED.json`。循环停着的时候，这个告警不依赖循环自己还在跑。

- 已推边界是从最早的已推标记，或已核对的无场次证据，连续走到的最后一天，不是日期最大的已推日。
- 从这条边界的次日查到今天。无场次日写成 `checked_no_matches_<日>.json`，这不是已推标记。下次扫描仍会再查库，不用主库文件时间跳过 WAL 里的新比赛。
- 过去未齐套的日子记入 `markers/gap_ledger.json` 并继续告警，不标已推，也不挡住更晚已经齐套的日子。整库里含有缺口日的行，不表示那个日子齐套。
- 今天还没齐套不标已推。未来赛程可以留在快照里，不会被标成已推。
- 已经有 `pending_push.json` 时不重新打包，不改 staging。半截 JSON 会隔离到 `markers/quarantine/`，原字节保留，不当作已覆盖，也不另起一份 pending。
- 打包用固定 gzip 时间，同一库字节重复打包得到同一个 gz。db 相同但 gz 不同时不标记，也不改旧回执。要让这次 gz 成为已投递，必须由推送脚本真正上传并写出 db、gz 都一致的 `LAST_PUSHED`。
- 损坏的 pending 会移入隔离目录，并留下 `QUARANTINE_HOLD.json`。在 `--release-quarantine` 写下原因之前，后面的轮次不会覆盖 staging。
- 无场次记录只说明上次查到没有比赛，下次仍会再查库，不用主库文件时间跳过 WAL 里的新比赛。
- MSI 回执的 db 和 gz 都与 pending 一致、而 YuePC 仍是旧回执时，可以标记，不重推。
- ready 或包的哈希对不上当前 pending 时不推、不标记。

齐套仍只调用原来的 `check_close_complete.py`：平博亚盘 `rule/close` 且盘口不为空；竞彩列表已撤下的场次继续排除。澳门盘不能把缺平博临盘的日子算成齐套。正式库只用只读 online backup。

## 同库异 gz 与 Windows 脚本

现网脚本在 db 哈希相同就删掉 ready 并退出。仓库里的推送入口没有 SSH 目标就直接失败，不会改在本机执行；测试要显式设置 `FAT_CN_ALLOW_TEST_LOCAL=1`。准备快照前必须已有可信 `push_base.json`。文件缺失、损坏或没有提交身份时，不发布 staging，也不写 pending。初始基线只能从维护脚本的 MSI 基线导入一次。确认真实 `COMMIT_JSON` 后，在和 prepare 同一把锁里把 `push_base` 推进到该提交。Windows 入口先按包内绑定授权，再提交。

同库但 gz 不同时，仍要先有维护基线，再授权这个 gz，然后由事务校验并提交新回执。基线只能由 `init_server_baseline.sh` 生成：MSI 原回执、对应快照和服务器库文件的 db 哈希必须一致。它不把现场查到的远端哈希塞进前驱，也不写 YuePC 回执。回执 JSON 里已经有 `source=msi` 时仍走这个脚本。

没有 `source` 字段的 MSI 原回执走 `migrate_legacy_msi_baseline.sh`。它不改回执文件，不补写 `source`，也不手填 `commit_id`。操作者用 `--evidence-source msi` 声明证据来源，并用 `--receipt-sha256` 绑定原文件字节。快照和服务器库必须与回执里的 `db_sha256` 一致。`commit_id` 仍由 `msi`、竞彩日、db 哈希和 gz 哈希算出。审计写在 `legacy_msi_baseline_audit.json`，记录原文件哈希、声明来源，以及原文件没有 `source` 字段。YuePC 回执、证据来源不是 `msi`、原文件哈希不符、或快照与服务器不一致，都不写基线。

2026-11-20 的 MSI 原 `LAST_PUSHED` 是 334 字节，sha256 为 `eddd8ca744cf5a6411a876071c2064e436de93f1cdd35b7086fe88e4004fe1e8`。字段只有 `jc_day`、`pushed_at`、`db_sha256`、`gz_sha256`、`backup`。db 哈希 `dd20c0e18359a388d752a6d72ef97c22e92745954b345ad631abd7b8d757fb30` 已与现服务器和 MSI 保留快照核对过。迁移时把这份原文件原样交给脚本，`--receipt-sha256` 就填上面这个文件哈希。不要先改文件再算哈希。

2026-11-21 22:45 的旧循环已经写出 11-21 的旧格式 pending 和 staging，22 场齐套，尚未投递。处理顺序是：

- 把 `pending_push.json` 和整个 `staging/` 按原字节复制到审计目录。不要删掉这些副本，也不要把它们标成已推。
- 活跃的 `pending_push.json` 移出后，`--prepare` 才会按新的 `push_base` 重新打包。留在原地时准备会停在 `pending_exists`，不会改 staging。
- 不要把新的 `expected_commit_id` 写进旧包的 `meta.json` 或 gz 注释。旧包留在审计目录，不能拿去授权。
- 导入这次维护基线后，重新 `--prepare`。新包在准备时写入冻结前驱。11-21 仍未推，直到新的投递写出匹配的回执。

box 上没有 crontab 安装器时，安装器正常停止。不要为了让它退出 0 去写一份假的 cron。调度另查可安装的方式。YuePC 旧任务已停，原回执仍是 11-11。MSI 代码已与 main 对齐。API 和激活钩子还没有上线。

换库使用目标目录里的临时文件再 `os.replace`。随后必须运行生产激活钩子。钩子要报告正在运行的 API 指纹，`fingerprint_source` 必须是 `running_api`。只对磁盘文件做 sha256 不能当成服务已经在跑。事务不把预期哈希放进环境变量。钩子没配置就失败，不换库。激活对不上时先恢复文件，再跑回滚钩子。回滚对不上就写 `CN_PUSH_ALERT.json`，不发成功回执。YuePC 仍优先，MSI 仍是备用。

生产激活钩子是 `cn_running_api_hook.py`。它请求正在运行的 `GET /health/db`。这个接口先核对内部令牌，再确认进程是 `APP_READONLY=1` 且连接以 `mode=ro` 打开，然后才连接数据库。身份是这次只读事务里的全部结构和全部行，用类型标记和长度前缀编码，保留 NULL、整数、浮点、文本和二进制的区别。摘要在一条工作线程里计算，建连、读取和关闭都在这条线程上，不占事件循环。首尾行、页数和行数相同但中间内容不同时，摘要不同。响应里没有文件 sha256。钩子再用只读连接对 `FAT_CN_DB` 算同一份摘要。两边一致，路径是同一个文件，`journal_mode` 为 `delete`，并且旁边没有 `-wal`/`-shm`，才把文件字节的 sha256 写进证明。未授权、可写实例、连不上、摘要不一致、路径不一致，或仍是 wal，都失败，事务按原逻辑回滚。可写实例不能得到 `read_only=true`。钩子不读预期哈希。现有 `/health` 仍只返回版本、双写开关和库标签，不证明读到了哪一份文件。

令牌不写进仓库，也不写进钩子命令。操作者自己生成文件，权限只给运行接口和钩子的用户：

```bash
umask 077
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > /操作者选定的路径/health-db.token
```

接口进程和钩子都读 `FAT_HEALTH_DB_TOKEN_FILE`。未配置、文件为空或请求头 `X-Fat-Health-Db-Token` 不对时，返回 401，不打开数据库，响应里没有库路径。`APP_READONLY` 不是真值时返回 403，同样不打开数据库。代码默认 `APP_READONLY` 关闭，这种实例不能通过激活。

默认地址是操作者给出的本机监听 `http://127.0.0.1:8787/health/db`。要改地址、超时或接口代码目录时，写进已有的钩子命令，不要改事务脚本：

```bash
FAT_CN_ACTIVATE_CMD='FAT_CN_API_URL=http://127.0.0.1:8787/health/db FAT_HEALTH_DB_TOKEN_FILE=/操作者选定的路径/health-db.token FAT_CN_APP_ROOT=/opt/football-analyze-tool/api python3 /操作者选定的路径/cn_running_api_hook.py'
FAT_CN_ROLLBACK_CMD="$FAT_CN_ACTIVATE_CMD"
```

钩子若还放在本仓库的 `ops/auto-cn-push/`，可以不设 `FAT_CN_APP_ROOT`。单独拷走时必须设置，否则 `APP_ROOT_MISSING` 失败。上面的 `/opt/football-analyze-tool` 只是发布文档里的代码同步目录，不是这次读到的 systemd 配置。全表扫描超过默认 60 秒时，把 `FAT_HEALTH_DB_TIMEOUT` 设得更大；超时按失败处理并回滚。

公网 Nginx 不应把这个接口交给外部。这次没有读取现网站点配置，下面只是要加的两处，不能整段覆盖已有的 `/api/`。精确路径直接拒绝；已有的反代里增加清空内部令牌头的一行。钩子直连 `127.0.0.1:8787`，不经过 Nginx：

```nginx
location = /api/health/db {
    return 404;
}
proxy_set_header X-Fat-Health-Db-Token "";
```

只读现场已经核对，钩子和 `/health/db` 仍未部署：用户 `football`，工作目录 `/opt/football-analyze-tool/api`，虚拟环境 `/opt/football-analyze-tool/.venv` 的 Python 3.12.13，`APP_DB_PATH=/opt/football-data/app_latest.db`，`APP_READONLY=1`，`APP_DB_LABEL=live`，日志模式 `delete`，旁边没有 `-wal`/`-shm`。Nginx 的 `/api/` 会去掉前缀。令牌文件、精确拒绝 `/api/health/db`，以及清空 `X-Fat-Health-Db-Token` 还没有装上。EnvironmentFile 的路径这次没有给出。

接口代码里的 `/health/db` 还没有装到现网。钩子命令也还没有写进推送环境。这次没有重启 `football-analyze-api`，没有换 `/opt/football-data/app_latest.db`。

Windows 本机安装和推送使用同一把 `FileShare.None` 文件锁，不要求本机有 `flock`、Git Bash 或 PATH 上的 `python3`。推送用 `ssh.exe` 把原始字节送进远端已有的 bash/flock 事务。缺少 `ssh.exe` 或明确的 Python 时退出码 20，输出 `DEPENDENCY_MISSING`，不会报成锁冲突。不要安装 OpenSSH Server，不要开端口，也不要安装未知的 `flock.exe`。WSL 不是两台机器的默认路径。

成功时打印「辅助锁检查通过」。这只说明本机锁、安装/推送入口交错，以及 SSH 失败时不写回执、不删除 ready。它不是远端事务已经跑通。

本环境没有 PowerShell，下面这条命令还没有在 YuePC / MSI 上跑过。请用调用运算符，每个参数单独成词，不要再用 `Start-Process -ArgumentList` 把带空格的路径拼成一条字符串：

```powershell
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\fat-tools\windows_isolation_check.ps1" -DropDir "C:\Program Files\fat drop" -Python "C:\Python314\python.exe"
```

成功时会先打印 `PS51_PARSE_OK` 和「参数回显通过」。stdin 帧检查是另一条命令。分开跑当前 UTF-8 宿主和独立 `-Child` 进程时，两机已经核对过首字节 `7B22`、131 字节和完整 gz。官方合一入口里局部进程变量曾与 `[switch]$Child` 大小写冲突，脚本会在打印 `STDIN_FRAME_OK` 前以退出码 1 结束；局部进程已改名为 `$separateProc`。云端没有执行这些 PowerShell 命令，合一入口仍要在两台机器上再跑一次。`--check-delivery` 不再把未定义的 `confirm` 传给投递判断。

```powershell
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "C:\Program Files\fat-tools\windows_stdin_frame_check.ps1" -Python "C:\Python314\python.exe"
```

YuePC 把 `-Python` 换成那台机器上真实的 Python 3.12.10 可执行文件，不要用 `python3`，也不要落到 `WindowsApps` 存根。MSI 使用 `C:\Python314\python.exe`。最小依赖是 Windows PowerShell、OpenSSH 客户端 `ssh.exe`（不是 Server），以及上面这个明确的 `python.exe`。远端 Linux 仍使用已有的 bash、flock、python3 和 base64，不新增监听服务。

11-20 的回执不能给 11-12 至 11-19 补造已推标记。没有与回执哈希一致的历史快照时，`migrate_receipt.py` 不写已推标记，也不清掉已有缺口。历史判断不使用调用方传来的 live 撤场目录。`--as-current` 只写 `evidence_current_*.json`，不写 `pushed_*.json`。已有不同证据的标记会停住，不覆盖。

## 安全迁移

附件里的推送脚本把密钥路径和 SSH 目标改成了 `FAT_CN_SSH_KEY`、`FAT_CN_SSH_TARGET`。这两个名字不是现网脚本正在读的变量。本仓库没有这些值。

默认不要用仓库副本覆盖 YuePC / MSI 上的现有推送脚本。若以后改用副本，只在那台机器的计划任务或不入库文件里注入这两个变量，值从现网脚本现有默认值抄取。`env.example` 只说明键名，cron 不会 source 仓库里的这个文件。

循环和看门狗共用仓库外的配置文件，由 `AUTO_CN_PUSH_ENV` 指向。文件里必须同时有已存在的 `APP_DB_PATH` 和 `ODDS_DATA_DIR`。缺任何一个就写 `markers/ALERT_CONFIG.json` 并以非 0 退出，不会去打开脚本里的默认库或默认 live 目录。安装和回滚在写入前读两次任务表；这不是比较并交换，维护时不要同时改 crontab。`check_close_complete.py` 与现网原件哈希一致：`d22aeb9ff120a8031351a1452d3d482680d0de2a3be5f22e7a33857644200c51`。

## 安装、核对、回滚

目标如果是现网目录，安装和回滚都会拒绝，除非同时设置 `AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL=1`。本次没有设置它。`crontab -l` 失败且不是「没有 crontab」时停止。写入前如果任务表已经变了，也停止，不整表写回。配置文件必须同时有已存在的 `APP_DB_PATH` 和 `ODDS_DATA_DIR`。

```bash
export AUTO_CN_PUSH_HOME=/tmp/auto-cn-push-install-test
export AUTO_CN_PUSH_CRON_FILE=/tmp/auto-cn-push-cron
export AUTO_CN_PUSH_ENV=/tmp/auto-cn-push.env
# 该文件在仓库外，APP_DB_PATH 必须指向一个已有库
bash ops/auto-cn-push/install_watchdog.sh
bash ops/auto-cn-push/verify_watchdog.sh
bash ops/auto-cn-push/rollback_watchdog.sh
```

`verify_watchdog.sh` 会看配置、循环是否真的健康、以及扫描。循环没在跑是 `VERIFY_LOOP` 退出 2，配置缺失是 `VERIFY_CONFIG` 退出 5，扫描失败是 `VERIFY_SCAN` 退出 6，仍有缺口日是 `VERIFY_GAP` 退出 3。只有这些都正常才打印 `VERIFY_OK`。

回滚只删除带 `# fat-auto-cn-push-watchdog` 的行，不把安装前的整张表写回去，所以不会复活别人已经删掉的任务。写入前如果表已经变了，就停止，不覆盖。不删除 pending、staging 和标记，也不改 Windows 脚本。

## 还没在生产生效

没有合并，没有部署，没有推大陆站。Windows 上的旧推送和旧安装脚本都还在用。因此下面这些都还不能在现网兑现：

- 服务器事务锁、投放目录锁，以及“同库不同 gz 由服务器提交新回执”。旧入口不调用 `invoke_cn_push.sh`，维护时必须停掉旧入口。
- YuePC 仍优先，MSI 仍是备用。两边的仓库副本都进同一条事务；线上副本换掉之前，这条链不生效。
- 11-12 至 11-19 若没有与 11-20 回执哈希一致的历史快照，不能标成当时已推。`--as-current` 只写 `evidence_current_*.json`，不写已推标记。
- 本环境没有 PowerShell，没有执行 `install_package_atomic.ps1`、`push-formal-db.ps1` 或 `windows_isolation_check.ps1`。Windows 本机锁还没有在 YuePC / MSI 上实测。

## 测试

```bash
python3 ops/auto-cn-push/test_protections.py
```

测试使用临时目录、临时 sqlite、假钟、假 crontab 和假启动器，不打开正式库，不连接大陆站。
