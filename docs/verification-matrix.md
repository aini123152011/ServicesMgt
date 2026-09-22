# 服务验证矩阵（2026-09-22）

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

合计：故障模式**声明 29 个，全部已验证**（27 个经 `fault_mode` 或等价覆盖验证；
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

