"""生成 AI Agent 一键接入提示词与 MCP 客户端配置。"""

import json

_PROMPT = """你是 AI 助手。请按下面三步把 OneTap 接入当前环境并装好技能，完成后我就能让你管理电影、电视剧、动漫、漫画、音乐、小说资源的查询、订阅、下载、整理、通知和替换。

注意：下面的地址和密钥是敏感凭据，只写进本机配置文件，不要打印到聊天或提交到 Git。

## 第 1 步：注册 MCP 服务
- 名称：onetap
- 类型：Streamable HTTP
- 地址：__ENDPOINT__
- 请求头：Authorization: Bearer __KEY__

按你所在客户端选一种：
1. Claude Code：执行
   claude mcp add --transport http onetap __ENDPOINT__ --header "Authorization: Bearer __KEY__"
2. Cursor / Windsurf / Trae 等读取 mcp.json 的客户端：把下面配置合并进 MCP 配置文件（Cursor 为 ~/.cursor/mcp.json，或项目内 .mcp.json）：
```json
__CLIENT_CONFIG__
```
3. 其他客户端：按它的文档填写地址与请求头；只支持 stdio 的可以先用 `npx -y mcp-remote __ENDPOINT__ --header "Authorization: Bearer __KEY__"` 桥接。

## 第 2 步：下载并安装技能
用下面的命令取技能包（JSON，含要写入的文件清单）：
   curl -fsSL -H "Authorization: Bearer __KEY__" __SKILLS_URL__

按返回的 `files[].path` / `files[].content` 原样写入，路径映射：
- Claude Code：~/.claude/skills/<path>（例如 ~/.claude/skills/onetap/SKILL.md）
- Cursor：把文件合并为 .cursor/rules/onetap.mdc
- Codex CLI 或其他以 AGENTS.md 为约定的客户端：追加到当前项目的 AGENTS.md

取不到技能包时，让用户打开 OneTap「系统设置 → AI接入」→「AI 一键接入」重新复制提示词，不要自己编造技能内容。
保存后重新加载技能或规则。

## 第 3 步：自检并告诉我结果
1. 重新连接 MCP，调用 tools/list，确认能看到 onetap 的工具。
2. 选一个只读工具实际调用一次，不要添加订阅或改配置。
3. 汇报：MCP 是否连通、工具数量、技能文件写到哪，然后问我先查什么。

任何一步失败就停下来，报告失败的步骤、原始错误和已尝试的修正。"""


def build_client_config(endpoint: str, key: str) -> dict:
    return {
        "mcpServers": {
            "onetap": {
                "type": "http",
                "url": endpoint,
                "headers": {"Authorization": f"Bearer {key}"},
            }
        }
    }


def build_client_config_json(endpoint: str, key: str) -> str:
    return json.dumps(build_client_config(endpoint, key), ensure_ascii=False, indent=2)


def build_connect_prompt(endpoint: str, key: str, skills_url: str) -> str:
    return (
        _PROMPT.replace("__ENDPOINT__", endpoint)
        .replace("__KEY__", key)
        .replace("__CLIENT_CONFIG__", build_client_config_json(endpoint, key))
        .replace("__SKILLS_URL__", skills_url)
    )
