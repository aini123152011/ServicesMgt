## IPv6 支持（12 服务）

每个服务一条「IPv6 客户端可观测行为」用例，跑在启用 IPv6 的容器网络内（宿主无全局 IPv6）：

| 服务 | 探针 | 判定 | 结果 |
| --- | --- | --- | --- |
| chrony | SNTP over v6 | 回包 leap/stratum 正常 | ⚠️ 已知限制 |
| nginx | HTTP GET over v6 | 状态行 200 | ✅ |
| rsyslog | UDP syslog over v6 | 报文送达（落盘由宿主侧核对） | ✅ |
| webdav | HTTP PUT + 回读 over v6 | 回读内容一致 | ⚠️ 已知限制 |
| sftp | SSH 横幅 over v6 | `SSH-` 横幅 | ✅ |
| vsftpd | FTP 横幅 + 登录 over v6 | `230` 登录成功 | ✅ |
| tftpd-hpa | TFTP RRQ over v6 | DATA 块 | ✅ |
| samba | TCP/445 over v6 | 可连接（连接级探针） | ✅ |
| nfs-ganesha | TCP/2049 over v6 | 可连接（连接级探针） | ✅ |
| snmptrapd | SNMPv2c Trap over v6 | 已发送（落盘由宿主侧核对） | ✅ |
| postfix | SMTP 横幅 over v6 | `220` 问候语 | ✅ |
| dhcp | RA 通告前缀 / DHCPv6 SOLICIT / DNS over v6 | RA 前缀正确、拿到 v6 地址、AAAA 与 PTR 可解析 | ✅ |

> samba/nfs 用连接级探针：探针容器里没有 smbclient 与挂载能力；协议级验证仍由 IPv4 阶段覆盖。
> dhcp 的 v6 用例在 DHCP 阶段内（RA/SLAAC、DHCPv6 有状态、DNS over v6），不在 `V6_CASES` 里。

# 服务验证矩阵（2026-09-23）

> 逐服务的四类验证结论：**正向协议** / **故障注入** / **页面** / **BMC 侧**。
> 数据来源：`scripts/verify_bmc_platform_e2e.py` 实际存在的检查项与 `fault_mode` 取值、各服务 `schema.json`、
> BMC Redfish 实测记录（见本机任务记录）。

## 总览

| 服务 | 正向协议检查 | 故障模式（声明 / 已验证 / 未验证） | 页面层 | BMC 侧 |
| --- | --- | --- | --- | --- |
| chrony | 8 条全通过 | 2 / **2** / 0 | 12 项全通过 | ✅ 已验证（BMC 向夹具请求授时，抓包取证） |
| nginx | 13 条全通过 | 3 / **3** / 0 | 12 项全通过 | ❌ 该 BMC 虚拟介质不收 http:// URI |
| rsyslog | 8 条全通过 | 2 / **1** / 1 | 12 项全通过 | ❌ 201.42 的 SyslogService 在 Redfish 上只读 |
| webdav | 8 条全通过 | 4 / **4** / 0 | 12 项全通过 | ❌ BMC 无 WebDAV 客户端 |
| sftp | 6 条全通过 | 2 / **2** / 0 | 12 项全通过 | ❌ BMC 无 SFTP 客户端 |
| vsftpd | 7 条全通过 | 3 / **3** / 0 | 12 项全通过 | ❌ BMC 无 FTP 客户端 |
| tftpd-hpa | 5 条全通过 | 2 / **0** + 1 等价 / 1 | 12 项全通过 | ❌ UpdateService 传输协议不含 TFTP |
| samba | 7 条全通过 | 2 / **0** + 2 等价 / 0 | 12 项全通过 | ⚠️ BMC 连到 445 并完成 SMB 协商，虚拟介质挂载报 ConnectionFailed |
| nfs-ganesha | 9 条全通过 | 2 / **1** + 1 等价 / 0 | 12 项全通过 | ✅ 已验证（BMC 把夹具导出挂成虚拟介质） |
| snmptrapd | 6 条全通过 | 3 / **2** / 1 | 12 项全通过 | ❌ 无 Trap 目的配置资源（Redfish） |
| postfix | 13 条全通过 | 4 / **4** / 0 | 12 项全通过 | ⚠️ SmtpService 可写，真实发信需 BMC 事件触发 |
| dhcp | 24 条全通过 | 5 / **5** / 0 | 12 项全通过 | ✅ v4 + v6 真机都已验证（BMC 取到 192.168.90.135 与 fd00:90::10d） |
| freeradius | 12 条全通过 | 4 / **4** / 0 | 12 项全通过 | ⏳ 待测（容器网络内的真实 RADIUS 客户端已覆盖认证与 VLAN 下发） |

