# 临盘齐套后自动打包：循环恢复与历史漏推补打

这里纳入原先只放在 box 上、不在 Git 里的 `auto-cn-push` 脚本，并加上两项保护。齐套口径没有改。采集、赛果、预测、双写和 `LIVE_CAPTURE` 都没有动。

本目录现在还没有安装到现网，也没有对大陆站做过推送。

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

- 已推边界是从最早的 `pushed_*.json` 连续走到的最后一天，不是日期最大的那一个。
- 从边界的次日查到今天。无场次日不标已推，也不把后面的齐套日截断。
- 过去的未齐套日会挡住它后面的日期：整库包可以一次带上连续的齐套日，并在 `coverage` 里写下每天的场次和临盘证据；缺口之后的日期即使库里已经齐套，也不会标成已推。
- 今天还没齐套不挡住更早的齐套日。未来赛程可以留在快照里，不会被标成已推。
- 已经有 `pending_push.json` 时不重新打包，不改 staging。打包失败不写 pending，原来的 staging 还在，下一轮可以重试。
- 标记已推必须有一份回执的 db/gz 哈希和 pending、staging 一致。MSI 回执已经一致而 YuePC 仍是旧回执时，可以标记，并且 `push_again` 为假，不会为了旧回执再推一次。
- ready 或包的哈希对不上当前 pending 时不推、不标记。分块前也会拒绝和 pending 不一致的 staging。

齐套仍只调用原来的 `check_close_complete.py`：平博亚盘 `rule/close` 且盘口不为空；竞彩列表已撤下的场次继续排除。澳门盘不能把缺平博临盘的日子算成齐套。正式库只用只读 online backup。

## 安全迁移

附件里的 `push-formal-db.ps1` 和 `push-formal-db-msi.ps1` 把原来写死的密钥路径、SSH 目标改成了 `FAT_CN_SSH_KEY` 和 `FAT_CN_SSH_TARGET`。这两个名字是脱敏时新加的，现网那两份脚本并不读它们，而是用脚本参数里的原有默认值。本仓库没有、也不应有这些值。

请选一种，不要混用：

1. 继续使用 YuePC 和 MSI 上现有的推送脚本，不要用本目录的副本覆盖它们。打包、看门狗和标记都在 box 上，不依赖这两个变量。这是默认建议。
2. 如果以后改用本目录的副本，在那台机器的计划任务或一个不入库的本地脚本里注入这两个变量。值只从那台机器上现有脚本的参数默认值抄过去，写完不要提交。`env.example` 只保留空值。

投放目录仍默认是现网的 YuePC / MSI 目录，也可以用 `AUTO_CN_PUSH_YPC_DROP`、`AUTO_CN_PUSH_MSI_DROP` 覆盖。`check_close_complete.py` 与现网原件哈希一致：`d22aeb9ff120a8031351a1452d3d482680d0de2a3be5f22e7a33857644200c51`。其余脚本因为加上了保护和参数，哈希不再与 box 原件相同。

## 安装、核对、回滚

下面的命令只写调用方指定的目录和 cron 文件。目标如果是现网目录 `/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020`，安装和回滚都会拒绝，除非同时设置 `AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL=1`。本次没有设置它，也没有执行那条现网命令。

先在临时目录核对：

```bash
export AUTO_CN_PUSH_HOME=/tmp/auto-cn-push-install-test
export AUTO_CN_PUSH_CRON_FILE=/tmp/auto-cn-push-cron
bash ops/auto-cn-push/install_watchdog.sh
bash ops/auto-cn-push/install_watchdog.sh
bash ops/auto-cn-push/verify_watchdog.sh
bash ops/auto-cn-push/rollback_watchdog.sh
```

第二次安装不会多出 cron 行。`verify_watchdog.sh` 成功时打印 `VERIFY_OK`。回滚只删掉带 `# fat-auto-cn-push-watchdog` 的行，不停止已经在跑的打包循环。再执行一次回滚也是成功。

以后若要接到现网，由人在 box 上执行，并先确认循环脚本已经换成带心跳和历史扫描的版本：

```bash
export AUTO_CN_PUSH_HOME=/workspace/football-analyze-tool-ops/auto-cn-push-after-close-20261020
export AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL=1
bash ops/auto-cn-push/install_watchdog.sh
bash ops/auto-cn-push/verify_watchdog.sh
```

这条 cron 会带上同一个允许标志，否则看门狗在现网目录里会拒绝拉起。撤回时用同一组变量执行 `rollback_watchdog.sh`。旧的 `install_box_cron.sh` 会再造一个 prepare 源，默认直接拒绝。

## 测试

```bash
python3 ops/auto-cn-push/test_protections.py
```

测试使用临时目录、临时 sqlite、`AUTO_CN_PUSH_NOW` 和假的启动器，不打开正式库，不连接大陆站。
