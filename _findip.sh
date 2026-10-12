#!/bin/bash
# 检查所有 dl_* 库中哪些表的数据里含老 IP
M="mysql -uroot -p123456 -N"
dbs=$($M -e "show databases" 2>/dev/null | grep '^dl_')
for db in $dbs; do
  tables=$($M -e "show tables from \`$db\`" 2>/dev/null)
  for t in $tables; do
    # 搜索含 192.168 的行
    hits=$($M -e "select * from \`$db\`.\`$t\`" 2>/dev/null | grep -l "192.168" >/dev/null 2>&1 && echo yes || true)
    cnt=$($M -e "select * from \`$db\`.\`$t\`" 2>/dev/null | grep -c "192.168")
    if [ "$cnt" != "0" ] && [ -n "$cnt" ]; then
      echo "HIT: $db.$t ($cnt rows)"
      $M -e "select * from \`$db\`.\`$t\`" 2>/dev/null | grep "192.168" | head -3 | cut -c1-200
    fi
  done
done
echo ===DONE===
