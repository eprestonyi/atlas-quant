# 私有复现包下载与严格导入

新分片报告的下载入口提供确定性 USTAR 归档：`manifest.json` 在首位，随后为清单注册的 `chunks/<collection>/<ordinal>.json`。预测包包含冻结行情、来源和完整研究记录，属于用户的私有数据；下载不代表可以公开分发供应商数据。独立执行包不重复保存行情，必须另行保留其原始预测包。旧 v0.4 单包报告目前没有对应的用户自助 bundle 下载能力，不能用重新取数伪造历史冻结输入。

使用系统 Python 标准库即可导入，无需安装研究引擎依赖。目标目录必须不存在，父目录须已存在：

```bash
python3 scripts/extract-bundle.py atlas-quant-run-bundle.tar private/reproduced

# 执行记录包必须提供已验证的原始预测目录，内含冻结行情。
python3 scripts/extract-bundle.py atlas-quant-execution-bundle.tar private/execution \
  --source-bundle private/reproduced
```

成功输出包含原独立审计摘要、`bundleId`、归档文件数/字节数和 `published:true`。可以将 `bundleId` 与原报告固定版本及下载响应 `X-Atlas-Quant-Bundle-Id` 对照。预测包导入后，可按原有命令重新审计，或在已安装锁定研究依赖的环境中只重放执行：

```bash
python3 scripts/audit-bundle.py private/reproduced
PYTHONPATH=engine python scripts/replay-execution.py \
  --source-bundle private/reproduced --bundle-output private/replayed-execution
```

CLI 不是通用 tar 解包器。它逐个读取512字节 header，使用 `TarInfo.frombuf` 检查校验和，随后只接受导出器的精确 USTAR 编码：普通文件 type `0`、0600、uid/gid/mtime均0、空链接和用户名字段。PAX/GNU扩展、软硬链接、目录成员、路径穿越、重复或遗漏成员、非规范ordinal、非零padding、body/hash不匹配和超预算均拒绝。即使普通 tar 工具认为缺少结束块的归档“可读”，这里仍要求**恰好两个512字节零结束块，之后立即EOF**；通用tar工具额外补齐的零块也不属于这个协议。

单个manifest不超过512KiB、chunk不超过8MiB、至多256个chunk；归档总长度还受256MiB分片预算和固定tar开销限制。现有独立bundle审计继续执行其更严格的完整bundle字节、集合行数、文档布局、计划覆盖、引用和会计校验，不因封装成tar放宽。只读取一个有界body，不使用 `tarfile.open(...).extractall()`，也不预先读取可能被恶意扩展header声明的大对象。

解包先写入同一文件系统的随机私有临时目录，全部独立审计通过后才原子发布。目录0700、文件0600；失败清理本次临时内容，不覆盖已有用户文件。macOS 使用 `renamex_np(RENAME_EXCL)`、Linux 使用 `renameat2(RENAME_NOREPLACE)`；不支持安全原子不覆盖发布的平台明确失败，不静默退化成可能覆盖空目录的普通POSIX rename。Windows依赖 `os.rename` 的不覆盖行为，但本轮实测平台为macOS。若发布后父目录fsync不可用，结果保留已成功发布的有效目录并显式报告warning，不将已提交目录误报为解包失败。

## HTTP 与独立复现验收

下载入口只接受当前工作区已完成运行对应的已提交包，并固定 bundleId。分片逐一读取并检查原字节大小及 SHA-256；使用 Workers 原生 FixedLengthStream 设置准确 HTTP 长度。实际 Miniflare/R2 损坏反例确认客户端收到失败，不能将被截断的 body 当作完整下载。慢消费和取消通过独立流测试；这不等于互联网带宽或生产延迟保证。

2026-10-08，固定的 50 股八年/32 因子合成研究包经本地真实 Worker/D1/R2 HTTP 下载，归档 60,082,176 字节、37 个文件。Python 3.12.13 使用 `-S`、不加载研究引擎导入；424,135 项独立核对通过，包含 19,700 主预测、19,700 基线、394 账本日期，最大数值差 2.11e−12。源包和提取目录逐文件哈希核对；无重新拟合或 provider 调用。归档 SHA-256 为 `e68656da8d735103c15932dcec5627c61bd808b9a05211bc35126adbfc562788`。这个验收验证数据和传输完整性；该合成案例没有实际交易，不能作为市场预测能力证据。

本轮新增 44 项严格导入测试，相关标准库审计测试合计 58 项；Node 全套 122 项通过。实际 in-app browser 点击复现包下载，得到相同 60,082,176 字节与 SHA-256，和上述独立审计文件逐字相同。390×844 的真实页面无横向溢出，两个下载入口均可见；DOM 行为另有回归。此处为隔离本地 Worker 验收，生产下载尚待发布后核对。
