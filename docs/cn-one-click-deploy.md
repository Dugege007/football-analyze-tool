# 大陆站一键发布

本文是操作说明。方案里的开放问题按下面的决定实现，业务页面和接口逻辑不在这次改动里。

## 决定

- 实现语言是 Node.js。Windows 入口仍是双击 `deploy.bat`。
- 只允许主机 `124.221.56.38`。`81.70.76.42` 和个人站目录会被拒绝。
- 静态站点、接口代码、正式库各保留最近 5 份备份，都放在 `/opt/football-data/backups/` 下（`wwwroot/`、`app/`、`app_latest.db.<时间>`）。这个目录已经在允许写入的前缀里。
- 不读取、不上传、不覆盖 `/opt/football-data/env`。
- 只重启现有单元 `football-analyze-api`，不修改其中的 User。
- 正式库属主设为 `football`，权限 `600`。本机正式库路径只写在 `.env.deploy`，不写进脚本。
- 持续集成里禁止实连服务器。`--dry-run` 只打印计划。

## 在 YuePC 或 MSI 上第一次配置

在仓库根目录：

```powershell
copy .env.deploy.example .env.deploy
```

用编辑器打开 `.env.deploy`，填写 `DEPLOY_SSH_KEY`：

- YuePC：`C:\Users\Administrator\.ssh\id_ed25519`
- MSI：`C:\Users\win10\.ssh\id_ed25519`

公钥需要已经在大陆站的 `authorized_keys` 里。默认 `DEPLOY_MODES=static,api`，不会覆盖远端正式库。要同步正式库时，把模式改成 `static,api,db`，或在命令里加 `--modes static,api,db`，并填写 `DEPLOY_DB_LOCAL_PATH`。MSI 上常见路径是 `D:\Du_Test\football\current\app_latest.db`。YuePC 填这台电脑上的绝对路径，文件名必须是 `app_latest.db`。

`.env.deploy` 已被 Git 忽略。可以提交的只有 `.env.deploy.example`。

## 发布

双击 `deploy.bat`。窗口会先打印目标主机、模式、网站根和应用根。含正式库时会写明「将覆盖远端正式库」。输入 `Y` 后才继续。

先演练、不连服务器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy.ps1 --dry-run
```

其他开关：`--skip-build`（使用已有 `web/dist`）、`--modes static,api,db`、`--yes`（跳过确认，仍会打印主机和模式）。可选指纹文件 `web/dist/.cn-deploy-tip.txt` 存在时会打印，没有也不阻塞。

开发机上可以运行 `npm run deploy:cn:check`。它只做断言和干跑，不会连接服务器。

网页依赖需要事先在 `web/` 里执行过 `npm ci`。服务器不需要安装 Node.js。接口依赖只在 `api/requirements.txt` 变化时，用 `/opt/football-analyze-tool/.venv` 里的 Python 3.12 安装。安装失败则不重启服务。

## 三种模式会上传什么

- `static`：在 `web/` 构建，打包 `web/dist`（必须有 `index.html` 和 `assets/`），备份网站根后覆盖 `/www/wwwroot/124.221.56.38`，再检测并重载 Nginx。
- `api`：同步 `api/app/`、`api/requirements.txt`、`api/resources/name-standard/`、`docs/schema/`，以及 `config/`、`api/config/` 里已提交的非密钥样例和分位表。不上传 `.env`、`.env.deploy`、私钥、`api/tests`、`api/data`、`api/scripts`，也不上传已忽略的 `strategy_params.json`、`kickoff_drift.json`、`leak_suspect.json`。服务器上原来的这些私有文件不会被删除。
- `db`：计算本机 SHA256，上传到同目录的 `app_latest.db.incoming`，远端哈希一致后才停服务、备份、原子 `mv`，再启动。哈希不一致会删掉 incoming，不碰现网库。

## 失败与回滚

某一段失败后，后面的段不再执行。已经成功的段不会自动改回去。脚本会打印备份路径和手工回滚要点：

- 静态：把 `/opt/football-data/backups/wwwroot/<时间>/` 拷回网站根，再重载 Nginx。
- 接口：停服务，把 `/opt/football-data/backups/app/<时间>/` 按相对路径拷回 `/opt/football-analyze-tool/`，再启动。
- 正式库：停服务，用备份文件原子换回 `/opt/football-data/app_latest.db`，属主 `football`、权限 `600`，删掉残留的 `-wal`/`-shm` 后再启动。

个人站 `https://duhongbo.com` 不在本脚本的操作范围内。发布后可以用浏览器看一眼它是否仍然打开，只看，不要对那台机器做任何修改。
