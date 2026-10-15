# A31 Wi-Fi 接收路径崩溃修复

本仓库默认构建当前路由器可用的 `kmod-mt_wifi` 修复包，机型为
KONKA KOMI A31 / E-Life ETR631-T / ETR635-U（mediatek/mt7981）。

## 构建基线

- 源码固定到 hanwckf/immortalwrt-mt798x 的 `ba554197ed7e252fd3c1ee023621e1d1d009d323`。
- `.config` 来自当前路由器 `/etc/build.config`。
- `feeds.conf.default` 固定到当前路由器 `/etc/build.feeds` 记录的四个提交。
- Wi-Fi 驱动版本 7.6.7.3，包发布号由 1 升为 2。
- 必须匹配内核依赖 `5.4.284-1-4c477d255c38d4ac64c5d98b9d3353e7`，
  模块 vermagic 为 `5.4.284 SMP mod_unload aarch64`。

补丁来自 [chasey-dev 的修复提交](https://github.com/chasey-dev/immortalwrt-mt798x-rebase/commit/3fdf8c56c66874558f3efad6a8efb8c040442eb6)，
修复 `rx_802_3_data_frm_announce()` 中二次查找站点失败后解引用空指针的问题。
该故障与本机 iPhone 接入后的 panic 栈和原模块指令相吻合。

## 使用 GitHub Actions

推送构建相关文件到 main 会启动驱动构建，也可在 Actions → Build OpenWrt → Run workflow 手动启动。
默认 `full_firmware=false`。需要完整固件时可勾选该选项；固件部署需另行核对分区、备份和迁移。
脚本不会调用旧 `diy-part1.sh` 的 UA3F 克隆或修改默认网络地址。

成功后下载 `mt_wifi-fixed-konka-a31-*`，其中包括 IPK、SHA256 和 `verification.json`。
工作流核对包内核依赖、两个模块的架构和 vermagic，并直接检查包内模块的函数重定位：
旧 `MacTableLookup` 调用必须消失，`RTMPCheckWPAframe` 安全检查必须保留。
任何检查失败都不会上传标记为可用的驱动产物；诊断日志会单独上传。

## 部署与验收

构建完成不代表路由器已经修复。安装前应再次核对路由器内核依赖与 `verification.json`，
备份原驱动和配置，并准备有线管理连接。不要忽略依赖或强制加载不匹配的模块。
安装和重启会中断无线连接，应在确定维护时间后执行。

单包更新无需重写 `/etc/config`：保留现有 AdGuard/NAS DNS 旁路与放行规则、NAT6、
Sunshine IPv6 转发及 NAS 8081 转发。完整固件请勿未经迁移直接刷写。

运行验收：iPhone 多次断开/连接与休眠唤醒后 uptime 连续、没有新同类 pstore panic；
同时确认无线、A/AAAA、AdGuard 日志、v2rayA 代理、IPv6、Sunshine 和 NAS 转发正常。
