# 新版工具箱使用说明

本说明汇总主体及可选插件的命令。FF14（包括 `/fsx`、`/ofish`、`/hunt` 和群狩猎设置）、嘟嘟脸、B站功能分别由独立安装包提供，不再内置于工具箱；未安装对应插件时其命令不可用。源码、安装与依赖说明见 [FF14 插件](https://github.com/lchlzk/ff14-ddl-qbot-plugin-ff14)、[嘟嘟脸插件](https://github.com/lchlzk/ff14-ddl-qbot-plugin-trickcal)、[B站插件](https://github.com/lchlzk/ff14-ddl-qbot-plugin-bililive)。

## AI 多角色聊天

任何用户都可私聊 `/ai 设置`。支持智谱 GLM 和千问 Character；用户自行提供 Key，每人可创建多个角色并分别加入不同群。同一用户每群仅一个角色，不同用户的角色可共存。私聊 `/ai 模型` 可查看当前模型、默认推荐和理由，再手动切换；额度耗尽不会自动切换。群角色默认“提到名字就立即回复”；角色主人可私聊 `/ai 群设置 授权编号`，按群改成无需点名或收集 1～20 条消息后回答，`收集 0` 表示每条符合条件的消息都尝试回复。无需点名且收集 0 会快速消耗 Token。所有个人角色共用每日 5 次私聊请求；完整配置、模型白名单、授权、额度与隐私说明见同目录 [AI.md](AI.md)。

服务器总管理员还可使用本地网页后台：`bot.cmd web account`（Linux：`./bot.sh web account`）设置或重置账号密码，然后打开 `http://127.0.0.1:8080/admin`。后台汇总群设置、AI 群授权、图库、反馈和审计记录；详情见 [WEB_ADMIN.md](WEB_ADMIN.md)。

## 群聊学习

`/learn` 是旧 `nonebot-plugin-learning-chat` 面向腾讯官方 QQ 适配器的本地移植版。默认关闭、默认使用公开学习库；群主或群管理员在目标群发送 `/learn on` 后，它才会把合规文字和图片写入本机 SQLite，学习相邻发言并在达到阈值后概率回复。群主可用 `/learn mode local` 改成本群私有，或用 `/learn mode public` 切回公开共享。数据不进入图片图库或 AI 角色上下文；本地网页后台可按群配置和管理学习词库。完整命令、容量、隐私及与上游的差异见 [LEARNING_CHAT.md](LEARNING_CHAT.md)。

发送 `/toolbox` 或 `/otter` 查看同一份完整功能目录。已获批并收到 QQ 全消息群事件时，新旧命令都可以直接发送，例如 `/cat`、`/fsx 暴击 3000`；只有 @ 消息权限时仍需先 @机器人。私聊直接发命令。免 @ 不会绕过管理员权限、命令开关或频率/配额限制；机器人发送的消息不会触发新工具。普通未指向机器人的频道消息不触发新工具。

## 权限分工与总管理员绑定

QQ群主和群管理员无需运行服务器命令：机器人会根据 QQ 当前群消息中的 `author.member_role` 自动允许他们管理**本群**的关键词、命令开关、配额、本群图库、狩猎及活动。在群内发送 `/bot` 或 `/bot whoami`，只显示当前身份和本人可用的管理功能，不显示身份码、授权码或服务器授权步骤。**图库模式切换仅限群主或服务器授权总管理员；公共图片删除仅限总管理员。**其余本群工具权限不变。普通用户访问 `/bot`、`/group`、`/command` 的管理面板会被拒绝，不影响其使用普通查询、上传、投票等功能。

群管理员、群主或机器人总管理员可在目标群发送 `/ginfo [页码]`（也可 `/group status [页码]`），查看狩猎默认小区、工具额度、图库模式与容量、功能开关、关键词/狩猎数量、AI 总开关以及本群 AI 角色。每项可配置属性同时显示对应的设置命令和权限提示；AI 角色只显示角色名与角色编号，不显示主人、所用 AI、启用状态、角色额度、API Key 或人设。三类管理员均可 `/ai 移除 角色编号`；`/ai 移除 all` 仅群主或总管理员可用。`/group server 小区名` 只供 `/hunt` 在省略小区时使用，不是市场插件的默认大区；市场、成交、制作成本和跨服比价可在查询末尾直接写服务器、大区或区域，写大区时 Universalis 会聚合该大区内的小区。

这里的 QQ 群主 `owner` **不是机器人总管理员**。只有服务器主人能通过下面的本机命令绑定机器人总管理员；普通成员无法通过 QQ 聊天运行这些命令，开放平台的开发/运营协作者身份也不自动授予总管理员。

`/bot status` 属于机器人运行管理，仅限服务器授权的总管理员；不显示在群主、群管理员或手动委派普通管理员的面板中，直接输入也会拒绝。其余本群管理功能不受影响。

1. 先在**服务器项目目录**生成一次性授权码（不要在 QQ 里执行）：

   Windows：`.\bot.cmd admin token`

   Linux：`./bot.sh admin token`

2. 只在**你自己的机器人私聊**发送 `/bot whoami 授权码`，把“授权码”替换成上一步的 32 位实际值。验证成功后，机器人返回 16 位身份码，**不会立即赋予任何新权限**。
3. 回到服务器，在 15 分钟内确认你本人私聊收到的身份码：

   Windows：`.\bot.cmd admin add 身份码`

   Linux：`./bot.sh admin add 身份码`

4. 总管理员绑定立即生效，不用修改 `.env`，不需要重启。在该私聊中发 `/bot` 查看可用管理功能，使用 `/comment list`、`/comment show 编号` 等查看全局反馈。其他群主/群管理员无权查看。

授权码有效期为 10 分钟，成功兑换一次后立即作废；再次运行 `admin token` 会作废上一个尚未兑换的授权码。群内不能兑换。数据库仅保存授权码的哈希值，内置运行日志会遮蔽 `/bot whoami` 后的授权码；QQ 聊天记录中仍有你自己发送的原文，切勿截图公开或转发。身份码的服务器确认申请有效期为 15 分钟，确认一次后不能重复使用，过期需要重新走完整流程。回复发送失败时也请重新生成授权码。

无授权码的普通 `/bot` / `/bot whoami` 不创建身份记录、不返回身份码；未授权的私聊也不能浏览管理面板。旧版公开身份码不能绕过新流程直接成为总管理员。新绑定不会自动把私聊身份映射到其他群或频道。

`bot.cmd admin list` / `./bot.sh admin list` 只列出手动授权身份；`admin remove 身份码` 撤销手动授权。身份码不是密码，单独拿到它不能授权自己。没有聊天命令可以创建总管理员，也没有远程执行服务器命令的入口。

群角色仅取自适配器解析后的消息字段，不从昵称、消息正文或另一个群推断；缺失或未知角色不会自动获得权限。群角色不会写成永久授权，撤销 QQ 管理员后，下一条以普通成员身份发送的消息将失去自动管理权限；如果曾另行手动授权，额外权限仍需明确撤销。

升级保留已存在的手动授权、身份记录和其他数据，不自动撤销历史权限；新总管理员只能通过上述私聊流程绑定，不再发放群/频道身份码。历史手动授权可在服务器用 `admin list` 查看、`admin remove 身份码` 撤销。兼容保留的 `/group admin add/remove` 仅供已有服务器授权总管理员对既有本会话身份委派/撤销普通权限，不创建身份码或总管理员，不展示在群管理帮助中。群内新用户直接使用 QQ 群角色；频道暂不提供新的自动管理员绑定。普通消息不新增身份记录。

## 功能与示例

| 命令 | 示例 | 说明 |
| --- | --- | --- |
| 副属性 | `/fsx 暴击 3000`、`/fsx 技速 1200` | 7.x、100 级属性换算和下一档；无急速/职业特性 |
| 海钓 | `/ofish 3`、`/ofish 靛青 3` | 北京时间未来班次，含当前可报名班次 |
| 红玉海钓 | `/ofish 红玉旧 3`、`/ofish 红玉新 3` | 分别为 7.5 前、7.5 起的航线，不自动猜测区服版本 |
| 公招 | `/akhr 治疗 支援 远程位` | 国服可公招干员快照，1～5 标签，组合最多 3 标签；默认 9 小时 |
| 低星公招 | `/akhr 支援机械 3:50` | 按低星短时长筛选；不保证标签不掉落 |
| 嘟嘟脸 | `/tr 角色 埃尔芬 [国服\|韩服]`、`/tr 埃尔芬` | 默认国服；国服未收录会提示并回退韩服；附缩小的角色、喜欢食物和蜡笔加成图 |
| 嘟嘟脸图鉴 | `/tr 神器 名称`、`/tr 宠物 名称`、`/tr 食物 名称` | GameKee 优先；食物暂用 BWIKI 备用 |
| 嘟嘟脸资料 | `/tr 攻略 新手`、`/tr 兑换码 [国服|韩服]`、`/tr 搜索 关键词` | 攻略、分服兑换码（最多 10 个）与全站搜索；详情见 `TRICKCAL.md` |
| 投票 | `/vote create 今晚玩什么 \| 海钓 \| 零式` | 创建后按回复编号投票 |
| 投票操作 | `/vote cast 1 2`、`/vote show 1`、`/vote close 1` | 每人一票可改投；发起人/管理员结束 |
| 抽奖 | `/lottery create 周末礼物 \| 2` | 只抽主动报名的人，不读取群成员列表 |
| 报名与开奖 | `/lottery join 1 昵称`、`/lottery draw 1` | 同一身份不重复报名；发起人/管理员开奖，结果永久锁定 |
| 退出/列表 | `/lottery leave 1`、`/lottery list`、`/vote list` | 开奖后不能退出或重抽；同场昵称不能重复 |
| 关键词 | `/custom_reply set 你好 \| 你好呀` | 管理员设置，精确匹配，最多 50 个关键词 |
| 关键词管理 | `/custom_reply list`、`/custom_reply del 你好` | 按群/私聊隔离，不覆盖 `/` 命令 |
| 公式图片 | `/tex \frac{a}{b}=\sqrt{x}` | 本地 Mathtext 排版，不运行 TeX/Shell；最多 240 字符 |
| 文字动图 | `/gif 今天也要开心` | 本地生成 GIF，最多 36 字 |
| 猫图 | `/cat` | 优先当前选中图库的 cat 分类，否则调用 TheCatAPI；在线猫图不自动入库 |
| 动漫图 | `/waifu` | 只读当前选中图库的 waifu 分类，所有人都可先上传合规素材 |
| 分类图库 | `/image 风景`、`/image list [分类] [page=页码]` | 当前图库随机图片与分页列表，每页最多 20 项 |
| 添加素材 | `/image add 风景`，同一条消息附带一张 QQ 图片 | 所有人可上传到当前选中图库；静态图片压缩去元数据，GIF 完整保存 |
| 删除素材 | `/image list 风景`、`/image del 12` | 本群图库：群主/管理员；公共图库：仅机器人总管理员。按编号删除，不得越过当前图库 |
| 图库容量 | `/image status` | 显示当前图库、张数和图片数据量，不含数据库额外开销 |
| 图库切换 | `/group gallery public`、`/group gallery local` | 仅群主/总管理员；立即生效，不搬迁、不删除旧图 |
| 对联草稿 | `/duilian 春风映青山` | 离线词组对仗，不是 AI 自由创作，不保证平仄；未覆盖字词保留原文 |
| 反馈 | `/comment 具体问题` | 本地保存，每人 24 小时最多 5 条，每条最多 500 字 |

每个命令可发送 `help` 获取用法；`/ofish`、`/cat`、`/waifu` 无参数时会实际查询。QQ 是否允许图片上传/显示由平台权限和客户端决定。

### 公共图库、本群图库与完整 GIF

QQ群在没有保存过图库选择时默认使用本机器人的公共图库（`public`）；私聊固定使用同一个公共图库，没有私聊私库，也不能在私聊切换模式。群主发送 `/group gallery local` 可改成本群私有图库，再发送 `/group gallery public` 可切回公共图库。升级前已经明确选择过 `local` 或 `public` 的群保持原选择。群管理员、手动授权的普通管理员和普通成员都不能切换。使用同一个机器人公共图库的群和私聊共享图片，不同机器人应用不共享。本群私有图库所有分类合计最多 100 张。

`/image`、`/cat`、`/waifu` 的取图、列表、新上传都使用当前选中的图库；不会将本群图库作为公共图库的回退来源。**切换不会公开旧的本群图片，也不会把公共图片复制到本群。**上传时如果有人切换了模式，本次上传会拒绝入库，请重新发送。所有人可上传到选中图库；公共模式下新上传内容会公开给其他使用公共图库的群和私聊。公共图片只有服务器授权的机器人总管理员可删除；本群图片仍由本群管理员管理。总管理员可直接在私聊用 `/image list 分类 page=2` 和 `/image del 编号` 管理公共库，无需也不能在私聊切换模式。

公共图库**不设张数上限、不自动过期或清理**，但服务器磁盘并不是无限的；上传依然受命令开关、每人冷却和每日工具箱配额限制。每张最多 4MB、1200 万像素；同图库同分类内内容完全相同的存储图片不重复入库。列表分页、随机取图均不把全部公共图片编号加载进 Python 内存。

GIF 经逐帧检查后保留 QQ 提供的完整原文件，所有帧、帧时长、循环、透明和调色板信息不重新编码；原文件附带的注释/元数据也保留。安全限制为最多 1000 帧、累计 2 亿帧像素，超出时直接拒绝，不截取第一帧。GIF 发出时使用 `.gif` 文件名，能否自动播放由 QQ 客户端决定。旧版本已转成 JPEG 的动图不能恢复，需要重新上传。其他支持的格式仍转为最长边不超过 1200 像素的 JPEG；不保证保留 PNG/WebP 动画。

本次存储后端仍为本地 SQLite：数据卷内的 `/app/data/bot.sqlite3`，不需要任何云凭据。`bot_tools/gallery.py` 独立提供 `current/add/get/listing/stats/delete` 读写入口，QQ 处理层不直接执行图库 SQL，方便后续改接对象存储。**当前未实现 COS/S3 驱动或自动迁移，也没有启用云存储配置项**。以后接 COS 时应保留本地权限/索引，只替换图片载荷存储，使用私有存储桶；“机器人公共图库”不等于公网公开存储桶。迁移需复制、校验、更新索引，不能仅换地址或把已有本群图库公开。

### 全消息关键词回复

`/custom_reply` 不强制 @。当前 QQ 适配器 1.7.2 已支持 `GROUP_MESSAGE_CREATE` 全量群消息事件。平台获批并在群内启用全消息能力后，普通成员直接发送精确关键词即可触发；只有 @ 消息权限时，继续用 @机器人触发。

关键词触发后只发送已设置的回复正文，不附加“关键词回复”标题、图标或分隔线；设置、删除、列表、帮助和报错仍保留管理提示样式。回复正文里自己填写的换行和装饰不会被移除。

不要新增不存在的 `group_messages` intent，现有 `QQ_C2C_GROUP_AT_MESSAGES=true` 订阅配置保留即可。适配器的 `guild_messages` 是频道概念，不能代替普通 QQ 群全消息权限。如果使用 Webhook，还需在平台回调事件订阅中启用 `GROUP_MESSAGE_CREATE`。本地脚本不能帮机器人取得平台尚未授予的权限。

除 AI 角色收集功能外，普通群消息只做精确匹配，不写入工具箱数据库；反馈、投票等明确操作另行保存。AI 角色主人启用消息收集后，符合该角色群设置的普通文本会短暂写入本地 SQLite，有效期 30 分钟，过期内容在下次 AI 清理时删除，达到阈值的内容会发送给对应 AI 服务；图片、附件、斜杠命令和机器人消息不收集。机器人自身/其他机器人发言不触发关键词。每群关键词回复至少间隔 3 秒，每人至少 2 秒，超限静默忽略。NoneBot 自身的运行日志仍受你的日志配置控制，不等同于工具箱数据库。

### 狩猎手动时钟

此版不接 Sonar、不扫描游戏，不冒充实时狩猎网络。

```text
/group server 梦羽宝境
/hunt rule 测试怪 4 6
/hunt kill 测试怪
/hunt check 测试怪
/hunt kill 测试怪 梦羽宝境 30
/hunt list 梦羽宝境
/hunt undo 测试怪 梦羽宝境
```

示例 4～6 小时只是演示，不是任何真实怪物的刷新规则。由管理员按怪物、维护情况设置可靠的时间范围；`30` 表示击杀发生在 30 分钟前。普通成员可查看，只有管理员能记击杀、改规则、撤销。每怪物/服务器最多保留 20 次手动记录。不同群之间不共享狩猎记录。过窗口上限只提示确认漏记，不代表已经出现，也不包含天气、月相等触发条件。

### 反馈箱（按你的要求不做支持入口）

用户发送 `/comment 内容` 后，得到反馈编号。已授权的机器人主人在**私聊**使用：

```text
/comment list
/comment list 2
/comment show 12
/comment done 12
/comment closed
```

列表每页 4 条摘要；`show` 查看全文；`done` 标记已处理，不删除原文；`closed` 查看已处理列表。反馈不外发，不接原獭獭的反馈/赞助地址，没有 `/donate`。提示用户不要提交密码、Token 或其他敏感信息。反馈箱最多 10000 条，满后拒收而非静默丢失。

### 命令开关、配额

```text
/command list
/command disable cat
/command enable cat
/group quota 100
/left_reply
```

按当前会话立即生效。`/command` 可管理新工具与内置旧查询，支持已有别名；第三方 pip 插件不自动纳管。`/ping` 和管理/反馈入口不可关闭。配额只统计新版游戏/互动/图片工具的非帮助请求（含失败请求与成功关键词回复），不影响旧版 FF14 查询，也不表示腾讯官方额度。每人每天默认 100 次，北京时间 00:00 重置。

不同群可以使用不同的功能开关。例如，在群 A 发送 `/command disable ff14`，会关闭该群整套 FF14 插件（包括市场、制作、战绩、资料、天气、房屋、海钓和狩猎等命令）；在群 B 发送 `/command disable tr`，只会关闭该群的嘟嘟脸功能。分别使用 `/command enable ff14` 和 `/command enable tr` 恢复。以上命令仅限对应群的群主、管理员或已授权机器人管理员使用。

## 数据保存、升级和打包

### QQ 原生命令面板

聊天中输入 `/` 弹出的 QQ 原生面板，与 `/toolbox`、`/otter` 回复的功能目录是两个入口。原生面板每个最多 20 项（[腾讯官方限制](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_panels.post.html)）。FF14 作为插件只占一个 `/ff14` 入口，点击后显示该插件的查询目录与用法；`/market`、`/dps`、`/raid`、`/gather` 等旧指令仍能手动输入，不需要加 `/ff14` 前缀。主面板的其余位置展示通用图片、互动、管理和反馈工具。`/toolbox` 是机器人总目录，`/otter` 暂保留为它的兼容入口。

修改 `qq-menu.json` 后，在服务器执行 `menu.cmd sync`（Linux：`./menu.sh sync`）。脚本会更新现有受管面板，并回读平台配置验证；单纯重建机器人不会自动更新 QQ 面板。平台同步后如客户端仍显示旧内容，可关闭输入框中的面板再重新打开。原生 `only_admin` 仅是 QQ 点击限制，机器人仍会独立检查管理权限，不能用它授予总管理员。

### QQ 发送连接保护

QQ HTTP 请求在连接建立或连接池获取失败时，最多额外重试两次。重试复用同一请求，不会重新执行命令或改变消息序号。读取/写入超时、服务端拒绝等不会盲目重试，因为消息可能已经被接受。这只能缓解暂时断连；持续断网时仍可能无法回复。

Compose 使用名为 `bot-data` 的项目级 Docker 卷（实际名称通常为 `qqbot_bot-data`），挂载到 `/app/data`，数据库为 `bot.sqlite3`。`start`、重建镜像、`stop` 不会删除这个卷。

不要运行 `docker compose down -v`，也不要在 Docker Desktop 中删除此数据卷。换项目文件夹名会创建不同的 Compose 项目/卷；迁移时应一起迁移数据，不能只复制镜像。镜像和离线包**不包含**反馈、图库、投票、管理员权限和 `.env`。接收方从空数据库开始，授权自己的管理员。

服务器备份可先 `bot.cmd stop` / `./bot.sh stop` 停止写入，再备份整个项目对应的 Docker 数据卷（或使用 SQLite backup API），完成后 `start`。运行中的 SQLite 使用 WAL，不能只复制 `bot.sqlite3` 而丢掉旁边的 WAL 文件。

## 实现范围与数据来源

- 副属性公式：[Allagan Studies](https://www.akhmorning.com/allagan-studies/stats/)，固定 7.x / Lv.100 基准，不声称适用于未来等级上限。
- 海钓事实与轮换交叉核对：[OceanTrip 路线定义](https://github.com/catrenelle/OceanTrip/blob/master/Definitions/Routes.cs)，独立计算 UTC 两小时索引，不复制其实现代码。靛青成就路线提示参考[原项目海钓数据](https://github.com/Bluefissure/OtterBot/blob/master/ffxivbot/handlers/QQCommand_ofish.py)。红玉新海域暂用英文名；具体成就条件/钓饵攻略未内置，时刻表不保证幻海流和成就。
- 公招数据：[arkntools/arknights-toolbox-data](https://github.com/arkntools/arknights-toolbox-data)，随包固定提交与日期见 `bot_tools/data/akhr.json`，保留 MIT 许可；游戏资源权利归鹰角。更新时在源项目执行 `python tools/update_akhr_data.py 具体提交SHA` 后重新构建。运行时不下载或执行上游代码。
- 猫图：[TheCatAPI](https://thecatapi.com/)，固定公开端点，不上传 QQ 身份；外网不可达会明确提示失败。
- 对联原在线源本次未通过连通性测试；当前仅为离线词组辅助。不接异常跳转的老动漫图库，`/waifu` 使用当前选中图库内的合规素材；公共图由总管理员管理。

## English quick reference

The native QQ `/` panel is separate from the full chat command directory. Each panel allows at most 20 items. FF14 occupies exactly one plugin entry, `/ff14`, which opens its query directory. Existing `/market`, `/dps`, `/raid`, `/gather` and other FF14 commands still work directly, with no `/ff14` prefix required. Other native slots contain general tools and administration. `/toolbox` is the top-level directory and `/otter` remains its compatibility alias. Run `menu.cmd sync` or `./menu.sh sync` after editing `qq-menu.json`; the manager reads back platform state to verify updates. Rebuilding the bot alone does not update QQ panels. Reopen the client panel if it still shows cached entries. Native `only_admin` never replaces server-side role checks.

QQ transport retries connection establishment/pool-acquisition failures at most twice, using the same prepared request and message sequence. Commands are not re-executed. Read/write failures and HTTP rejections are not blindly retried, since delivery may already have occurred. Persistent network outages can still prevent replies.

Send `/toolbox` or `/otter` for the same complete command directory. New tools, including `/cat`, accept ordinary group commands when QQ grants and delivers full-message events; otherwise an @mention is still required. Private chats need no mention. This never bypasses role checks, command switches, cooldowns or quotas and cannot grant platform permissions locally. Bot-authored messages and unaddressed ordinary channel messages do not trigger the new tools. Existing FF14 tools remain available.

QQ group owners and admins automatically manage only their current group, based on the structured `author.member_role` in each QQ group event. They do not need server access. This role is not persisted as a manual grant, does not follow a user into another group/private chat, and never grants access to the global feedback inbox. Missing/unknown roles, nicknames and message text cannot elevate permissions.

In a QQ group, owners, admins, and recognized bot superadmins can use `/ginfo [page]` or `/group status [page]` for a privacy-safe overview. Each configurable property includes its command and permission hint. `/group server WORLD` sets only the default world used when `/hunt` omits one; it is not a default market data center. Market, sales, craft-cost, and cheapest-price queries accept a world, data center, or region as their final argument, and Universalis aggregates all worlds when given a data center. AI entries show only the role name and management ID—never the owner, provider, status, quota, API key, or persona. Any of these administrators can remove one role with `/ai remove ROLE_ID`; only the group owner or bot superadmin may use `all`.

`/bot status` is reserved for server-authorized superadmins. It is hidden from group owners, group admins and manually delegated ordinary admins, and direct invocation is also denied. Their current-group management tools remain available.

Only the server operator binds new bot superadmins: run `bot.cmd admin token` (Windows) or `./bot.sh admin token` (Linux) **on the server**, send `/bot whoami TOKEN` **only in your own bot private chat**, then confirm the returned identity using `admin add CODE` on the server within 15 minutes. The 32-character invitation expires after 10 minutes and is single-use; regenerating it invalidates the previous unused invitation. Redemption does not grant permissions. Only its hash is stored. Built-in application logs mask invitations in the command, but your QQ chat history still contains what you sent. Never share it. Server confirmation is also single-use. Retry the complete workflow after expiry or failed reply delivery.

In groups, `/bot` and `/bot whoami` show only the caller's role and available current-group management tools, never identity codes or binding instructions. Group owners and group admins currently have the same scoped tool permissions. Ordinary users cannot access `/bot`, `/group` or `/command` management panels; ordinary queries and games remain available. Normal commands no longer register identities. Unverified private chats cannot view the panel either. Existing public identity codes cannot bypass the new invitation flow.

Existing grants/data are preserved and remain conversation-scoped; private identities are not automatically linked to groups. New superadmin binding is private-only. Use `admin list` and `admin remove CODE` on the server to inspect/revoke legacy grants. Legacy `/group admin add/remove CODE` is retained only for existing server-authorized superadmins and existing scoped identities, is not advertised in group help, and cannot create identities or superadmins. New group admins use QQ roles; no new channel identity binding is provided.

Feedback is stored locally, never forwarded. An owner authorized in a private conversation can use `/comment list [page]`, `/comment show ID`, `/comment done ID`, and `/comment closed [page]`. The support/donation entry is intentionally not implemented.

Polls, opt-in lotteries, permissions, per-chat switches, quotas, custom replies, gallery images and manual hunt records persist in the Compose `bot-data` volume. Never delete that volume or use `down -v` unless you intend to erase data. The release image contains no credentials or user database. Each recipient authorizes their own identities.

Scope: `/fsx` uses level-100 7.x formulas without haste; `/ofish` is a version-explicit schedule, not a live fishing report; `/hunt` uses administrator-defined manual windows; `/akhr` is a bundled CN recruitment snapshot. `/tex` supports Mathtext, not arbitrary LaTeX; `/gif` creates local animated text; `/waifu` uses the selected gallery. `/duilian` is currently only a labeled offline phrase-pair draft helper because the old remote endpoint was unavailable. No AI model or automatic external hunt feed is bundled.

Gallery: QQ groups with no saved gallery preference and all private chats use the bot-wide public gallery. Private chats cannot select a local gallery. Group owners (not group admins) and server-authorized bot owners may select `/group gallery local` inside a group, or `/group gallery public` to switch back. Existing explicit group choices are preserved. Local group galleries hold 100 images across categories. The public gallery is shared within one bot, with no count cap or automatic expiry, but still limited by actual disk space. Switching never migrates or publishes existing local images. Anyone can upload into the selected gallery; only bot owners can delete public images. Local gallery deletion still requires conversation administration rights. Use `/image status` for usage and `/image list [category] [page=2]` for pagination. Valid GIFs retain their exact original bytes, including all frames/timing/looping and metadata; safety limits are 4MB, 12 megapixels per frame, 1000 frames and 200 million cumulative frame pixels. Excessive GIFs are rejected, not flattened. Old flattened GIFs require re-uploading. Other formats are still normalized to JPEG. Cooldown and daily quota remain effective. Storage remains local SQLite; `bot_tools/gallery.py` isolates the storage API for future object-storage integration. No COS/S3 driver, cloud credentials, or automatic migration is enabled in this release.
