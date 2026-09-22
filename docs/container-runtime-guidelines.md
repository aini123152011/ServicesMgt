# Container Runtime Guidelines

> 容器内守护进程的固有限制与修法。这些坑在阶段 3 实机验收时逐个踩过：
> **共同点是「配置文件写对了，但服务没按配置工作」**，只比对渲染产物完全发现不了。

---

## 1. 渲染文件的权限必须是守护进程可读的

- **现象**：nginx 开启 Basic 认证后，带凭据的请求返回 500；容器内 error.log 报
  `open() "/etc/nginx-bmc/htpasswd" failed (13: Permission denied)`。
- **成因**：平台用 `tempfile.mkstemp` 落盘，文件权限是 0600（root only），而 nginx worker 以
  `www-data` 运行，读不到凭据文件。
- **修法**：`config_renderer._atomic_write` 统一 `os.chmod(tmp_name, 0o644)`（每次写入都重设，
  已存在的 0600 文件也会被修正）。
- **新服务自查**：守护进程是否降权运行（`user`/`User=`/`--user`）？它要读的渲染文件，
  平台写入的权限够不够？

## 2. `/var/run` 在容器里是空 tmpfs，镜像构建期建不出来

- **现象**：vsftpd 对任何连接直接回 `500 OOPS: not found: directory given in
  'secure_chroot_dir':/var/run/vsftpd/empty`；ganesha 写 pidfile 失败
  （`open(/var/run/ganesha/ganesha.pid) failed ... errno 2`）后 FATAL，监督循环反复重启。
- **成因**：`/var/run` 由容器运行时挂载为空 tmpfs，Dockerfile 里 `mkdir` 的东西在运行时不存在。
- **修法**：在 `entrypoint.sh` 启动时补建（`services/vsftpd/entrypoint.sh` 建
  `/var/run/vsftpd/empty`，`services/nfs-ganesha/entrypoint.sh` 建 `/var/run/ganesha`）。
- **新服务自查**：守护进程启动时要在 `/var/run`、`/run` 下写 pidfile/socket/锁目录吗？

## 3. 前台标志不等于监听模式

- **现象**：tftpd-hpa 容器进程在跑，UDP 69 却从不应答，且日志里反复出现
  `entrypoint: in.tftpd 已退出，2 秒后带新配置重启`。
- **成因**：`in.tftpd --foreground` 只表示「不转入后台」，并不隐含监听模式；缺 `--listen` 时它走
  inetd 模式从 stdin 读请求，容器内没有 inetd 就立刻退出。
- **修法**：显式加 `--listen`（见 `services/tftpd-hpa/entrypoint.sh`）。
- **新服务自查**：这个守护进程的 standalone 监听是否需要单独开关？容器内有没有 inetd？
  用容器内 `/proc/net/{tcp,udp}` 确认端口真的处于 LISTEN。

## 4. 宿主机发布段必须覆盖 schema 允许的端口范围

- **现象**：FTP 登录成功，`STOR` 报 `Connection refused`。
- **成因**：vsftpd 被动端口在 `pasv_min_port~pasv_max_port`（schema 默认 40000-40100）内**随机**
  选一个，而 compose 只发布了 40000 与 40100 两个端口 → 约九成数据连接落空。
- **修法**：`services/vsftpd/docker-compose.yml` 整段发布 `40000-40100:40000-40100/tcp`，
  与 schema 默认范围对齐；schema 范围若调整，发布段必须同步。
- **新服务自查**：有没有「运行时随机/协商端口」（FTP 被动、RPC、数据通道）？发布段够不够宽？

## 5. FSAL / 文件句柄类依赖需要额外权限

- **现象**：ganesha 的 EXPORT 加载失败，`resolve_posix_filesystem(/data/nfs) returned No such
  file or directory`，导出为空、客户端挂载报 ENOENT；overlay 路径则报
  `vfs_lookup_path ... Operation not supported`。
- **成因**：VFS FSAL 用 `name_to_handle_at/open_by_handle_at` 给导出目录发文件句柄，
  overlayfs 不支持，宿主 bind 挂载的 ext4 在非特权容器下也失败。
- **修法**：`services/nfs-ganesha/docker-compose.yml` 用 `privileged: true`（实测仅加
  `SYS_ADMIN`/`DAC_READ_SEARCH`、放开 seccomp/label 都不行）；导出目录必须持久化，
  tmpfs 虽可行但不能当导出目录。
- **新服务自查**：服务是否依赖文件句柄、`CAP_SYS_ADMIN`、内核特性或宿主设备？在容器里能否拿到？

## 6. 配置语法要按目标版本核对，别照搬示例

- **现象**：ganesha 报 `Expected a client, got a double quoted string for (*)`，整个 EXPORT
  校验失败、导出不加载；chrony 报 `Could not parse local directive at line 11`，chronyd 直接起不来。
