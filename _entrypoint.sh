#!/bin/sh
# 容器入口：先拉起面板（PID 1），AUTO_START=1 时后台自动按依赖顺序启动全部服务
LOG_DIR=/opt/wdpanel/logs
mkdir -p "$LOG_DIR"

if [ "$AUTO_START" = "1" ]; then
    (
        sleep 3   # 等面板监听 8080
        python /opt/wdpanel/_start_chain.py >> "$LOG_DIR/autostart.log" 2>&1
    ) &
fi

exec python /opt/wdpanel/app.py
