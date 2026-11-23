# quant-lab 第 41 轮轻量合成研发归档

这是已签收的轻量研发发布子集。签收覆盖 320/320 项回归，0 失败、0 跳过。使用 Windows CPython 3.10.11 和随附的本地 vendor 源码，从本目录运行以下命令。此目录不依赖 pip 安装。

```powershell
$syntheticPython = (Get-Command python.exe).Source
& $syntheticPython --version
& $syntheticPython -I -S -B .\run_synthetic.py
& $syntheticPython -I -S -B .\smoke40.py
```

后一条命令仅复现三个已签收的开发样例并在本目录下保存新的本地回执。它需要写权限。参见[维护指南](round40-guide.md)、[冻结能力清单](round40-capabilities.md)、[技术债](round40-closeout.md)及[签收报告](round41-signoff.md)。签收报告与维护指南为公开副本，已替换本机解释器路径和省略材料的链接。

## 文件校验

从本目录执行以下 PowerShell 命令，对照[发布清单](publication-manifest.json)校验文件内容与大小。清单覆盖自身以外的归档目录文件。Git 提交和清单可核对内容一致性，独立来源仍需另行认证。

```powershell
$m = Get-Content -LiteralPath .\publication-manifest.json -Raw | ConvertFrom-Json
foreach ($entry in $m.files.PSObject.Properties) {
    $file = Get-Item -LiteralPath $entry.Name -ErrorAction Stop
    if ($file.Length -ne $entry.Value.bytes -or
        (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value.sha256) {
        throw ('文件不匹配 ' + $entry.Name)
    }
}
```

## 本地证据与复现范围

发布内容提供轻量回归和三个既有合成样例所需的源码与输入。Windows 解释器须单独安装。约 1.19 GB 的完整本地归档保留原始签收文件及第 40、41 轮完整证据清单、历史原始日志、逐路径结果和重复运行输出。发布副本省略这些材料、历史报告运行预留记录、独立数值审查专用脚本及其部分输入，以及原有 60 行留出和关联账本。只用发布副本无法复核完整 515/418 项证据、重放全部历史研究或恢复原留出流程。发布清单记录完整本地清单的历史摘要，公开子集采用单独的逐文件清单。第 33–38 轮的部分旧清单作为回归所读的固定输入保留，它们引用的原始证据不保证随附。

正式 22 项仍为 `not_executed`。原 60 行留出未评分，历史首次使用未知，三项就绪为 `no`。真实研究、Qlib、生产与交易继续 No-Go，正式研究准入为 `insufficient_evidence`。