- **成因**：`CLIENT { Clients = "*"; }` 的值不能加引号；`local stratum` 只接受 0–15，
  写 16（NTP 语义的「未同步」）会被拒绝——**服务不会告诉你「范围不对」，只会拒绝启动**。
- **修法**：模板去掉引号（`services/nfs-ganesha/templates/ganesha.conf.j2`）；chrony 的
  `stratum_16` 改为「上游全部 `offline` + 不配 `local` 参考」，对外应答 Stratum 0 / Not synchronised
  （`services/chrony/templates/chrony.conf.j2`）。
- **新服务自查**：模板里的每条指令都在目标版本上实测过吗？故障模式的值是否落在服务允许的范围内？
  **渲染成功 ≠ 服务能启动**——启动失败时先看容器日志里的 parse/validation 报错。

## 7. 不变量：种子文件必须等于模板默认渲染

- `defaults/<cfg>` 是空配置卷时 entrypoint 播种的内容，必须与平台渲染结果一致，
  否则「独立部署」与「平台托管」两条路径行为不同。阶段 3 曾有 6 个服务的种子落后于模板。
- 已由 `platform/backend/tests/test_service_seeds.py` 固化：改 schema 默认值或模板后必须
  重新生成种子（渲染一遍模板写回 `defaults/`），否则测试失败。

---

## 8. 访问控制类字段要按守护进程的语义写，否则是摆设

**现象**：nfs-ganesha 的 `allowed_clients` 设成不含客户端的网段后，网段外照样挂载成功；
用同一写法的 `access_denied` 故障模式同样不生效——字段看着生效，实际零约束。

**成因**：ganesha 的 `CLIENT { Clients = <cidr> }` 是「**对匹配客户端的覆盖**」，不是允许名单；
不匹配的客户端会落到**导出级**的 `Access_Type`。原模板在导出级写了 `Access_Type = RW`，
等于对所有人开放，CLIENT 块只对匹配者做了「重复授权」。

**修法**：要真正限制网段，导出级写 `Access_Type = None;`，权限放进 CLIENT 块：

```
EXPORT {
    Access_Type = None;          # 默认拒绝
    CLIENT {
        Clients = 192.168.0.0/16;
        Access_Type = RW;        # 只授给这个网段
    }
}
```

不限制（`allowed_clients = *`）时保持导出级 `Access_Type = <access_type>`，CLIENT 块只列 `Clients = *`，
这样默认渲染与既有种子文件逐字节一致（§7 的不变量）。

**推广**：任何「访问控制」字段都要先确认**语义方向**——是允许名单、拒绝名单，还是对匹配项的覆盖。
判定方式与故障注入一样：**用真实客户端从被限制的一侧试一次**（网段外挂载必须失败），
而不是看配置文件里出现了那行。同类需要留意的：samba 的 `hosts allow/deny`、vsftpd 的
`userlist_deny`、rsyslog 的 `allowed_senders`、tftpd 的 `create_enabled`。

## 9. 「reload」必须真的重读配置，别把参数缓存在循环外

**现象**：snmptrapd 的 `blackhole_drop` 故障模式把监听地址改成 `127.0.0.1:162`，但设置后外部 Trap
照样被收到；基础字段 `output_file`/`output_format` 改了同样不生效。

**成因**：这些参数只能通过**命令行**传给守护进程，模板把它们回写成 `# runtime:` 注释行，由 entrypoint 解析。
但 entrypoint 把解析放在**监督循环外面**，而 `reload.sh` 的约定是「杀掉守护进程、由监督循环重新拉起」
（不重启容器）——于是每次拉起都用容器启动时那一份旧值。

**修法**：凡是「reload = 杀进程 + 监督循环拉起」的服务，循环内每一轮都要重读渲染产物，
不能在循环外读一次就复用：

```sh
while :; do
    RUNTIME_LISTEN="$(sed -n 's/^# runtime: listen=//p' "$CONF_FILE" | tail -n 1)"
    LISTEN="${RUNTIME_LISTEN:-0.0.0.0:162}"
    snmptrapd ... "$LISTEN" &
    wait $!
done
```

**判定**：光看渲染产物会以为生效了——要验证「设置 → 重启后客户端可观测行为变化」这条完整链路。

**容器内没有 ss/netstat/ps 时怎么查监听地址**：读 `/proc/net/udp`，第 2 列是 `local_address:port`
的小端十六进制（`00000000:00A2` = 0.0.0.0:162，`0100007F:00A2` = 127.0.0.1:162）。探测时换一个
不冲突的端口，否则会读到正在运行的守护进程那一条。

## 10. 降权守护进程 + 「重载要重建 pidfile」= SIGHUP 会杀死守护进程

**现象**：chrony 每次下发配置后容器就重启一次（`RestartCount` 随下发次数增长，实测 26 次），
故障模式切换越多越慢；改成监督循环后表现为「每隔一次下发报 502：未发现运行中的 chronyd 进程」。

