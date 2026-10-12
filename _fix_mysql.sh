#!/bin/bash
# v4: --init-file 方式重置密码 + 清理匿名用户
echo "== 清场 =="
pkill -9 -f mysqld_safe 2>/dev/null
pkill -9 -f 'libexec/mysqld' 2>/dev/null
sleep 3
rm -f /var/lib/mysql/mysql.sock /var/lib/mysql/*.pid

cat > /tmp/init.sql <<'EOF'
UPDATE mysql.user SET Password=PASSWORD('123456') WHERE User='root';
DELETE FROM mysql.user WHERE User='';
FLUSH PRIVILEGES;
EOF

echo "== 带 init-file 正常授权模式启动 =="
/usr/libexec/mysqld --init-file=/tmp/init.sql --user=mysql --datadir=/var/lib/mysql --socket=/var/lib/mysql/mysql.sock >/tmp/mysqld_init.log 2>&1 &
sleep 10
tail -3 /tmp/mysqld_init.log

echo "== 验证 =="
mysql -uroot -p123456 -e "SELECT Host,User FROM mysql.user WHERE User='root';"
echo "== TCP 远程验证 =="
mysql -uroot -p123456 -h127.0.0.1 -e "SELECT 'tcp-ok';"