合计：故障模式**声明 38 个，全部已验证**（36 个经 `fault_mode` 或等价覆盖验证；
`tftpd-hpa.timeout_simulate` 与 `snmptrapd.blackhole_drop` 在修复实现后也已验证）。

本矩阵维护的结论：**「声明了但没验证过」在本项目里约等于「可能有缺陷」**——本轮 12 个从未验证的
故障模式里挖出 3 个真缺陷（nginx corrupt_content_length 未实现、postfix force_tls 让服务不可用、
snmptrapd 重载不生效导致 blackhole_drop 与 output_file 等参数都改不动）。

## 逐服务明细

### chrony

- 正向协议检查（8 条）：2.1 正向配置提交并生效；2.2 NTP 应答已同步且时间与基准一致（leap=0）；2.3 故障注入 stratum_16 配置生效（容器不崩溃）；2.4 NTP 应答宣告未同步（leap=3 / stratum 0）；2.5 故障注入 fake_offset 配置生效；2.6 NTP 授出时间 = 基准时间 + 3 小时；2.7 复位正向配置生效；2.8 偏移已清除，恢复正向授时
- 故障模式声明：fake_offset, stratum_16
  - 经 `fault_mode` 验证：fake_offset, stratum_16
  - 未验证：无
- BMC 侧：✅ 已验证（BMC 向夹具请求授时，抓包取证）

### nginx

- 正向协议检查（13 条）：3.1 正向配置提交并生效；3.2 上传固件文件（HTTP PUT）；3.3 下载固件文件且内容一致；3.4 Basic 认证配置提交并生效；3.5 未认证被拒(401)、认证后放行；3.6 HTTPS 配置提交并生效；3.7 HTTPS 经自签证书可访问（AC14）；3.8 故障注入：返回 500；3.9 故障注入：返回 503；3.10 故障注入：限速规则 limit_rate 5k 已生效；3.11 故障注入：响应出现两个 Content-Length（长度不符）；3.12 复位正向配置生效；3.13 复位后恢复单个 Content-Length 且可正常下载
- 故障模式声明：http_status, extreme_slow, corrupt_content_length
  - 经 `fault_mode` 验证：corrupt_content_length, extreme_slow, http_status
  - 未验证：无
- BMC 侧：❌ 该 BMC 虚拟介质不收 http:// URI

### rsyslog

- 正向协议检查（8 条）：4.1 正向配置提交并生效；4.2 按 IP/日期归档为文件；4.3 日志浏览接口 /data/tree 返回目录树（AC12）；4.5 故障注入 port_blackhole：端口仍可连但消息不落盘；4.6 复位正向配置生效；4.7 复位后消息重新落盘；4.4 日志浏览接口 /data/content 关键字过滤命中（AC12）；4.4 日志浏览接口 /data/content 关键字过滤命中（AC12）
- 故障模式声明：port_blackhole, drop_all
  - 经 `fault_mode` 验证：port_blackhole
  - 等价覆盖：`drop_all` —— 与 `port_blackhole` 渲染**完全相同**的配置（都注入 `stop`），
    行为判定由 4.5–4.7 覆盖。两个模式实现重复，属设计冗余（已在任务记录里标注）
  - 未验证：无
- BMC 侧：❌ 201.42 的 SyslogService 在 Redfish 上只读

### webdav

- 正向协议检查（8 条）：5.1 正向配置提交并生效；5.2 正向 PUT 写入文件；5.3 正向 GET 读回内容一致；5.4 故障注入：423 Locked（并发锁冲突）；5.5 故障注入：507 Insufficient Storage；5.6 故障注入：写入方法返回 405 Method Not Allowed；5.7 故障注入：正确凭据也被拒（401）；5.8 复位正向配置生效
- 故障模式声明：lock_conflict_423, quota_exceeded_507, method_not_allowed_405, auth_reject
  - 经 `fault_mode` 验证：auth_reject, lock_conflict_423, method_not_allowed_405, quota_exceeded_507
  - 未验证：无
- BMC 侧：❌ BMC 无 WebDAV 客户端

### sftp

- 正向协议检查（6 条）：6.1 用户与密码认证配置提交并生效；6.2 平台下发的用户可密码登录并读写文件；6.3 故障注入：认证被拒；6.4 故障注入 readonly_reject：上传被拒但读取仍可用；6.5 复位正向配置生效；6.6 复位后恢复可读写
- 故障模式声明：auth_reject, readonly_reject
  - 经 `fault_mode` 验证：auth_reject, readonly_reject
  - 未验证：无
