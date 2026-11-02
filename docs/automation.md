# 主页数据与自动更新

## 贡献口径

`scripts/update_contributions.py` 通过 GitHub GraphQL API 的
`user(login: "boyfeiyu").pullRequests` 分页读取本人发起的 PR。只有公开、
仓库所有者不是 boyfeiyu、状态为 `OPEN` 或 `MERGED` 的条目写入快照。
开放包含草稿，关闭且未合并的 PR 不统计。这里不是 GitHub 贡献日历的总贡献数，
也不是所有历史贡献的完整数量。

每条记录包含仓库、原始标题、PR URL、状态、是否草稿、创建/更新时间和合并时间。
标题说明提交的修改；开放 PR 不表示维护者已经接受。主页展示贡献数最多的四个项目，
每个项目分别选最近更新的最多两条已合并和两条开放 PR；完整清单在
[contributions.md](contributions.md)，机器可读数据在
[contributions.json](../data/contributions.json)。

API 错误、分页重复/不完整、字段不一致时退出失败，保留上次成功快照与核验时间。
不会把未知状态当成零贡献。`fetchedAt` 表示实际成功读取数据的时间，不是 PR 合并时间。
刷新会重新生成贡献卡、主页标记区和完整清单，标记区外的个人介绍保留。

## GitHub Actions

[Refresh profile](https://github.com/boyfeiyu/boyfeiyu/actions/workflows/main.yml)
每天 **02:17 UTC / 北京时间 10:17** 运行，也支持手动运行和主页相关 `main` 分支推送。
GitHub 的定时任务可能延迟；准确更新时间请看快照及 Actions 结果。

- `test`：不访问 API，运行测试并确认快照可重现。PR 上只运行此只读任务。
- `contributions`：使用自动提供的 `GITHUB_TOKEN`（通过 `GH_TOKEN` 传给 gh），读取公开 PR，
  以 `github-actions[bot]` 提交生成的文件到 `main`。
- `snake`：独立生成明暗两版 SVG，验证后以同一机器人身份提交到既有 `output` 分支。
  保留分支历史，仅更新两个蛇图文件，不强推。不依赖贡献抓取任务成功。

自动写入仅在 `boyfeiyu/boyfeiyu` 的 `main` 运行，只有两个发布任务授予 `contents: write`。
不需要 PAT、不添加 Multica 定时任务。机器人生成提交与本人开发提交分别署名。
使用 SHA 固定 checkout v6.1.0 与 snk v3.5.0（Node 24）；升级时重新核对来源。

成功的数据刷新会提交新的真实核验时间，保持仓库有实际更新活动。连续失败可能再次触发
GitHub 的闲置停用规则，应留意 Actions 失败通知。分支保护如果禁止机器人直接写入，
任务会明确失败；不要为了主页刷新关闭保护或强推。

## 停更原因与恢复

2026-11-02 只读检查发现：旧工作流 `generate animation` 状态是
`disabled_inactivity`；最新 `output` 提交停在 2023-10-04；推送触发写的是
`master`，仓库默认分支实际为 `main`。旧运行列表为空，没有可核验的历史失败日志，
不能把旧 action 的版本直接判成某次运行失败的原因。

发布此修改后检查工作流是否已恢复。更改 cron 可以使因闲置而停用的工作流重新激活；
若仍显示 disabled，用有仓库写权限的认证账号启用，再手动运行一次：

```sh
gh workflow enable main.yml --repo boyfeiyu/boyfeiyu
gh workflow run main.yml --repo boyfeiyu/boyfeiyu --ref main
```

这两条命令是发布后的操作，不属于本地测试，也未由开发阶段执行。
确认两个发布任务成功，以及 `main` 的数据时间和 `output` 两个 SVG 均刷新。
如果 `output` 分支被删除，先由发布者恢复该分支，再运行工作流。

相关文档：[GitHub 定时触发与闲置停用](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、
[启用/停用工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)、
[snk 官方用法](https://github.com/Platane/snk#usage)。

## 本地验证

Python 3.9+，仅标准库；实时刷新另需已认证的 GitHub CLI（Actions runner 已提供）。

```sh
python3 -m unittest discover -s tests -v
python3 scripts/update_contributions.py --check
# 读取实时公开数据并更新文件：
python3 scripts/update_contributions.py
# 离线重新渲染已有规范化快照：
python3 scripts/update_contributions.py --snapshot data/contributions.json
```
