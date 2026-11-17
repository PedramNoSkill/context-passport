# Context Passport

**切换 AI 工具，不必从头解释。**

[English](README.md) · [简体中文](README.zh-CN.md) · [Русский](README.ru.md)

将你明确选择的 Claude、Claude Code、Codex、ChatGPT 或文本会话，整理成可审查的工作交接包：目标、决策、对话中声称已完成的工作、阻碍、下一步、记录中的测试命令，以及可选的 Git 变更。每条信息都保留来源引用。

运行时完全离线，只使用 Python 标准库。不需要 API Key、模型服务、数据库或账号。需要 Python 3.10 或更新版本；只有启用 Git 快照时才需要 Git。

## 安装与体验

```bash
pip install "git+https://github.com/PedramNoSkill/context-passport.git"
context-passport demo --out ./passport-demo
```

在浏览器中打开 `passport-demo/review.html`。页面无需联网。演示对话和历史测试输出均为**明确标注的虚构示例**；输出文件由真实 CLI 生成。

本版本从 GitHub 安装，尚未发布到 PyPI。

## 创建自己的交接包

```bash
passport create \
  --from ./session.jsonl \
  --repo ./my-project \
  --target codex \
  --title "继续完成本地搜索" \
  --next "修复空结果提示，然后重新运行测试。" \
  --out ./my-passport
```

- `--from`：明确选择一个本地会话文件，不会自动扫描账号或主目录。
- `--repo`：可选。不提供时，不采集任何 Git 信息。
- `--no-diff`：只记录变更路径，不包含差异内容。
- `--conversation`：从多会话导出中选择精确 ID 或唯一标题。

输出三个文件：

- `HANDOFF.md`：交给下一个助手的说明。
- `passport.json`：带版本的结构化交接数据。
- `review.html`：离线审查页面，包含可搜索的来源证据。

```bash
passport inspect ./my-passport
passport prompt ./my-passport --target claude
passport conversations ./conversations.json
```

你决定何时分享 Markdown。程序不会登录 Claude/Codex，也不会自动把信息注入其他应用。

## 证据优先

本工具使用确定性的字面提取，**不是 LLM 摘要器**。明确的 `Goal:`、`Decision:`、`Done:`、`Next:`、`Blocked:` 标签会进入相应章节；支持 `目标:`、`决定:`、`完成:`、`下一步:`、`阻塞:`。其他文本保留在证据部分，可继续查看。

可通过 `--goal`、`--decision`、`--done`、`--next` 和 `--blocker` 添加自己的明确备注。第一条用户消息是目标的默认候选，`--goal` 可以覆盖它。

助手在对话中说“测试都通过了”，不会变成已执行的测试证据。测试记录要求工具调用与对应输出、退出码关联。退出码 0 也只表示记录中的命令成功，不代表当前代码已经验证。

Git 采集只读，不执行仓库代码，不读取未跟踪文件内容，不提交、不推送、不下载远端对象。已知凭证路径和符号链接内容不进入 diff。

## 分享前审查

常见凭证模式会在写入前被替换，包括私钥、常见 token、Authorization 头、URL 凭证和密码赋值。**这是尽力检测，不是安全保证。** 文件名、业务信息和代码仍可能敏感。请审查全部输出，详见 [隐私与信任模型](docs/PRIVACY.md)。

本版本是文件交接工具，不提供自动跨应用同步、长期记忆守护进程、MCP 服务或自动 LLM 总结。

格式与限制：[FORMATS.md](docs/FORMATS.md)。开发与测试：[CONTRIBUTING.md](CONTRIBUTING.md)。

由 [PedramNoSkill](https://github.com/PedramNoSkill) 构建，使用 MIT 许可证。
