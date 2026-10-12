#!/bin/bash
M="mysql -uroot -p123456"
db=dl_adb_all
$M -e "
use \`$db\`;
UPDATE cs_ccs SET ip='127.0.0.1' WHERE ip='192.168.200.128';
UPDATE spa   SET ip='127.0.0.1' WHERE ip='192.168.200.128';
UPDATE config SET value='127.0.0.1:' WHERE name IN ('mdb_host','ldb_host') AND value='192.168.200.128:';
" 2>&1
echo "--- 验证：服务地址表中还有没有老IP ---"
for t in aaa ccs csa dba cs_ccs cta spa tts config server; do
  cnt=$($M -N -e "select count(*) from (select * from \`$db\`.\`$t\`) x" 2>/dev/null | head -1)
  hits=$($M -e "select * from \`$db\`.\`$t\`" 2>/dev/null | grep -c "192.168.200.128")
  echo "$t: rows=$cnt oldip_hits=$hits"
done
echo "--- server 表（客户端入口） ---"
$M -e "select * from \`$db\`.server" 2>/dev/null | cat -v
echo ===DONE===
