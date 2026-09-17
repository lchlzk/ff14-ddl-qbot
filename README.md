# NoneBot QQ Docker starter

这是一个基于 NoneBot2 2.5.0 与 `nonebot-adapter-qq` 1.7.2 的 QQ 官方机器人项目。日常使用不需要记 Docker 命令；Windows 和 Linux 都提供了一键脚本。

本项目源码按 [AGPL-3.0-only](LICENSE) 发布；B站订阅与群聊学习是 AGPL 项目的移植，分别保留了[来源声明](LICENSES/nonebot-plugin-bililive.NOTICE.md)与[来源声明](LICENSES/nonebot-plugin-learning-chat.NOTICE.md)。游戏资料、角色立绘和第三方服务内容不因本项目开源而改变原有权利归属。公开仓库只包含源码、静态配置模板和测试，不包含 `.env`、密钥、用户数据库、缓存、运行日志或 Docker 镜像。

主体与三个主要插件分别维护：这个仓库提供 QQ 连接、共享数据服务、管理后台，以及 AI 聊天、群聊学习、工具箱、Ping；FF14（含 FF Logs、FF14 工具、獭獭市场）、嘟嘟脸和 B站订阅各有独立仓库和安装包。**蜡笔板网页、用户账号、只读 API、角色资料和节点管理全部随嘟嘟脸插件安装**，不属于主体的管理后台。标准 Docker 构建按 [requirements-first-party.txt](requirements-first-party.txt) 中的提交号安装这三个插件。只想安装其中某个插件时，先准备主体环境，然后单独执行 `python -m pip install 'git+https://github.com/lchlzk/ff14-ddl-qbot-plugin-trickcal.git'`（以嘟嘟脸为例），重启机器人即可。插件使用本主体的 `bot_tools` 公共接口，不宣称可直接用于任意 NoneBot 项目；其他第三方插件仍由现有插件管理器处理。

QQ 机器人的 AppID/AppSecret 在网页后台配置，AppSecret 通过独立主密钥加密保存在数据卷，不再明文写入 `.env`；其他可选查询接口仍由服务器主人在 `.env` 配置。用户自带的 AI Key 也会加密保存在数据卷。所有密钥都不会写入镜像，发包时不要发送 `.env` 或数据卷。

新增 **AI 多角色聊天**：智谱 GLM、千问 Character。普通用户私聊 `/ai 设置` 即可创建多个角色，分别加入不同群；群内多人角色可共存，同一用户每群仅一个。`/ai 模型` 会显示默认推荐、选择理由和安全白名单，模型由角色主人手动切换，额度耗尽不会自动回退。角色主人还能按群设置是否必须叫到角色名，以及收集 0 或 1～20 条消息后回答；无需点名且收集 0 会显著增加 Token 消耗。详见 [AI.md](AI.md)，不需要管理员替用户改 `.env` 或重启。

新增 **本地网页管理后台**：机器人运行后执行 `bot.cmd web account`（Linux：`./bot.sh web account`）设置或重置用户名和密码，再打开 `http://127.0.0.1:8080/admin`。后台可添加多个 QQ 机器人并切换管理范围；群设置、AI 角色、学习词库和反馈按机器人隔离，公共图库和同一 QQ 用户的蜡笔板进度跨机器人共享。后台还可删除 AI 角色、管理 AI 群授权与图库；群名会在腾讯开放接口允许时自动同步，也可设置本地显示名称。密码只以加盐哈希保存在数据卷，不写入 `.env` 或镜像，也不会展示 AppSecret、AI Key、角色人设、角色所属群友或 QQ 原始 OpenID。远程访问建议使用 SSH 隧道。完整说明见 [WEB_ADMIN.md](WEB_ADMIN.md)。

内置 **群聊学习**：这是 `nonebot-plugin-learning-chat 0.4.0` 面向腾讯官方 `nonebot-adapter-qq` 的 AGPL-3.0 原生移植，不安装 OneBot 或旧 Web UI 依赖。功能默认关闭、学习库默认公开共享；群主/管理员在目标群发送 `/learn` 阅读隐私说明和配置方法，群主可切换成本群私有。详见 [LEARNING_CHAT.md](LEARNING_CHAT.md)。

## 1. 最快启动

首次安装会自动从同目录的 `nonebot-qq.tar` 导入镜像。请完整解压安装包，不要只复制启动脚本；`image-id.txt` 用于识别安装包版本。启动脚本已兼容 Windows PowerShell 5.1：本地没有镜像时不会因检查失败而提前退出。缺少归档、清单损坏、导入失败时会停止，不会悄悄启动不匹配的旧镜像。

