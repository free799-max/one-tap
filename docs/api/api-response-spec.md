# OneTap API 响应格式规范

**版本**: 1.0
**生效日期**: 2026-05-29

---

## 1. 规范概述

本文档定义 OneTap 项目所有 RESTful API 的统一响应格式。后端所有接口（除特殊说明外）必须遵循此规范，前端通过 Axios 拦截器自动解包消费。

## 2. 响应格式

### 2.1 成功响应

```json
{
  "success": true,
  "data": <业务数据>,
  "message": "ok"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | `boolean` | 固定为 `true` |
| `data` | 任意 | 实际业务数据，可为对象、数组、基础类型或 `null` |
| `message` | `string` | 固定为 `"ok"`，如有特殊业务提示可覆盖 |

**示例 — 列表数据**:
```json
{
  "success": true,
  "data": [
    { "id": 1, "name": "Jellyfin" },
    { "id": 2, "name": "Komga" }
  ],
  "message": "ok"
}
```

**示例 — 单对象数据**:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "username": "admin",
    "role": "admin"
  },
  "message": "ok"
}
```

**示例 — 无数据**:
```json
{
  "success": true,
  "data": null,
  "message": "ok"
}
```

### 2.2 错误响应

```json
{
  "success": false,
  "data": null,
  "message": "错误描述信息"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | `boolean` | 固定为 `false` |
| `data` | `null` | 固定为 `null` |
| `message` | `string` | 人类可读的错误描述 |

**示例 — 业务错误**:
```json
{
  "success": false,
  "data": null,
  "message": "用户名已存在"
}
```

**示例 — 认证错误**:
```json
{
  "success": false,
  "data": null,
  "message": "认证失败"
}
```

**示例 — 服务器错误**:
```json
{
  "success": false,
  "data": null,
  "message": "服务器内部错误"
}
```

### 2.3 特殊响应 — 204 No Content

删除操作（`DELETE`）返回 `204 No Content`，**不包装**响应体，保持空响应。

```http
HTTP/1.1 204 No Content
```

这是 RESTful 标准行为，不参与统一格式包装。

## 3. 后端开发指南

### 3.1 路由层正常返回数据

路由层**不需要手动包装**响应，直接返回数据即可。`CustomAPIRoute` 会自动包装为 `StandardResponse`。

```python
# ✅ 正确 — 直接返回数据
@router.get("/status")
async def docker_status():
    return {"available": docker_manager.available}

# ✅ 正确 — 返回 ORM 对象（依赖 response_model 序列化）
@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user
```

### 3.2 抛出业务异常

所有业务错误统一使用 `APIException`，**禁止使用** `HTTPException`。

```python
from app.core.exceptions import APIException

# ✅ 正确 — 使用 APIException
raise APIException("用户名已存在", 400)
raise APIException("认证失败", 401)
raise APIException("权限不足", 403)
raise APIException("容器不存在", 404)
raise APIException("Docker 不可用", 503)

# ❌ 错误 — 不再使用 HTTPException
# raise HTTPException(status_code=404, detail="...")
```

### 3.3 状态码规范

| HTTP 状态码 | 使用场景 | 示例 |
|------------|---------|------|
| `200` | 成功 GET / POST | 获取列表、登录成功 |
| `201` | 资源创建成功 | 注册、创建订阅 |
| `204` | 删除成功（无响应体）| 删除订阅、删除实例 |
| `400` | 请求参数错误 | 用户名已存在、缺少必填字段 |
| `401` | 认证失败 | Token 无效/过期、密码错误 |
| `403` | 权限不足 | 非 admin 尝试创建实例 |
| `404` | 资源不存在 | 容器不存在、订阅不存在 |
| `500` | 服务器内部错误 | 数据库异常、Docker 操作失败 |
| `503` | 服务不可用 | Docker 未安装或未运行 |

### 3.4 核心组件说明

| 文件 | 职责 |
|------|------|
| `app/schemas/response.py` | `StandardResponse[T]` 泛型响应模型 |
| `app/core/exceptions.py` | `APIException` 自定义业务异常 |
| `app/core/custom_route.py` | `CustomAPIRoute` 自动包装成功响应 |
| `app/main.py` | 全局异常处理器注册 |

## 4. 前端开发指南

### 4.1 自动解包

前端通过 Axios 响应拦截器自动解包 `StandardResponse`，hooks 中直接使用 `response.data` 即可。

```typescript
// api.ts 拦截器已自动提取 data 字段
api.interceptors.response.use(
  (response) => {
    if (response.status === 204) return response;
    if (response.data && typeof response.data === "object" && "data" in response.data) {
      response.data = response.data.data;  // 提取业务数据
    }
    return response;
  },
  // ...
);
```

### 4.2 消费示例

```typescript
// hooks/useDocker.ts
export function useContainers() {
  return useQuery<ContainerInfo[]>({
    queryKey: ["docker", "containers"],
    queryFn: () => api.get("/docker/containers").then((r) => r.data),
  });
}

// 页面消费
const { data: containers = [] } = useContainers();
// data 已经是 ContainerInfo[]，无需额外解包
```

### 4.3 错误处理

错误响应中 `message` 字段已附加到 `error.displayMessage`：

```typescript
// hooks/useContainerAction.ts
export function useContainerAction() {
  const toast = useToast();
  return useMutation({
    mutationFn: ({ id, action }) =>
      api.post(`/docker/containers/${id}/action`, { action }),
    onError: (error: any) => {
      toast.error(error.displayMessage || "操作失败");
    },
  });
}
```

### 4.4 401 自动处理

非登录接口的 401 响应会自动清除 token 并跳转登录页，无需手动处理。

## 5. 边界情况

| 场景 | 处理方式 |
|------|---------|
| `204 No Content` | 直接透传，不包装 |
| `StreamingResponse` | 直接透传，不包装 |
| `404 Not Found`（不存在的路由）| Starlette 默认返回 `{"detail": "Not Found"}`，实际极少触发 |
| 已经是 `StandardResponse` 的响应 | 跳过二次包装 |
| 文件下载响应 | 直接透传，不包装 |

## 6. 修改历史

| 日期 | 版本 | 变更说明 |
|------|------|---------|
| 2026-05-29 | 1.0 | 初始版本，定义统一响应格式 `{success, data, message}` |
