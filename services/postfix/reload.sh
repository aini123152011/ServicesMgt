#!/bin/bash
# 统一生效入口：平台通过 docker exec fx-postfix /reload.sh 触发配置生效。
# 为什么是重启而非热加载：postfix reload 只让运行中的守护进程重读部分参数，
# 官方文档要求 inet_interfaces 等参数变更、以及 master.cf 进程结构变更必须
# stop + start（重启进程）才能完全生效，因此统一按重启路径处理，
# manifest 声明 reload_mode=restart。
# 实现方式：postfix stop 优雅结束 master，entrypoint 的监督循环检测到退出后自动拉起。
set -euo pipefail

# postfix status 非 0 说明 master 未在运行，reload 无从谈起，非 0 退出让平台感知失败
if ! postfix status >/dev/null 2>&1; then
    echo "reload: 未发现运行中的 postfix 进程，无法生效" >&2
    exit 1
fi

postfix stop
echo "reload: postfix 已停止，监督循环将带新配置重启"
