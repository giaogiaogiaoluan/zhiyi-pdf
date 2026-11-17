#!/bin/zsh
# 双击运行：安装 / 升级知译 PDF 引擎并启动本机服务
set -eu
cd "${0:A:h}"
bash scripts/install.sh
echo
read -k1 "?按任意键关闭窗口…"
