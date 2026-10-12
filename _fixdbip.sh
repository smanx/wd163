#!/bin/bash
# 修复 dl_adb_all 中所有服务地址表的老 IP -> 127.0.0.1
# 用 IP 字符串本身匹配（ASCII 安全，避开 GBK 中文比较问题）
M="mysql -uroot -p123456"
db=dl_adb_all

echo "--- 先看各表结构 ---"
for t in aaa ccs csa dba cs_ccs cta spa tts config; do
  echo "== $t =="
  $M -N -e "describe \`$db\`.\`$t\`" 2>/dev/null | awk '{print $1":"$2}' | tr '\n' ' '
  echo
done

echo "--- 执行更新（含192.168.200.128的所有列） ---"
# 逐表更新：直接把所有文本列里的 192.168.200.128 替换掉
$M -e "
use \`$db\`;
UPDATE aaa   SET ip='127.0.0.1'   WHERE ip='192.168.200.128';
UPDATE ccs   SET ip='127.0.0.1'   WHERE ip='192.168.200.128';
UPDATE csa   SET ip='127.0.0.1'   WHERE ip='192.168.200.128';
UPDATE dba   SET ip='127.0.0.1'   WHERE ip='192.168.200.128';
UPDATE cta   SET ip='127.0.0.1'   WHERE ip='192.168.200.128';
UPDATE tts   SET ip='127.0.0.1'   WHERE ip='192.168.200.128';
" 2>&1

# cs_ccs / spa / config 列名未知，先打印再动态处理
echo "--- cs_ccs 数据 ---"
$M -e "select * from \`$db\`.cs_ccs" 2>/dev/null | cat -v | cut -c1-200
echo "--- spa 数据 ---"
$M -e "select * from \`$db\`.spa" 2>/dev/null | cat -v | cut -c1-200
echo "--- config 数据 ---"
$M -e "select * from \`$db\`.config" 2>/dev/null | cat -v | cut -c1-200
echo ===DONE===