**成因**：chronyd 启动后按 PRIVDROP 降权到 `_chrony`，而 SIGHUP 重载配置时要**删除并重写自己的
pidfile**、重建命令套接字。这两件事对 `/run/chrony` 属主的要求互相矛盾，两种取值都死：

| `/run/chrony` 属主 | 启动 | SIGHUP 重载 |
| --- | --- | --- |
| `root:root 755`（发行版默认） | 正常，命令套接字可用 | 删不掉 pidfile → `Could not remove ... : Operation not permitted` → **退出** |
| `_chrony:_chrony 755` | `Wrong permissions on /run/chrony`，命令套接字被禁用（`chronyc` 失效） | **静默退出**（无任何日志） |

即：**「热加载」从未真正生效**，每次下发都在重启进程；因为守护进程重启后照样服务，
只看客户端行为完全发现不了，只有看 `RestartCount` 与容器日志才会暴露。

**修法**（`services/chrony/`）：放弃 SIGHUP，统一走「监督循环内重启进程」：

- `entrypoint.sh` 是 1 号进程，循环里 `chronyd ... &` + `wait`，把子进程 pid 写进
  `/run/chrony/supervisor.pid`，并 `trap` 转发 `docker stop` 的 SIGTERM；
- `reload.sh` 杀掉子进程并**等新进程就绪**（轮询 pidfile 变化），退出 0 = 新配置已加载；
- 容器不重启 → 没有 Docker 重启退避、没有重启期间 exec 被拒的窗口；NTP 中断约 1 秒。

**新服务自查**：守护进程是否降权运行？它 reload 时会不会重建 pidfile/套接字？如果会，
就不要用 SIGHUP 做热加载（尤其是模板里带 `pidfile` 指令的服务）。

## 11. 平台侧：reload 撞上容器重启窗口不能算失败

**现象**：回滚 chrony 配置的接口返回 502、版本未记录，但 NTP 应答其实已经变了（配置已生效）。

**成因**：某些服务的 reload 必然伴随进程/容器重启，重启期间 Docker 以 409 拒绝 `docker exec`
（`Conflict ("Container ... is restarting, wait until the container is running")`；退避等待期间是
`is not running`）。原实现把这个 409 直接当 reload 失败 → 平台把「正在生效」报成下发失败。

**修法**（`app/lifecycle.py: exec_reload`）：识别 409 且文案含 `restarting`/`is not running` 时
等待并重试（`RELOAD_RETRY_ATTEMPTS=5` × 3s），其他错误与非零退出码照旧立即失败。
配套单测：`platform/backend/tests/test_lifecycle_reload.py`。

**推广**：任何「平台调用外部进程/API 让配置生效」的链路，都要区分**可重试的瞬时冲突**与
**真实失败**；把前者当失败会让调用方看到假错误，进而做出错误决定（这里差点把「回滚失败」
写进结论）。

---

## 验收方式：用真实协议判定，不要比对配置文件

阶段 3 的教训是：**只 grep 渲染出的配置文件，会漏掉上面全部 7 类问题**。每个服务的验收都要有
一个真实协议客户端，从「客户端可观测的行为」判定：

| 服务类型 | 判定手段（示例见 `scripts/verify_bmc_platform_e2e.py`） |
| --- | --- |
| NTP | 发标准 SNTP 请求，看 leap/stratum/授出时间与基准的差 |
| HTTP/HTTPS | 真实 PUT/GET 文件并比对内容；Basic 认证看 401/200；HTTPS 用 `-k` 访问自签证书 |
| TFTP | 发 RRQ 收 DATA 块并比对内容 |
| FTP/SFTP/SMB | 用平台下发的账号真实登录并读写文件 |
| NFS | 真实挂载导出目录并读写 |
| Syslog | 发 BMC 风格 syslog 报文，再看归档文件与平台浏览接口能否命中关键字 |
| SNMP Trap | 发 SNMPv2c Trap，看落盘日志 |
| SMTP | 真实 SMTP 会话，看问候语、白名单拒绝与投递结果 |

故障注入的判定同理：**必须是客户端可观测的行为变化**（返回码、授出时间、拒绝原因），
而不是「模板里出现了那行配置」。

另外两个环境陷阱（与实现无关，但会让判定假失败）：
- **UDP + Docker 端口映射的回环路径**：TFTP 这类「服务端另起临时端口应答」的协议，
  经 `127.0.0.1` 访问时应答会因 NAT 会话匹配不上被丢弃；探测统一走网卡地址
  （真实 BMC 从局域网访问正常）。
- **reload 窗口**：`reload_mode=restart` 的服务在 reload 时会短暂停进程，窗口内发出的 UDP 报文
  直接丢失、配置 PUT 可能拿到 502；探测要按轮重发，PUT 要重试。