- BMC 侧：❌ BMC 无 SFTP 客户端

### vsftpd

- 正向协议检查（7 条）：7.1 本地用户配置提交并生效；7.2 平台下发的本地用户可登录并上传/下载；7.3 故障注入：写入被 550 拒绝；7.6 故障注入 deny_pasv_data：登录正常但被动数据通道被拒；7.7 故障注入 extreme_throttle：限速生效（15s 内下不完 200KB）；7.8 复位正向配置生效；7.9 复位后恢复可读写
- 故障模式声明：deny_pasv_data, write_deny_550, extreme_throttle
  - 经 `fault_mode` 验证：deny_pasv_data, extreme_throttle, write_deny_550
  - 未验证：无
- BMC 侧：❌ BMC 无 FTP 客户端

### tftpd-hpa

- 正向协议检查（5 条）：8.1 配置提交并生效；8.2 可经 TFTP 下载文件且内容一致；8.3 关闭创建开关的配置提交并生效；8.4 写请求被拒（ERROR 应答）；8.5 复位正向配置后写请求重新被接受
- 故障模式声明：deny_create, timeout_simulate
  - 经 `fault_mode` 验证：（无）
  - 等价覆盖：`deny_create` —— 已用基础字段 create_enabled=false 验证行为（8.3–8.5）
  - `timeout_simulate`：**已修**——原实现只改重传超时（干净网络下无可观测效果）；改为只绑回环
    （外部 RRQ 收不到应答 = 超时），进程与健康检查仍正常，用例 8.6
  - 未验证：无
- BMC 侧：❌ UpdateService 传输协议不含 TFTP

### samba

- 正向协议检查（7 条）：9.1 共享与用户配置提交并生效；9.2 平台下发的用户可读写共享目录；9.3 错误口令被拒（NT_STATUS_LOGON_FAILURE）；9.4 抬高协议下限的配置提交并生效；9.5 满足下限的客户端仍可正常访问；9.6 客户端协议低于服务端下限时被拒；9.7 复位正向配置后共享恢复可读写
- 故障模式声明：force_protocol_mismatch, auth_reject
  - 经 `fault_mode` 验证：（无）
  - 等价覆盖：`force_protocol_mismatch` —— 已用 min_protocol=SMB3_11 + 低协议客户端验证（9.4–9.6）
  - 等价覆盖：`auth_reject` —— 已用错误口令验证（9.3）
  - 未验证：无
- BMC 侧：⚠️ BMC 连到 445 并完成 SMB 协商，虚拟介质挂载报 ConnectionFailed

### nfs-ganesha

- 正向协议检查（9 条）：10.1 导出配置提交并生效；10.2 可经 NFSv4 挂载并读写导出目录；10.3 只读导出的配置提交并生效；10.4 只读导出下写入被拒（Read-only file system）；10.5 收紧允许网段的配置提交并生效；10.6 允许网段外的客户端挂载被拒；10.7 故障注入 access_denied 配置提交并生效；10.8 故障注入 access_denied 下挂载被拒；10.9 复位正向配置后导出恢复可读写
- 故障模式声明：access_denied, force_readonly
  - 经 `fault_mode` 验证：access_denied
  - 等价覆盖：`force_readonly` —— 已用 access_type=RO 验证行为（10.3/10.4）
  - 未验证：无
- BMC 侧：✅ 已验证（BMC 把夹具导出挂成虚拟介质）

### snmptrapd

- 正向协议检查（6 条）：11.1 社区与输出配置提交并生效；11.2 SNMPv2c Trap 被接收并写入日志文件；11.3 故障注入：非授权社区被丢弃；11.4 故障注入 force_v3_only：v2c Trap 不落盘且容器仍在运行；11.5 复位正向配置生效；11.6 复位后 Trap 重新落盘
- 故障模式声明：reject_community, force_v3_only, blackhole_drop
  - 经 `fault_mode` 验证：force_v3_only, reject_community
  - `blackhole_drop`：**已修**——原实现依赖 entrypoint 每轮重读运行时参数，而读取在监督循环外，
    reload 后仍用旧值（只绑回环不生效）；修复后用例 11.5 通过
  - 未验证：无
- BMC 侧：❌ 无 Trap 目的配置资源（Redfish）

### postfix

