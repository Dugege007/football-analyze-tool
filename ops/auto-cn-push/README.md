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

- 已推边界是从最早的已推标记，或已核对的无场次证据，连续走到的最后一天，不是日期最大的已推日。
- 从这条边界的次日查到今天。无场次日写成 `checked_no_matches_<日>.json`，这不是已推标记；库文件的大小和 mtime 没变时不再重复查询。
- 过去未齐套的日子记入 `markers/gap_ledger.json` 并继续告警，不标已推，也不挡住更晚已经齐套的日子。整库里含有缺口日的行，不表示那个日子齐套。
- 今天还没齐套不标已推。未来赛程可以留在快照里，不会被标成已推。
- 已经有 `pending_push.json` 时不重新打包，不改 staging。半截 JSON 会隔离到 `markers/quarantine/`，原字节保留，不当作已覆盖，也不另起一份 pending。
- 打包用固定 gzip 时间，同一库字节重复打包得到同一个 gz。若远端库哈希已经和 pending 的 db 一致、但 gz 不同，不能改写旧回执的 gz。须另有一份 `REMOTE_DB_CONFIRM.json`：远端 `sha256sum` 与 pending 的 db 一致、健康检查为 `"ok": true`，并原样引用旧回执的 gz。这样才允许标记，且 `push_again` 为假。
- MSI 回执的 db 和 gz 都与 pending 一致、而 YuePC 仍是旧回执时，可以标记，不重推。
- ready 或包的哈希对不上当前 pending 时不推、不标记。

齐套仍只调用原来的 `check_close_complete.py`：平博亚盘 `rule/close` 且盘口不为空；竞彩列表已撤下的场次继续排除。澳门盘不能把缺平博临盘的日子算成齐套。正式库只用只读 online backup。

## 同库异 gz 与 Windows 脚本

现网 `push-formal-db.ps1` 在 db 哈希相同就删掉 ready 并退出，不产生这次 gz 的回执。仓库里的副本已改成：先读远端库哈希；相同则在健康检查通过后写 `REMOTE_DB_CONFIRM.json`，不改 `LAST_PUSHED`；不同才上传，上传后健康失败不写回执。PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM，box 读回执时用 `utf-8-sig`。仓库副本写回执时不再加 BOM。

这些 Windows 改动，包括 `pending_expect.json`、`push_claim.json` 和「禁用任务后等待运行结束」，都还没有装到 YuePC 或 MSI。在覆盖现网脚本之前，不能把它们说成已经生效的生产防护。

在此之前，协议靠 box 生效：把远端 `sha256sum` 和健康结果写入 `REMOTE_DB_CONFIRM.json`，不要改旧回执的 gz。`python3 ops/auto-cn-push/run_auto_cn_push.py --mark-pushed <日> --receipt <旧 LAST_PUSHED.json> --remote-confirm <REMOTE_DB_CONFIRM.json>` 只在确认文件引用的 gz 与旧回执一致时标记。同一 pending 的 `push_claim.json` 同时只允许一台机器；这是 box 上的约束，现网推送脚本还不会读它。

11-20 的 MSI 回执不能用来给 11-12 至 11-19 补造已推标记。`migrate_receipt.py` 在没有与回执 db 哈希一致的历史快照时只写 `MIGRATION_NEEDS_EVIDENCE.json`。有那份快照时，按快照上的逐日齐套结果写标记，并注明证据来自历史快照，不是当前库。快照哈希对不上就拒绝。

## 安全迁移

附件里的推送脚本把密钥路径和 SSH 目标改成了 `FAT_CN_SSH_KEY`、`FAT_CN_SSH_TARGET`。这两个名字不是现网脚本正在读的变量。本仓库没有这些值。

默认不要用仓库副本覆盖 YuePC / MSI 上的现有推送脚本。若以后改用副本，只在那台机器的计划任务或不入库文件里注入这两个变量，值从现网脚本现有默认值抄取。`env.example` 只说明键名，cron 不会 source 仓库里的这个文件。

循环和看门狗共用仓库外的配置文件，由 `AUTO_CN_PUSH_ENV` 指向。文件里必须有已存在的 `APP_DB_PATH`。没设或路径不对时写 `markers/ALERT_CONFIG.json` 并以非 0 退出，不会去打开脚本里的默认库。`check_close_complete.py` 与现网原件哈希一致：`d22aeb9ff120a8031351a1452d3d482680d0de2a3be5f22e7a33857644200c51`。

## 安装、核对、回滚

目标如果是现网目录，安装和回滚都会拒绝，除非同时设置 `AUTO_CN_PUSH_ALLOW_PRODUCTION_INSTALL=1`。本次没有设置它。`crontab -l` 失败且不是「没有 crontab」时，安装和回滚都会停止，不会用空表覆盖已有任务。安装前把不含本看门狗行的任务表备份到 `logs/crontab.before-install`，写回后核对原任务还在。

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

回滚在备份校验通过后恢复安装前的任务表，不删除 pending、staging 和标记，也不改 Windows 脚本。`logs/ROLLBACK_DONE.json` 记下这些没动过的部分。旧的 `install_box_cron.sh` 默认拒绝。

## 仍未在生产生效

- 仓库里的 Windows 推送、安装锁、`pending_expect` 和 `push_claim` 检查都还没部署。现网脚本仍可能在 db 相同但 gz 不同时删掉 ready 并不写确认文件。
- 11-12 至 11-19 若没有与 11-20 回执哈希一致的历史快照，不能标成当时已推。可以另做一份新快照，按新协议确认，但那是新证据。
- 两台 Windows 机器的互斥要等推送脚本升级后才会在投放目录里生效。在此之前只靠 box 上的 claim，现网任务不会读它。

## 测试

```bash
python3 ops/auto-cn-push/test_protections.py
```

测试使用临时目录、临时 sqlite、假钟、假 crontab 和假启动器，不打开正式库，不连接大陆站。
