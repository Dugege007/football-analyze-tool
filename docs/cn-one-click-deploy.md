# 大陆站一键发布

本文是操作说明。方案里的开放问题按下面的决定实现，业务页面和接口逻辑不在这次改动里。

## 决定

- 实现语言是 Node.js。Windows 入口仍是双击 `deploy.bat`。
- 只允许主机 `124.221.56.38`。
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

- `static`：在 `web/` 构建，打包 `web/dist`（必须有 `index.html` 和 `assets/`），备份网站根后覆盖 `/www/wwwroot/124.221.56.38`，写入下面的缓存片段，再检测并重载 Nginx。
- `api`：同步 `api/app/`、`api/requirements.txt`、`api/resources/name-standard/`、`docs/schema/`，以及 `config/`、`api/config/` 里已提交的非密钥样例和分位表。不上传 `.env`、`.env.deploy`、私钥、`api/tests`、`api/data`、`api/scripts`，也不上传已忽略的 `strategy_params.json`、`kickoff_drift.json`、`leak_suspect.json`。服务器上原来的这些私有文件不会被删除。
- `db`：计算本机 SHA256，上传到同目录的 `app_latest.db.incoming`，远端哈希一致后才停服务、备份、原子 `mv`，再启动。哈希不一致会删掉 incoming，不碰现网库。

## 失败与回滚

某一段失败后，后面的段不再执行。已经成功的段不会自动改回去。脚本会打印备份路径和手工回滚要点：

- 静态：把 `/opt/football-data/backups/wwwroot/<时间>/` 拷回网站根，再重载 Nginx。若同一次还改过缓存配置，备份在 `/opt/football-data/backups/nginx/<时间>/`：把 `vhost-*.conf` 拷回原站点配置，把 `fat-static-cache.conf` 拷回片段路径；只有 `snippet.absent` 时删除现有片段。然后 `nginx -t` 与 `nginx -s reload`。
- 接口：停服务，把 `/opt/football-data/backups/app/<时间>/` 按相对路径拷回 `/opt/football-analyze-tool/`，再启动。
- 正式库：停服务，用备份文件原子换回 `/opt/football-data/app_latest.db`，属主 `football`、权限 `600`，删掉残留的 `-wal`/`-shm` 后再启动。

## 免硬刷

Vite 的 `index.html` 引用 `/assets/index-<哈希>.js` 这类带内容哈希的文件。入口文档若被浏览器按启发式规则或很长的 `max-age` 留住，重新打开页面仍会请求旧哈希，看起来就像必须硬刷新。

大陆站和以后同类一键发布都用同一套，和入口短缓存、指纹资源长缓存对齐。片段在 `deploy/nginx/cn-static-cache.conf`：

- `/` 与 `/index.html`（含单页应用回落到入口文档）使用 `expires -1`，响应为 `Cache-Control: no-cache`。
- `/assets/` 使用 `Cache-Control: public, max-age=31536000, immutable`。至少要有 30 天。
- 不给整站统一长缓存。没有哈希的图标、HTML 不加一年缓存。

`static` 发布会把片段写到 `/www/server/panel/vhost/nginx/extension/124.221.56.38/fat-static-cache.conf`。探测只看直接位于下列目录、且含有网站根 `/www/wwwroot/124.221.56.38` 的 `*.conf`：

- `/www/server/panel/vhost/nginx`
- `/etc/nginx/conf.d`
- `/etc/nginx/sites-enabled`
- `/etc/nginx/sites-available`
- `/usr/local/nginx/conf/vhost`

含有网站根的 `server` 块如果已经 `include` 了 `extension/124.221.56.38/*.conf` 或上述片段路径，就只覆盖片段文件。否则先把站点配置备份到 `/opt/football-data/backups/nginx/<时间>/`，再插入带 `FAT_STATIC_CACHE_BEGIN` 的 include。`nginx -t` 或重载失败会还原片段和站点配置，不会用错误配置重载。

找不到可合并的站点配置时，静态文件仍会盖上，Nginx 会重载，但发布失败。请手工把片段放到上面的路径，并在大陆站含有网站根的 `server` 块内加入：

```nginx
include /www/server/panel/vhost/nginx/extension/124.221.56.38/fat-static-cache.conf;
```

然后执行 `nginx -t` 与 `nginx -s reload`。宝塔上的 nginx 一般是 `/www/server/nginx/sbin/nginx`。不要把这段配置写进其他站点。

换一轮静态之后，普通刷新或重新打开 `http://124.221.56.38/`，页面里的 `/assets/index-<哈希>.js` 应换成新文件名。验收：

```bash
curl -sI http://124.221.56.38/
curl -sI http://124.221.56.38/index.html
curl -sI http://124.221.56.38/assets/index-<哈希>.js
```

前两个响应的 `Cache-Control` 为 `no-cache` 或 `max-age=0`，不能出现大于 0 的 `max-age`。指纹资源的 `max-age` 至少为 2592000。发布脚本在写入片段并重载后，用服务器本机探测做同样判断；验收步骤再从公网探测一次。本机探测连不上时，以公网结果为准。