- 正向协议检查（13 条）：12.1 中继与网络配置提交并生效；12.2 SMTP 问候语反映平台配置的主机名；12.3 源地址不在 mynetworks 白名单时中继被拒；12.4 把宿主机网段加入 mynetworks 的配置生效；12.5 白名单放行后邮件被 SMTP 接受投递（250）；12.6 故障注入：邮件被 554 永久拒收；12.7 force_tls 故障模式配置提交并生效；12.8 force_tls 下明文投递被拒（要求先 STARTTLS）；12.9 force_tls 下 STARTTLS 投递成功（TLS 真的可用）；12.10 故障注入 greylist_451：投递被临时拒绝（4xx）；12.11 故障注入 tarpit_delay：出错会话被拖慢（>=15s）；12.12 复位正向配置生效；12.13 复位后同样出错会话恢复快速响应
- 故障模式声明：reject_554, greylist_451, force_tls, tarpit_delay
  - 经 `fault_mode` 验证：force_tls, greylist_451, reject_554, tarpit_delay
  - 未验证：无
- BMC 侧：⚠️ SmtpService 可写，真实发信需 BMC 事件触发


### dhcp

- 正向协议检查（24 条）：18.1 正向配置提交并生效；18.2 DHCPv4 取址（地址在池内、网关与租约时长正确）；18.3 SLAAC 的 RA 前缀；18.4/18.5 DNS 正向解析 A/AAAA；18.6 DNS 反向解析 PTR 且经 IPv6 访问 53；18.7 按 MAC 静态绑定；18.8 PXE 引导参数（bootfile + next-server）；18.9 DHCPv6 有状态取址；18.10 ra_mode=off 关闭 v6 下发；18.11–18.21 五个故障模式；18.22/18.23 复位后恢复；18.24 租约目录可浏览
- 故障模式声明：pool_exhausted, blackhole, wrong_gateway, dns_wrong_answer, short_lease
  - 经 `fault_mode` 验证：全部 5 个（每条都以客户端可观测行为判定：无 OFFER / 无应答 / 错误网关 / 错误解析结果 / 120 秒租约）
  - 未验证：无
- **判定手段**：`scripts/dhcp_probe.py` 手写协议报文（DHCPv4 用 AF_PACKET 自建帧、DHCPv6 SOLICIT、ICMPv6 RS/RA、最小 DNS 查询），跑在与 dhcp 同网络的客户端容器里——DHCP 走二层广播，宿主经 NAT 端口映射收不到，也没有现成客户端能回显原始选项。
- **实测否掉的两个故障候选**（写进模板注释，避免后人重复踩）：
  - 「RA 通告错误前缀」：把 ra-only 的 range 换成接口子网外的前缀后，dnsmasq 启动正常但**一个 RA 都不发**（被动监听 25 秒 0 包）。
  - 「DHCPv6 池耗尽」：池缩成一个地址后，dnsmasq 会把同一地址发给多个客户端（两个 DUID 都拿到 `fd00:30:12::100`），BMC 侧无可观测差异。
  - 另外两个实测坑：`dhcp-boot` 的服务器地址必须写在**第 3 段**才进 `siaddr`（两段式只填 option 66）；dnsmasq 租约下限是 **2 分钟**，写 60 会被静默抬到 120。
