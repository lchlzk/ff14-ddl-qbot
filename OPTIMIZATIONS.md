# 优化版升级说明 / Optimized release

## 一键安装包

保留全部机器人功能，包括 FF14、AI 角色、公式图片、中文动态文字、完整 GIF、图库、群学习和网页后台。镜像仅保留代码实际使用的完整常规 CJK 字体；排除项目字节码和已知依赖的自测文件，不删除运行所需的字体数据、`numpy.testing`、许可证或加密组件。依赖不预生成字节码，首次导入有少量编译开销。

Windows 运行 `package.cmd`，Linux 运行 `./package.sh`，会同时生成 `release/` 和可直接发布的 `QQbot-one-click.zip`。ZIP 内含离线镜像、Windows/Linux 启动器、配置模板和说明，不包含制作者的 `.env`、数据库或图片。打包时校验 ZIP 完整性并输出大小及 SHA256。接收方仍按原流程解压并运行 `bot.cmd start` 或 `./bot.sh start`。

## 消息与后台性能

- 中文账号能正常登录；非法长度的密码不能通过校验。密码计算不占用数据库写锁，并防止重置密码与登录并发造成旧密码重新生效。
- 后台阻塞查询在线程池执行。群名同步不再阻塞异步主线程。
- 没有 AI 角色的群使用只读快速路径。普通学习消息只读配置，不统计完整词库。
- 全局过期清理每分钟执行，完整性检查每天执行；每个群的清理使用单独事务。清理只涉及原有过期运行记录和无引用学习内容，不自动删除公共图库原图。
- 群和角色在数据库中搜索、分页；角色群授权也可分页。后台切换页面不再额外请求完整概览，过时请求会取消。
- FF14 相同在途查询合并，公开响应缓存按条数和内存容量双重限制。AI 付费请求不会合并、自动重试或自动切换模型。
- AI 全局最多同时执行 4 次、同一会话最多 2 次，等待队列最多 64 次、每会话最多 16 次。采用轮流调度；排队超时或队列满会在扣额度和请求模型前拒绝。
- AI 和图片复用 HTTP 连接，密钥仍只放在对应请求中，不作为共享客户端默认值；不接收跨用户会话 Cookie。

## 图片、备份与回退

新增图库原图以内容哈希保存到数据卷的 `media/objects/`，数据库保存分类、权限范围及文件引用。同一原图可复用文件，但公共/私有访问权限仍按各自记录分别判断。本群图库仍最多 100 张，公共图库不设张数上限。

旧图库会在后台分批迁移。开始前先在 `backups/before-gallery-files-*.sqlite3` 保存并检查数据库备份；每张图片写入文件、同步到磁盘并读回验证后，才清空该行旧 BLOB。失败时保留未迁移原图，混合存储期间照常读取。不会自动 VACUUM，已有数据库文件不会立刻缩小，迁移备份和原图副本会临时增加磁盘占用。

后台列表使用 `media/thumbnails/` 中的缩略图，点击查看完整原图。**GIF 原文件、帧数、时序和循环均不改变；仅列表预览取第一帧。** 磁盘空间不足时停止新增图片，不以自动删除老图片腾空间。

备份时应备份整个 `bot-data` 数据卷，而不是只复制 `bot.sqlite3`。删除图库记录不会立刻物理清除共享原图副本，避免影响其他分类或群；本版不自动回收这些文件。

如需回退至只支持数据库 BLOB 的旧镜像，先保持本版运行，在服务器的项目/安装目录执行：

```text
docker compose exec -T qqbot python -m bot_tools.gallery restore-blobs
```

确认所有图片已恢复数据库格式后再回退镜像。该命令会暂停自动文件迁移，保留文件副本。不要直接恢复很早的整库备份，否则会丢失备份后的其他业务数据。

之后想重新启用文件存储，可运行：

```text
docker compose exec -T qqbot python -m bot_tools.gallery migrate
```

不要执行 `docker compose down -v`，该选项会删除数据卷。

## English summary

All bot features are retained. Builds keep the complete regular CJK font, omit known dependency self-tests and bytecode, and produce a validated offline `QQbot-one-click.zip` on both Windows and Linux. Login validation, database hot paths, SQL pagination, bounded/fair AI admission, connection reuse, public request coalescing and thumbnail previews are optimized.

Gallery originals are content-addressed local files; permissions remain per gallery record. Legacy BLOBs are migrated in small batches only after a verified database backup and byte-for-byte file verification. Animated GIF originals are unchanged. Back up the entire data volume. Before downgrading to a BLOB-only release, run the `restore-blobs` command above and wait for completion. No active public images or unreferenced shared object files are automatically deleted by this release.
