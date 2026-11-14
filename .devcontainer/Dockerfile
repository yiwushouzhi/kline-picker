FROM mcr.microsoft.com/devcontainers/python:3.13-bookworm

# PySide6 离屏测试所需的系统库；成品仍在 Windows 构建。
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y --no-install-recommends \
       libdbus-1-3 \
       libegl1 \
       libfontconfig1 \
       libgl1 \
       libxcb-cursor0 \
       libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*
