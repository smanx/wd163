#!/bin/sh
# 容器入口：数据卷空卷时自动初始化游戏数据库，然后拉起面板（PID 1），
# AUTO_START=1 时后台自动按依赖顺序启动全部服务
LOG_DIR=/opt/wdpanel/logs
mkdir -p "$LOG_DIR"

# /var/lib/mysql 可被 -v 挂载为空卷：空卷时从镜像内置的原始数据自动初始化
if [ ! -d /var/lib/mysql/dl_adb_all ]; then
    echo "[entrypoint] empty mysql datadir, seeding from built-in pristine copy ..."
    cp -a /opt/wdpanel/mysql-pristine/. /var/lib/mysql/ && rm -f /var/lib/mysql/*.pid
    chown -R mysql:mysql /var/lib/mysql
    echo "[entrypoint] seed done"
fi

if [ "$AUTO_START" = "1" ]; then
    (
        sleep 3   # 等面板监听 8080
        python /opt/wdpanel/_start_chain.py >> "$LOG_DIR/autostart.log" 2>&1
    ) &
fi

exec python /opt/wdpanel/app.py
