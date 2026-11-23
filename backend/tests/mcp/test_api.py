import pytest


@pytest.mark.asyncio
async def test_management_access_key(mcp_client):
    response = await mcp_client.get("/api/settings/mcp/key")
    assert response.status_code == 200
    key_data = response.json()["data"]
    assert key_data["key"].startswith("otmcp_")
    assert "token_hash" not in key_data
    assert "••••••" in key_data["masked_key"]
    assert key_data["masked_key"].startswith(key_data["key"][:12])
    assert key_data["masked_key"].endswith(key_data["key"][-8:])
    assert key_data["created_at"]
    assert key_data["last_used_at"] is None

    response = await mcp_client.get("/api/settings/mcp/key")
    assert response.json()["data"]["key"] == key_data["key"]

    response = await mcp_client.post("/api/settings/mcp/key/reset")
    assert response.status_code == 200
    reset_data = response.json()["data"]
    assert reset_data["key"].startswith("otmcp_")
    assert reset_data["key"] != key_data["key"]

    response = await mcp_client.get("/api/settings/mcp/key")
    assert response.json()["data"]["key"] == reset_data["key"]


@pytest.mark.asyncio
async def test_upstream_management_does_not_expose_credentials(mcp_client, moviepilot):
    response = await mcp_client.get("/api/settings/mcp/upstreams")
    assert response.status_code == 200
    upstream = response.json()["data"][0]
    assert upstream["instance_id"] == moviepilot.id
    assert "api_key" not in upstream
    assert "url" not in upstream
    assert "enabled" not in upstream

    response = await mcp_client.put(
        f"/api/settings/mcp/upstreams/{moviepilot.id}",
        json={"enabled": True},
    )
    assert response.status_code in {404, 405}


@pytest.mark.asyncio
async def test_catalog_only_lists_onetap_tools(mcp_client):
    response = await mcp_client.get("/api/settings/mcp/catalog")
    assert response.status_code == 200
    data = response.json()["data"]
    assert [source["kind"] for source in data["sources"]] == ["local_tool"]
    assert data["sources"][0]["name"] == "OneTap"
    names = [entry["name"] for entry in data["entries"]]
    assert names == sorted(names)
    assert "onetap_search" in names
    assert "onetap_present" in names
    assert "onetap_notifications" in names
    assert all(entry["source"] == "local" for entry in data["entries"])
    assert all(entry["kind"] == "local_tool" for entry in data["entries"])
    assert {entry["risk"] for entry in data["entries"]} <= {
        "read",
        "write",
        "destructive",
    }
    assert "enabled" not in data["entries"][0]

    entry_id = data["entries"][0]["entry_id"]
    detail = await mcp_client.get(
        "/api/settings/mcp/catalog/detail", params={"entry_id": entry_id}
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["input_schema"]


@pytest.mark.asyncio
async def test_connect_info_builds_prompt_and_client_config(mcp_client):
    key = (await mcp_client.get("/api/settings/mcp/key")).json()["data"]["key"]

    response = await mcp_client.get("/api/settings/mcp/connect")
    assert response.status_code == 200
    data = response.json()["data"]

    assert data["endpoint"] == "http://test/mcp"
    assert key.startswith("otmcp_")
    assert data["masked_key"].startswith(key[:12])
    assert key not in data["masked_key"]

    assert key in data["config_json"]
    assert '"type": "http"' in data["config_json"]
    assert "http://test/mcp" in data["config_json"]

    assert "http://test/mcp" in data["prompt"]
    assert key in data["prompt"]
    assert "http://test/api/mcp/skills" in data["prompt"]
    assert "curl -fsSL" in data["prompt"]
    # 随机 API Key 本身可能包含 `__`，这里只校验模板占位符已被替换。
    for placeholder in ("__ENDPOINT__", "__KEY__", "__CLIENT_CONFIG__", "__SKILLS_URL__"):
        assert placeholder not in data["prompt"]


@pytest.mark.asyncio
async def test_connect_info_uses_browser_origin_with_port(mcp_client):
    response = await mcp_client.get(
        "/api/settings/mcp/connect",
        params={"base_url": "http://192.168.1.20:6782"},
    )
    assert response.status_code == 200
    data = response.json()["data"]

    assert data["endpoint"] == "http://192.168.1.20:6782/mcp"
    assert "http://192.168.1.20:6782/mcp" in data["config_json"]
    assert "http://192.168.1.20:6782/mcp" in data["prompt"]
    assert "http://192.168.1.20:6782/api/mcp/skills" in data["prompt"]


@pytest.mark.asyncio
async def test_connect_prompt_covers_all_resource_workflows(mcp_client):
    response = await mcp_client.get("/api/settings/mcp/connect")
    prompt = response.json()["data"]["prompt"]

    for keyword in ("电影", "电视剧", "动漫", "漫画", "音乐", "小说"):
        assert keyword in prompt
    for step in ("查询", "订阅", "下载", "整理", "通知", "替换"):
        assert step in prompt
    assert "tools/list" in prompt
    assert "系统设置 → AI接入" in prompt
    assert "MCP 聚合" not in prompt


@pytest.mark.asyncio
async def test_removed_management_endpoints_are_gone(mcp_client):
    assert (
        await mcp_client.get("/api/settings/mcp/local-tools")
    ).status_code == 404
    assert (
        await mcp_client.put("/api/settings/mcp/local-tools", json={"enabled_tools": []})
    ).status_code in {404, 405}
    assert (
        await mcp_client.put("/api/settings/mcp/api-operations", json={"operation_ids": []})
    ).status_code in {404, 405}


@pytest.mark.asyncio
async def test_skills_bundle_for_admin_and_mcp_key(mcp_client):
    response = await mcp_client.get("/api/settings/mcp/skills")
    assert response.status_code == 200
    bundle = response.json()["data"]
    paths = [item["path"] for item in bundle["files"]]
    assert "onetap/SKILL.md" in paths
    assert "onetap/references/replace.md" in paths
    skill = bundle["files"][0]["content"]
    assert "onetap_replace" in skill
    assert "订阅或下载 → 轮询进度" not in skill
    assert "不要调用 `onetap_downloads` 轮询" in skill
    assert "只有用户主动询问进度时才查询一次" in skill
    assert "## 搜索结果展示（必须遵守）" in skill
    assert "从 1 开始连续编号" in skill
    assert "编号 + 多行字段" in skill
    assert "`时长 · 码率 · 大小 · 专辑`" in skill
    assert "标题加粗" in skill
    assert "所有域都不要展示给用户" in skill
    assert "禁止 Markdown 表格" in skill
    assert "内部 ID 一律不展示" in skill
    assert "onetap_present" in skill
    assert "推送候选（图文卡片）" in skill
    assert "## 安装 / 更新引导（先做这一步）" in skill
    assert "onetap_set_notify_mode" in skill
    assert "两种通知方式的行为差异" in skill
    replace = bundle["files"][paths.index("onetap/references/replace.md")]["content"]
    assert "不要轮询或等待下载、整理、入库" in replace
    assert "完成后汇报新的文件状态" not in replace

    key = (await mcp_client.get("/api/settings/mcp/key")).json()["data"]["key"]
    response = await mcp_client.get(
        "/api/mcp/skills", headers={"Authorization": f"Bearer {key}"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["files"]

    response = await mcp_client.get(
        "/api/mcp/skills", headers={"Authorization": "Bearer otmcp_invalid"}
    )
    assert response.status_code == 401