如果旧版 Windows 安装脚本报 `No such image: local/nonebot-qq:1.7.2`，请换用新版 `bot.ps1` 后重新运行 `bot.cmd start`，已有 `.env` 不需要重填。也可在包含 `nonebot-qq.tar` 的目录先执行 `docker load -i nonebot-qq.tar`，成功后执行 `bot.cmd start`。

Offline first start automatically imports `nonebot-qq.tar`. Extract the complete package. Windows PowerShell 5.1 is supported; a missing local image no longer aborts the import probe. If an older launcher reports `No such image`, replace `bot.ps1` and retry, or run `docker load -i nonebot-qq.tar` followed by `bot.cmd start`. Keep your existing `.env`.

先在 [QQ 开放平台](https://q.qq.com/#/)创建机器人，取得 AppID 与 AppSecret。它们稍后在本机网页后台填写。

Windows（推荐使用 `.cmd`，不受 PowerShell 脚本执行策略限制）：

```bat
.\bot.cmd setup
.\bot.cmd start
```

`.cmd` 只为本次进程使用 `ExecutionPolicy Bypass`，不会修改系统策略。如果需要直接调用 PowerShell 脚本，也可以使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\bot.ps1 setup
powershell -ExecutionPolicy Bypass -File .\bot.ps1 start
```

Linux：

```bash
chmod +x bot.sh menu.sh plugin.sh
./bot.sh setup
./bot.sh start
```

`setup` 只创建不含 QQ 凭据的 `.env`。首次启动后运行 `bot.cmd web account`（Linux：`./bot.sh web account`）设置后台账号，打开 `http://127.0.0.1:8080/admin`，在“机器人”页面添加一个或多个 AppID/AppSecret。新增会立即连接，停用或删除会立即断开，修改连接配置会自动重连，不需要重启整个进程。

## 2. 简单的 `.env`

QQ AppID/AppSecret 不再属于 `.env`。这里仅保留所有机器人共用的运行参数：

```dotenv
QQ_IS_SANDBOX=false
```

- 每个机器人的 WebSocket/Webhook 连接方式和消息事件开关在网页后台单独设置。
- 只有确实在 QQ 开放平台配置了沙箱资源时，才使用 `QQ_IS_SANDBOX=true`。

旧版 `.env` 中的 `QQ_APP_ID`/`QQ_APP_SECRET` 或 `QQ_BOTS` 会在首次升级启动时导入加密凭据库，且不会覆盖后台已经修改的凭据。确认后台显示机器人且已经上线，再删除这些旧明文行。

OtterBot FFXIV 与 FF Logs 功能都是可选的，不需要手工编辑复杂 URL；使用第 7 节各自的安全配置命令即可。

Docker 容器无法在运行中自动获得宿主机环境变量的变化。修改 `.env` 后，不必记住 Compose 参数，只需再次运行：

```bat
.\bot.cmd start
```

或在 Linux 运行：

```bash
./bot.sh start
```

脚本会安全地重建容器并重新读取 `.env`，不会重新安装依赖。只有修改 Python 代码、Dockerfile 或依赖后才需要 `build`。

## 3. 日常管理

Windows：

```bat
.\bot.cmd start
.\bot.cmd status
.\bot.cmd logs
.\bot.cmd stop
.\bot.cmd build
```

Linux 的命令完全对应：

```bash
./bot.sh start
./bot.sh status
./bot.sh logs
./bot.sh stop
./bot.sh build
```

- `start` 与 `restart` 都会重新读取 `.env`。
- `build` 会重建镜像、启动机器人并等待健康检查通过。
- `logs` 会持续显示日志，按 `Ctrl+C` 只会停止查看，不会停止机器人。

## 4. 测试机器人

默认已经实现 `/ping`，机器人会回复 `pong`。

- C2C 私聊：发送 `/ping`。
- QQ 群：先测试 `@机器人 /ping`；是否能接收不带 @ 的普通群消息由 QQ 平台授予的消息范围决定，仅修改 NoneBot 配置不能取得该权限。
- QQ 频道：公域频道通常也需要 @；全量消息能力取决于平台授权。

镜像的 `/healthz` 只代表 Python 服务存活；QQ 是否真正连接成功，应以日志中的连接状态和 QQ 端实测为准。

## 5. 一键配置 QQ 菜单与指令面板

`qq-menu.json` 配置了 20 个快捷入口。FF14 插件只占一个 `/ff14` 入口，用于打开其查询目录；具体 `/market`、`/dps` 等旧指令仍兼容直接输入。其余入口用于通用工具、互动、管理和反馈。机器人总目录使用 `/toolbox`（兼容 `/otter`）。QQ 每个原生命令面板最多 20 项。面板名称不带 `/`，客户端会自动按指令形式展示和填入：

- 单聊窗口底部的自定义菜单；
- 单聊输入 `/` 时出现的指令面板；
- 群聊输入 `/` 时出现的指令面板；
- QQ 频道输入 `/` 时出现的指令面板。

先查看 QQ 端当前配置：

```bat
.\menu.cmd status
```

```bash
./menu.sh status
```

一键同步：

```bat
.\menu.cmd sync
```

```bash
./menu.sh sync
```

脚本从网页后台的加密凭据库读取 AppID 与 AppSecret，临时换取 AccessToken，并调用 QQ 官方 OpenAPI；多机器人时用 `--bot-id AppID` 指定目标。脚本不会输出或保存 AccessToken。受管指令面板使用稳定的 `panel.remark` 标记，重复执行只会更新，不会反复创建副本；修改后会回读平台验证。重建镜像不等于更新 QQ 面板，菜单改动需执行 sync。

单聊自定义菜单是全局且唯一的。如果 QQ 已有不同菜单，脚本默认停止以避免覆盖。确认要用本地 `qq-menu.json` 替换时，可显式执行：

```bat
.\menu.cmd sync -ForceMenu
```

```bash
./menu.sh sync --force-menu
```

菜单同步不依赖 Webhook 域名，也不需要重启机器人。点击菜单项会把命令填入输入框；QQ 客户端可能需要重新进入会话或稍等片刻才会刷新显示。菜单只是入口，对应的处理逻辑仍需由 NoneBot 插件实现。

## 6. 一键管理第三方插件

Windows：

```bat
.\plugin.cmd add nonebot-plugin-apscheduler
.\plugin.cmd list
.\plugin.cmd remove nonebot-plugin-apscheduler
.\plugin.cmd rebuild
```

Linux：

```bash
./plugin.sh add nonebot-plugin-apscheduler
./plugin.sh list
./plugin.sh remove nonebot-plugin-apscheduler
./plugin.sh rebuild
```

正式使用建议固定版本：

```bat
.\plugin.cmd add "nonebot-plugin-apscheduler==插件版本"
```

通常模块名由包名自动转换，例如 `nonebot-plugin-apscheduler` 对应 `nonebot_plugin_apscheduler`。如果插件文档给出的导入模块不同，可额外传入：

```bat
.\plugin.cmd add "some-plugin==1.2.3" some_plugin_module
```

```bash
./plugin.sh add 'some-plugin==1.2.3' some_plugin_module
```

连续添加多个插件时可先只改清单，最后构建一次：

```bat
.\plugin.cmd add nonebot-plugin-one -NoBuild
.\plugin.cmd add nonebot-plugin-two -NoBuild
.\plugin.cmd rebuild
```

```bash
./plugin.sh add nonebot-plugin-one --no-build
./plugin.sh add nonebot-plugin-two --no-build
./plugin.sh rebuild
```

第三方插件是可执行 Python 代码，运行时可以读取机器人密钥并访问网络。只安装可信来源，并优先在 [NoneBot 插件商店](https://nonebot.dev/store/plugins)确认它支持 NoneBot2 与 `QQ` / `nonebot-adapter-qq`。只支持 OneBot V11 的插件通常不能直接使用。

## 7. FFXIV：OtterBot 兼容插件与 FF Logs（可选）

项目已内置针对 `NoneBot-Adapter-QQ` 的安全兼容层：它接入 OtterBot 可用的 WebAPI，并本地移植了几个不依赖服务器的命令。这不是把旧 OtterBot 整个搬进来：原项目的群管、订阅推送、自定义回复等功能依赖领养网络、Django/Redis 数据、旧 OneBot 数字 QQ 号或 QQ 官方机器人并未开放的动作，无法通过 WebAPI 无损兼容。

这个方案保留当前 WebSocket 连接，不需要在网站“领养机器人”，也不需要域名或 Webhook。

先找一只在线的獭獭机器人，**私聊**发送：

```text
/bot token 你设置的随机字符串
```

建议使用 12～16 位随机字母数字，服务端最多保存 16 个字符；不要在群聊发送 Token。然后运行安全配置向导，Token 输入不会显示：

```bat
.\bot.cmd otter-setup
.\bot.cmd start
```

Linux：

```bash
./bot.sh otter-setup
./bot.sh start
```

向导中的 QQ 号是执行 `/bot token` 时使用的**个人数字 QQ 号**，不是 QQ 开放平台 AppID；Otter API Token 也不是 AppSecret。配置最终保存在宿主机 `.env`：

```dotenv
OTTER_API_QQ=个人数字QQ号
OTTER_API_TOKEN=獭獭Token
OTTER_API_BASE=https://xn--v9x.net/api/
OTTER_INCLUDE_URLS=false
OTTER_GLOBAL_MIN_INTERVAL=0.25
```

市场查询现在直接使用 Universalis v2，不再经过獭獭旧市场接口，也不需要
OtterBot Token。镜像内置国服可交易物品名称索引；新版本物品尚未进入索引时，
也可以直接输入物品 ID：

```text
/market <物品名或ID> <服务器>   查询当前挂单
/market <物品名或ID> 国服全大区 比较国服四个大区的最低价
/mitem <物品名或ID> <服务器>    /market 的别名
/market 火之水晶 拂晓之间
/market 8 拂晓之间
/market 火之水晶 国服全大区
```

下面 5 个国服资料与市场工具也不需要 OtterBot Token；它们使用中文 XIVAPI
读取当前国服客户端数据，并使用 Universalis 查询玩家众包的市场数据：

```text
/gather <物品名或ID>                         查询采集职业、等级和区域
/sales <物品名或ID> <服务器/大区/区域>       查询最近成交与参考售速
/recip <物品名或ID>                         查询制作配方（/recipe 同义）
/craftcost <物品名或ID> <服务器/大区/区域>   按最低挂单估算材料成本
/cheapest <物品名或ID> <服务器/大区/区域>    比较查询范围内各服务器最低价
```

例如：

```text
/gather 枫木原木
/sales 火之水晶 梦羽宝境
/recip 枫木木材
/craftcost 枫木木材 陆行鸟
/cheapest 火之水晶 陆行鸟
```

`/craftcost` 是行情估算：材料按当前最低挂单单价计算，半成品不会继续递归拆解，
也不包含交易税费。若任何材料没有公开行情，机器人只显示已知小计，不会给出虚假的
完整成本或利润。

其他獭獭查询（需要 Token）：

```text
/otter                         查看帮助和配置状态
/quest <任务名>                查询任务
/search <物品名>               查询物品
/luck                          今日运势
/luck r                        重抽运势
/house <服务器> [区域] [大小] [部队|个人]
/weather <区域> [天气] [数量]  查询 FFXIV 天气
```

市场挂单与成交来自 Universalis 众包数据，可能存在延迟；查询结果不是成交保证。

本地命令（不需要 Token，断网也可用）：

```text
/random [面数]                默认在 1–1000 中取随机数
/gate [2|3]                    帮你选择挖宝门
/dice <NdM±修正>              例如 /dice 3d12+5
/about                         项目说明
```

### FF Logs 官方 API v2（可选，独立于 OtterBot）

`/dps` 和 `/raid` 现在直接使用 FF Logs 官方 API v2，不需要獭獭 Token。先在与查询区域对应的网站创建 API Client，取得 **Client ID** 和 **Client Secret**：

- 国服：[https://cn.fflogs.com/api/clients/](https://cn.fflogs.com/api/clients/)
- 国际服：[https://www.fflogs.com/api/clients/](https://www.fflogs.com/api/clients/)

然后运行配置向导并重新启动：

```bat
bot.cmd fflogs-setup
bot.cmd start
```

Linux：

```bash
./bot.sh fflogs-setup
./bot.sh start
```

可用命令：

```text
/fflogs                                                       查看帮助
/fflogs status                                                查看配置状态
/fflogs zones [CN|JP] [all]                                   查看当前副本名称、简称和 ID
/dps <角色名> <服务器> [区域=国服] [副本=M9S或ID] [职业=职业] [指标=rdps]
/raid <角色名> <服务器> [区域=国服] [副本=M9S或ID] [职业=职业] [指标=rdps]
/reid ...                                                     /raid 的别名
/dps North Face | Chocobo | JP | zone=76 | job=Paladin | metric=rdps
```

最后一行演示了英文多词角色名的 `|` 分隔写法。中文和英文参数名都能使用，例如 `副本=M9S` 与 `zone=M9S` 等价；也可以直接把简称写在末尾，如 `/dps 角色名 服务器 E8S`。机器人内置 M/P/E/O/A 系列零式简称映射：M9S–M12S 对应 FF Logs zone 73，E8S 对应 zone 33 中的希瓦。输入具体楼层时只显示该楼层；M12S 这类分阶段 Boss 会显示 P1/P2。`/fflogs zones` 从官方 API 读取当前副本目录并显示可复制的 ID。

`zone`/`副本` 不填时由 FF Logs 选择当前可用副本；`metric`/`指标` 支持官方角色排名接口当前接受的 `rdps`、`ndps`、`cdps`、`dps`。这里使用服务器到服务器的 Client Credentials，不需要公网域名、OAuth 回调地址或普通 QQ 用户逐个登录授权。

排名百分位使用 FF Logs 角色页面相同的“排名 + 历史 + 全装等”口径：比较战斗发生时的历史榜单，而不是拿旧战绩按今天的榜单重新计算。单 Boss 百分位与击杀时间按网站表格方式显示，例如 API 历史值 `89.9624` 显示为 `89%`。

查询只能看到 FF Logs 上的**公开日志**；私有、隐藏或尚未上传的战报不会返回。因此“没有找到公开记录”不代表角色没有通关。Client Secret 只应保存在 `.env`，不要放进镜像、菜单或分享给别人。

旧版 OtterBot `/dps`、`/raid` 端点及 `OTTER_ENABLE_EXPERIMENTAL` 配置已从本项目移除；同名命令现在只走 FF Logs 官方 API v2。`/botlist` 仍未接入，因为旧服务会在同一响应中包含私有机器人的未遮罩 QQ 账号信息。

插件使用异步 HTTPS，带连接超时、输入/响应大小限制、并发限制、全局限速和每用户冷却；不会记录包含 Token 的完整 URL。`/luck` 会先将 QQ OpenID 转成稳定的匿名标识，不会把原始 OpenID 发给第三方。默认 `OTTER_INCLUDE_URLS=false`，会移除上游返回的 CQ 码和网页链接，因此没有 QQ 可信域名也能使用纯文本结果。只有在 QQ 开放平台已批准相应可信域名后，才建议改成 `true`。獭獭是第三方社区服务，没有公开 SLA，服务不可用时插件会返回友好错误，不会影响 `/ping` 和 QQ 连接。

已有机器人曾同步旧菜单时，需要确认替换一次：

```bat
.\menu.cmd sync -ForceMenu
```

新机器人直接运行 `menu.cmd sync` 即可。插件所依据的固定版本见 [OtterBot WebAPI 源码](https://github.com/Bluefissure/OtterBot/blob/151fca30ac74d0e091ba790a5e2c3ed763775af9/ffxivbot/views/api/webapi.py)和 [原始命令清单](https://github.com/Bluefissure/OtterBot/blob/151fca30ac74d0e091ba790a5e2c3ed763775af9/ffxivbot/handlers/__init__.py)。

### CN game-data and market tools (no Otter token required)

These five commands use the Simplified-Chinese XIVAPI service for current CN client
data and Universalis for crowdsourced market data:

```text
/gather <item-or-ID>                         Gathering method, level, and area
/sales <item-or-ID> <world/DC/region>        Recent sales and sale velocity
/recip <item-or-ID>                         Crafting recipe (/recipe is an alias)
/craftcost <item-or-ID> <world/DC/region>    Estimated material cost
/cheapest <item-or-ID> <world/DC/region>     Lowest listing in each world
```

`/craftcost` uses current lowest unit listing prices, does not recursively expand
intermediate materials, and excludes taxes and fees. If a material has no public
listing, it reports only the known subtotal instead of presenting an incomplete cost
as a real total.

### FF Logs official API v2 (optional, separate from OtterBot)

The `/dps` and `/raid` commands now use the official FF Logs API v2 directly and do not require an Otter token. Create an API client on the site matching the region you query and copy its **Client ID** and **Client Secret**:

- China: [https://cn.fflogs.com/api/clients/](https://cn.fflogs.com/api/clients/)
- International: [https://www.fflogs.com/api/clients/](https://www.fflogs.com/api/clients/)

On Windows, run `bot.cmd fflogs-setup` and then `bot.cmd start`. On Linux, run `./bot.sh fflogs-setup` and then `./bot.sh start`.

Command syntax is `/dps <character> <server> [region=CN] [zone=M9S-or-ID] [job=job] [metric=rdps]`; `/raid` accepts the same arguments, and `/reid` is an alias for `/raid`. Use `/fflogs zones [CN|JP] [all]` to list live ranking zones and their IDs. Savage floor aliases from the M/P/E/O/A series are accepted directly; for example, `zone=E8S` selects Shiva in zone 33, while `zone=M9S` selects the first floor in zone 73. For a multi-word English character name, use a pipe-separated form such as `/dps North Face | Chocobo | JP | zone=76 | job=Paladin | metric=rdps`. The official character-ranking API currently accepts `rdps`, `ndps`, `cdps`, and `dps` here.

Percentiles use the same rankings + historical + all-brackets basis as FF Logs character pages. In other words, an older performance is evaluated against the historical ranking around the time of that fight, not recalculated against today's leaderboard.

This integration uses the server-to-server Client Credentials flow, so it needs no public domain, OAuth callback URL, or authorization by ordinary QQ users. It can read public FF Logs reports only; the absence of a public record does **not** prove that a character has not cleared the encounter. Keep the Client Secret private. The legacy OtterBot `/dps` and `/raid` endpoints and the `OTTER_ENABLE_EXPERIMENTAL` setting have been removed from this project.

## 8. 嘟嘟脸恶作剧插件

发送 `/tr` 查看查询目录。角色查询默认使用国服数据，末尾可指定 `国服` 或 `韩服`；国服未收录的角色会明确提示并自动使用韩服资料。角色形象、喜欢食物、蜡笔板全体加成和文字资料会合成为一张纵向角色卡，并与“随机角色”按钮放在同一条消息中；生成图会在数据目录保留 7 天并跨重启复用。`/tr 兑换码 [国服|韩服]` 默认国服，只列最多 10 个兑换码及过期时间。在私聊或群聊发送 `/tr 蜡笔板 导入` 后，于 10 分钟内将下一条消息直接发送为 Soshage collection v1 JSON 文件，即可查看自己的总进度、角色进度、计划节点、百分比属性加成、金币和金蜡笔用量。发送 `/tr 蜡笔板 网页` 可通过单次链接或群聊绑定码进入管理网页；登录后点击“绑定新机器人”，再用本人 QQ 到新机器人所在群发送页面生成的 `/tr 蜡笔板 绑定 绑定码`，即可让多个机器人和网页访问同一份蜡笔板。不导入也能直接发送 `/tr 蜡笔板 加点 角色名 2 攻击`；首次加点会自动创建个人蜡笔板并点亮该角色，也可用 `/tr 蜡笔板 点亮 角色名` 只记录拥有状态。`/tr 蜡笔板 点亮 全部` 会登记公开目录中的所有角色但不点任何节点；`/tr 蜡笔板 加点 全部 1 攻击` 可把全部已拥有角色适用的第一层百分比攻击节点一次记为已点，属性写成 `全部`（如 `/tr 蜡笔板 加点 全部 1 全部`）则会处理该层全部百分比节点。也可用逗号、中文逗号或顿号指定最多 100 个角色，例如 `/tr 蜡笔板 加点 艾尔芬,艾雅 3 攻击`；对应的 `撤销加点` 同样支持全角色、全部属性和指定角色名单。节点数、进度角色数、金币、金蜡笔和属性加成都只统计百分比节点，固定数值节点完全不参与；第一至第三层会分别显示攻击、防御、血量、暴击/暴伤、暴抗/暴伤抗五组百分比节点，同组属性来自同一个节点，不会重复计数。`未点` 命令可按属性查询未点完角色，也可用 `/tr 蜡笔板 未点 二层 攻击` 只查看指定层；尚未开始蜡笔板的已拥有角色同样会被查到。等待上传同时绑定发起人与当前会话，记录按 QQ 用户隔离；公开角色/节点目录合并 Soshage 与 [Crayon-note](https://github.com/kai2002002-crayon/Crayon-note)，已有角色优先采用 Soshage 的精确节点、金币和材料，Crayon-note 补充缺失角色及三层百分比路径。目录固定在每天 18:00（UTC+8）刷新，任一补充来源失败都不会拖垮原 Soshage 目录，个人进度不会被定时任务自动修改。该插件优先只读访问 GameKee 的嘟嘟脸玩家 Wiki，也可查询神器、宠物、攻略，并支持全站搜索和随机角色；GameKee 故障或缺少旧分类时使用 BWIKI 备用。无需 Token，不登录游戏，不使用玩家账号接口。查询有超时、限速、响应大小限制、请求合并和缓存保护。完整说明见 [TRICKCAL.md](TRICKCAL.md)。

蜡笔板中的中文角色名统一转换为简体并保留英文名。发送 `/tr 蜡笔板 未拥有 [页码]` 可查看公开目录中尚未点亮的角色；`/tr 蜡笔板 取消点亮 角色名或逗号名单` 会移除指定角色及其全部节点记录，也可写成 `反点`。清空全部角色必须发送 `/tr 蜡笔板 取消点亮 全部 确认`。

蜡笔板网页账号现可在“第三方 API”生成 90 天有效的只读令牌；第三方后端用 `Authorization: Bearer` 访问 `/api/v1/tr-board/catalog` 和 `/api/v1/tr-board/board`，只读取令牌所有者的角色目录与进度，不开放节点写入。令牌仅显示一次、可随时撤销；可分享的[网页 API 文档](https://qbot-l.yizhixiaogame.com/tr-board/api-docs)包含请求示例、响应字段与错误码，项目内说明见 [TRICKCAL.md](TRICKCAL.md#第三方只读-api)。

## B站直播与动态推送

这是 [nonebot-plugin-bililive](https://github.com/Akiyy-dev/nonebot-plugin-bililive) 的腾讯官方 `nonebot-adapter-qq` 原生移植，不安装 OneBot、PostgreSQL 或原插件的 Web UI。群主/管理员在目标群发送 `/bili 关注 UID` 即可同时开启该 UP 主的直播和动态推送；使用 `/bili 取关 UID`、`/bili 列表`、`/bili 已开播`、`/bili 开启直播 UID`、`/bili 关闭直播 UID`、`/bili 开启动态 UID`、`/bili 关闭动态 UID` 和 `/bili 状态` 管理。首次轮询只建立当前直播状态和最新动态偏移，不会补发历史内容。默认每 60 秒检查直播、每 120 秒检查动态，可通过 `BILILIVE_LIVE_INTERVAL`、`BILILIVE_DYNAMIC_INTERVAL` 调整；两个检查任务独立运行，检查耗时不再额外叠加到间隔上。`BILILIVE_OFF_NOTIFY=true` 时额外发送下播通知。动态列表显式请求全部类型，异常空列表、落后的列表及接口失败会继续尝试备用接口和有超时限制的浏览器降级；所有来源均失败时保留原偏移，等待下一轮，不把失败当作成功。新动态会根据 B站公开 API 的发布者、发布时间、正文和配图合成卡片，与原动态链接一起发送；若个别图片临时下载失败，仍会发送正文卡片，卡片生成异常时则退回文字摘要与链接。发布时间、发现时间和推送结果会写入服务端日志及 `bililive_dynamic_events` 表（保留最近 2000 条）；QQ 发送结果不确定时不自动重发，以免重复通知。B站接口的可见时间与风控仍可能影响实际延迟，不能保证发布后固定时间内送达。

关注时依次尝试用户名片、直播资料和个人空间动态中的发布者信息；名片查询失败或返回空资料也会尝试备用来源。没有直播间不代表用户不存在，仍可关注其动态。所有来源均无法确认账号时会提示稍后重试，不把接口风控或空数据误报为“用户不存在”。用户查询有 90 秒总超时限制。

订阅按“机器人＋QQ群”隔离，数据库中的群 OpenID 使用独立 Fernet 主密钥加密；备份数据卷时必须同时保留 `data/secrets/bililive-targets-master.key`。后台群管理页面提供“B站推送”总开关，关闭后该机器人不会向该群发送 B站通知。Webhook 模式下后台轮询仍可调用 QQ 主动群消息接口，但最终能否送达取决于 QQ 开放平台为该机器人批准的主动消息能力；失败只记录安全错误类型，不会把群 OpenID、凭据或 B站响应正文写入日志。动态数据使用 B站公开 Web API，若触发 `-352/-412` 风控，`/bili 状态` 会提示稍后重试。

移植来源和固定审阅版本见 [第三方声明](LICENSES/nonebot-plugin-bililive.NOTICE.md)，许可为 AGPL-3.0-or-later。

## 9. Webhook（可选）

将 `.env` 改为：

```dotenv
QQ_CONNECTION=webhook
```

再执行 `bot.cmd start` 或 `bot.sh start` 重新加载。宿主机默认只在 `127.0.0.1:8080` 暴露服务，应使用 Caddy、Nginx 或云负载均衡提供公网 HTTPS。QQ 平台回调地址为：

```text
https://你的域名/qq/webhook
```

反向代理必须保留 `X-Bot-Appid`、`X-Signature-Ed25519`、`X-Signature-Timestamp` 请求头和原始请求体。不要关闭 Webhook 签名验证，也不要直接把容器 HTTP 端口暴露到公网。

## 10. 一键打包给别人

Windows 执行：

```bat
.\package.cmd
```

Linux 执行：

```bash
chmod +x package.sh
./package.sh
```

脚本会构建最终镜像，在 `release` 目录生成离线文件，并自动生成项目根目录的 `QQbot-one-click.zip`。它只复制运行需要的白名单文件和 `nonebot-qq.tar`，明确排除你的 `.env`、密钥和运行数据。直接发布这个 ZIP，对方完整解压即可；也可以交付整个 `release` 目录。两平台均借助构建镜像完成压缩，不需要另外安装 Python。

Both packaging scripts automatically create `QQbot-one-click.zip` using an explicit runtime allowlist and verify the archive. No real `.env`, credentials, or runtime data are included. See [优化与升级说明](OPTIMIZATIONS.md) for size, performance, and gallery migration details.

接收方装好并启动 Docker 后，Windows 只需在该目录运行：

```bat
.\bot.cmd start
```

Linux 只需运行：

```bash
chmod +x bot.sh menu.sh
./bot.sh start
```

第一次启动会自动导入随包镜像、生成不含 QQ 凭据的 `.env` 并启动后台；接收方随后在网页后台填写自己的一个或多个 AppID/AppSecret，机器人会立即开始连接。无需手工重启，也无需执行 `docker load` 或 Compose 命令。`image-id.txt` 会避免机器上恰好存在同标签旧镜像时误用旧版本。详细说明也随包放在 `START.txt`。

接收方可以按需配置两项独立的 FFXIV 功能：需要 OtterBot 查询时，运行 `bot.cmd otter-setup`（Linux：`./bot.sh otter-setup`），输入自己的个人 QQ 与 Otter API Token；需要 `/dps`、`/raid` 时，接收方先在 [国服](https://cn.fflogs.com/api/clients/)或[国际服](https://www.fflogs.com/api/clients/)申请自己的 FF Logs Client ID/Secret，再运行 `bot.cmd fflogs-setup`（Linux：`./bot.sh fflogs-setup`）。每项配置完成后都再次运行 `start`。这两项都是可选配置，制作者的 Token、Client ID/Secret 和 `.env` 都不会进入离线包。

离线包是运行版，不含插件构建工具。若需要增删插件，请在完整源项目中操作后重新运行打包脚本。

## 新版持久化工具箱

QQ群和私聊默认使用本机器人的公共图库；私聊固定使用公共图库，没有私库。只有群主可在群内发送 `/group gallery local` 改用本群私有图库，或 `/group gallery public` 切回公共图库，普通群管理员不能切换。私有图库所有分类合计 100 张；公共图库不设张数上限、不自动清理，公共图仅机器人总管理员可删除。切换不会公开或搬迁旧的本群图片。所有人均可上传，合格 GIF 保留完整动画；`/image status` 查看图库状态，`/image list 分类 page=2` 翻页。本次仍保存在 Docker 本地数据卷，图库读写已独立封装，尚未接入 COS/S3；“无上限”仍受真实磁盘容量限制。

Gallery: QQ groups and private chats use the bot-wide public gallery by default. Private chats are always public and cannot select a local gallery. Group owners can select `/group gallery local` for a private group gallery or `/group gallery public` to switch back. Local galleries hold 100 images each; public storage has no count cap or automatic expiry, and only bot superadmins can delete public images. Switching does not migrate existing images. Valid GIFs retain all frames. Originals are stored in local files with SQLite metadata; legacy BLOBs remain readable during backed-up migration. A separate storage API reserves future COS/S3 integration.

新增游戏、互动、图片和管理员功能：发送 `/toolbox` 或 `/otter` 查看同一份完整目录。获批 QQ 群全消息权限并收到事件后，新命令可直接发送（例如 `/cat`），不额外强制 @；未获批时仍需 @。这不改变任何管理员权限。使用方法、权限授权、私聊反馈箱、数据卷迁移和功能边界见 [工具箱说明（含英文）](TOOLBOX.md)。

QQ群主/群管理员按平台当前消息中的群角色自动管理本群，无须服务器操作。群内 `/bot` 仅向有权限者展示可用管理功能，不展示身份码或授权步骤；普通成员无权访问管理面板。新总管理员绑定流程：服务器执行 `bot.cmd admin token`（Linux：`./bot.sh admin token`）生成 10 分钟有效的一次性授权码 → 仅本人机器人私聊发送 `/bot whoami 授权码` 获取身份码 → 在 15 分钟内回到服务器执行 `bot.cmd admin add 身份码`（Linux：`./bot.sh admin add 身份码`）确认。私聊兑换本身不授权，普通 `/bot` 请求不再创建身份记录。群角色不能提升为总管理员，也不能读取全局反馈。已有授权保留；新绑定仅限私聊，不自动关联群身份。设置立即生效，无须修改 `.env` 或重启。反馈仅允许已授权总管理员私聊查看；不做支持/赞助入口。

`/custom_reply` 同时支持 @ 和获批的全消息群事件，不在代码里强制 @；未获 QQ 全消息权限时仍需 @。新持久化数据位于 Docker `bot-data` 卷，镜像升级不会清空，但 `down -v` 会删除数据，不要误用。

New persistent features are documented in [TOOLBOX.md](TOOLBOX.md), including English setup notes. QQ group owners/admins automatically manage their current group only; `/bot` shows their available tools, not identity codes or binding steps. Ordinary users cannot access the management panel. New superadmin binding requires three steps: server `admin token` → private chat `/bot whoami TOKEN` → server `admin add CODE`. Use `bot.cmd` on Windows or `./bot.sh` on Linux. Invitations expire after 10 minutes and are single-use; confirm the returned identity within 15 minutes. Redemption alone grants nothing. Existing grants are preserved, but new bindings are private-only and not linked across chats. Feedback remains local and is accessible only to server-authorized superadmins in a private chat. Custom replies accept full-message events when QQ grants the corresponding platform permission. Keep the Docker data volume when updating.

## 常见问题

- `TOKEN` 不需要手工填写；程序使用 AppID 与 AppSecret 自动换取短期 AccessToken。
- `OTTER_API_TOKEN` 是獭獭 WebAPI 的独立 Token，不是 QQ AppSecret，也不是 QQ 官方 AccessToken。
- AppSecret 只在网页后台填写并加密保存，不要写进 `.env`、Dockerfile、镜像、`qq-menu.json` 或插件清单；备份时同时保留数据库和主密钥。
- 正式环境可能要求服务器的固定公网出口 IP 加入 QQ 平台白名单。
- Docker 负责运行环境，但不会自动提供域名、HTTPS 证书或固定公网 IP。
- 菜单/指令面板配置成功不等于机器人获得全量群消息权限。
