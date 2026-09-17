# 网页管理后台 / Web administration

网页后台和机器人运行在同一个 Docker 容器中，直接读取同一份 SQLite 数据。默认只监听宿主机的 `127.0.0.1:8080`，不依赖第三方云服务；后台使用一套由服务器主人设置的独立账号密码。

## 打开后台

先确认机器人正在运行。首次使用或忘记密码时，在服务器终端执行：

```text
Windows：bot.cmd web account
Linux：./bot.sh web account
```

按提示输入用户名、密码并再次确认密码。密码输入时不会显示。设置完成后打开：

```text
http://127.0.0.1:8080/admin
```

使用刚设置的账号密码登录。浏览器会话有效 12 小时；连续输错 5 次会暂停登录 5 分钟。密码仅以随机盐和 PBKDF2-SHA256 哈希保存在 `bot-data` 数据卷，无法从数据库还原，也不会写入 `.env`、镜像或操作记录。重新运行 `web account` 可以修改用户名或重置密码，并会立即注销全部旧网页会话。

查看和撤销网页会话：

```text
Windows：bot.cmd web status
Windows：bot.cmd web revoke-all

Linux：./bot.sh web status
Linux：./bot.sh web revoke-all
```

`revoke-all` 会立即注销所有网页后台浏览器，但不会修改账号密码，也不影响 QQ 总管理员授权。

## 后台能做什么

- 添加、修改、停用或删除多个 QQ 机器人凭据，并从页面顶部选择当前管理的机器人。AppSecret 保存后不会再显示；新增会立即连接，停用或删除会立即断开，修改凭据和连接配置会自动重连，不中断其他机器人。
- 查看运行时间、数据库、群、AI 角色、图库、学习回复和反馈数量。
- 搜索群名、短标识或默认小区，并修改群显示名称、默认狩猎小区、工具箱额度、公共/本群图库、命令开关与群聊学习设置。
- 按群、状态或文字搜索“问句 → 回答”学习词库，并禁用或恢复不合适的回答。后台不会为此额外保存完整群聊历史。
- 搜索 AI 角色名、角色编号、群名或授权编号；查看或永久删除角色，并管理群授权（暂停、恢复、清空记忆或移出某个群）。删除角色会同时删除它的密钥、全部群授权和聊天记忆，但保留账号及当日用量记录。
- 全局暂停或恢复 AI 角色聊天。
- 严格分开浏览公共图库与群私有图库；私库可按所属群筛选，每张私有图片都显示群名和短标识，并可删除指定图片。
- 查看反馈全文，标记为完成或重新打开。
- 查看网页后台的登录和修改记录。

群设置、AI 角色与群授权、AI 总开关、学习词库和反馈箱按当前机器人隔离。公共图库跨机器人共享；群私有图库仍按群隔离。同一个 QQ 用户从任意机器人打开蜡笔板时使用同一份进度。若 QQ 平台将同一账号在不同应用下返回成不同 OpenID，则需要后续增加显式账号绑定后才能识别为同一用户。

为了保护群友隐私，后台不会显示 AI API Key、加密密文、角色人设、角色所属群友或 QQ 原始 OpenID。机器人收到群消息后会尝试通过腾讯群资料接口同步官方群名；这个接口可能只对白名单机器人开放。无法自动取得时，可在“群设置 → 群显示名称”手动填写。手动名称优先显示，留空则恢复使用自动取得的官方群名；短标识始终保留用于区分同名群。

群聊学习默认关闭，但学习库范围默认是公开。启用后，公开群学到的问答可在其他公开群参与回复；群主可切换成“本群私有学习库”来隔离内容。网页上的开关先显示待保存状态，点击“保存本群设置”后才正式生效。

## 从另一台电脑访问

最安全的方法是使用 SSH 隧道，不要把管理端口直接暴露到公网：

```text
ssh -L 8080:127.0.0.1:8080 服务器用户名@服务器地址
```

保持 SSH 窗口运行，然后在自己电脑打开 `http://127.0.0.1:8080/admin`。账号密码仍应仅在服务器终端设置。

如果以后使用 HTTPS 反向代理，请保持机器人端口只监听 `127.0.0.1`，并在 `.env` 设置：

```text
WEB_ADMIN_SECURE_COOKIE=true
```

然后运行 `bot.cmd start` 或 `./bot.sh start` 重新载入配置。不要在裸 HTTP 下设为 `true`，否则浏览器不会发送安全 Cookie；也不要在没有 HTTPS、访问控制和防火墙的情况下把后台暴露到公网。

## 数据与恢复

优化版后台使用数据库分页与缩略图，磁盘不足时会显示提醒。图库原件已改为数据卷内的独立文件，升级迁移、整卷备份和回退步骤见 [优化版升级说明](OPTIMIZATIONS.md)。

后台修改直接写入现有 `bot-data` 数据卷，并记录到 `web_audit`。QQ AppSecret 使用 `bot-data` 中独立生成的 `secrets/qq-bots-master.key` 加密；恢复时必须同时恢复 SQLite 数据库和这个主密钥，任何一个丢失都无法解密凭据。删除图片、移出群角色、清空角色记忆等操作需要二次确认，但仍建议在批量管理前备份整个数据卷。更新和重启时不要执行 `docker compose down -v`。

---

The web console runs in the same container and uses the same SQLite database. Set or reset its administrator account with `bot.cmd web account` or `./bot.sh web account`, then open `http://127.0.0.1:8080/admin`. QQ bot credentials are managed there: AppSecrets are encrypted with a separate data-volume master key and never returned by the API. Multiple bots share the public gallery and Trickcal board progress, while group settings, AI data, learning data, and feedback are filtered by the selected bot. Adding a bot connects it immediately; disabling or deleting it disconnects it immediately; credential and connection changes reconnect only that bot. Back up both the database and `secrets/qq-bots-master.key`. Browser sessions last 12 hours and can all be revoked with `web revoke-all`. For remote administration, prefer an SSH tunnel; enable `WEB_ADMIN_SECURE_COOKIE=true` only behind HTTPS.
