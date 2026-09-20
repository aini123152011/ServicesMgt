# 构建入口 —— 全部转发给 scripts/build.sh，构建逻辑唯一来源
SHELL := /bin/bash

.DEFAULT_GOAL := help

.PHONY: help services build check push

help: ## 显示可用目标
	@grep -E '^[a-zA-Z_%-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

services: ## 列出全部可构建服务
	scripts/build.sh --list

build: ## 本地构建（当前平台，--load）；SERVICE=chrony 指定单服务
ifdef SERVICE
	scripts/build.sh $(SERVICE)
else
	scripts/build.sh
endif

check: ## 多架构(arm64+amd64)构建校验，不产出镜像（CI 同款校验）
	PLATFORMS="linux/arm64,linux/amd64" scripts/build.sh --check

push: ## 多架构构建并推送；用法: make push REGISTRY=ghcr.io/owner [TAG=latest]
	@test -n "$(REGISTRY)" || { echo "用法: make push REGISTRY=ghcr.io/owner [TAG=latest]"; exit 1; }
	PLATFORMS="linux/arm64,linux/amd64" REGISTRY="$(REGISTRY)" $(if $(TAG),TAG=$(TAG),) PUSH=1 scripts/build.sh