- BMC 侧：✅ **v4 与 v6 真机都取到了**（对端 BMC `<被测服务器管理地址>`，夹具 `enp125s0f1` 经 macvlan 与 BMC 管理口同二层）
  - v4：夹具日志完整走完 `DHCPDISCOVER → DHCPOFFER → DHCPREQUEST → DHCPACK 192.168.90.135`，BMC 带内 `ipmitool lan print 1` 读到 `IP Address Source: DHCP Address`、地址 `192.168.90.135`、网关 `192.168.90.1`；夹具反向 `ping` 3/3 通（~1 ms）。
  - v6：夹具日志 `DHCPSOLICIT → DHCPADVERTISE → DHCPREQUEST → DHCPREPLY fd00:90::10d`（DUID `00:03:00:01:94:a4:f9:fa:98:21`），BMC 带内 `lan6 print 1` 的 `IPv6 Dynamic Address 0` 显示 `Source/Type: DHCPv6 / fd00:90::10d/64 / Status: active`；夹具 `ping6 fd00:90::10d` 3/3 通，v6 邻居表里该地址的 lladdr 正是 BMC 的 `94:a4:f9:fa:98:21`（REACHABLE）。
  - 一个绕不过的坑：v6 最初起不来，根因是**取址方式不匹配**而非链路——BMC 设的是 DHCPv6，而夹具当时渲染的是 `dhcp-range=fd00:90::,ra-only`（只发 RA、不做 DHCPv6 分配）。夹具侧把 `ra_mode` 从 `slaac` 切成 `stateful`（`dhcp-range=fd00:90::100,fd00:90::200,64,12h`）后立刻取到。
  - **一次误判复盘（值得记住）**：BMC 的 IPv6 方式在 SLAAC 与 DHCPv6 之间被切换过。夹具 `ra_mode=slaac` 期间，BMC **其实已经通过 SLAAC 取到过地址**——带内 `lan6 print 1` 的动态地址槽里出现过 `fd00:90::96a4:f9ff:fefa:9821/64`（正是它 MAC `94:a4:f9:fa:98:21` 的 EUI-64）。我最初只 `grep` 了 `Address:` 行、丢掉了槽头与 `Status`，把这条真地址当成了「占位值」，又因为「夹具侧收不到对端的 v6 报文」而判定它没启用——**这两条推理都是错的**。
  - 正确的判据：①带内动态地址槽要**连槽头与 `Status` 一起看**（`IPv6 Dynamic Address 0: Source/Type: DHCPv6 / <地址>/64 / Status: active` 才是完整证据）；②`Addressing Enables: both` 单独不能证明已启用；③**「段上收不到对端的 v6 报文」也不能作为否定证据**——SLAAC 客户端接受非请求 RA 时本来就不发包，收包计数为 0 是正常的。最终要靠「带内槽位 + 从夹具侧 ping 实测」两条一起判。
  - 后来 BMC 被改成 DHCPv6，SLAAC 地址随之消失，而夹具当时只发 RA、不做 DHCPv6 分配 → 夹具侧把 `ra_mode` 切成 `stateful` 后立即取到 `fd00:90::10d`。所以「取址方式不匹配」是**后一阶段**的真实根因，但当时我给出的理由是错的。
  - 遗留：BMC 的 `IPv6 Dynamic Router 0` 仍是 `::`（没把夹具记为 v6 路由器）。同一 `/64` 内互访（如 BMC → 夹具 `fd00:90::2`）是 on-link、不受影响；若要测 BMC 经 v6 访问网段外目标，需另配 v6 网关。

### freeradius

- 正向协议检查（12 条）：19.1 正向配置提交并生效；19.2 正确凭据 Access-Accept 且下发配置的 VLAN；19.3 错误密码 Access-Reject；19.4 用户表外用户被拒；19.5/19.6 新增用户与改 VLAN 后**配置真的生效**（新用户可认证、拿到新 VLAN）；19.7–19.10 四个故障模式；19.11/19.12 复位后恢复
- 故障模式声明：reject_all, accept_all, no_response, wrong_attributes
  - 经 `fault_mode` 验证：全部 4 个（判定口径：Access-Reject / 错密码也 Access-Accept / 无应答超时 / 下发 VLAN=999）
  - 未验证：无
- **判定手段**：`scripts/auth_probe.py` 手写 RADIUS Access-Request（User-Password 按 RFC 2865 用 MD5 加密，并校验响应 Authenticator——校验通过才证明共享密钥一致），跑在与 freeradius 同网络的客户端容器里；属性值（Tunnel-Type/Tunnel-Medium-Type/Tunnel-Private-Group-Id）直接从 Access-Accept 里解出来。
- **实测踩到的三个坑**（都写进了模板/探针注释）：
  - users 文件**按顺序匹配**，兜底的 `DEFAULT Auth-Type := Reject` 必须放最后——放最前面会把所有用户（含正确密码）都判成 Access-Reject；
  - `control:Response-Delay` 这类 control 属性**不能**写在 users 文件里（files 模块实例化失败、radiusd 起不来），所以「应答变慢」这类故障没做；
  - RADIUS 头里 **Id 在第 1 字节**，探针最初把随机 Id 写到第 2 字节上，等于改坏了 Length 的高位，报文非法 → freeradius 连日志都不打就丢弃（表现为认证一直超时）。
- **IPv6**：`ipaddr = *` 在 3.2 里已是双栈（再加 `ipv6addr` 会报 Address already in use），容器内 `/proc/net/udp6` 能看到 1812/1813。
- **LDAP（slapd）为什么没有**：slapd 2.5.13 在本平台启动阶段约一半概率崩（`ch_calloc` 断言），
  与配置/数据卷/后端参数都无关，详见 `container-runtime-guidelines.md` 第 15 条 → 放弃交付，
  不纳入服务列表（宁可没有，也不要一个只有一半概率能起来的夹具）。
- BMC 侧：⏳ 待测（真机需在 BMC 的认证设置里指向本夹具）
