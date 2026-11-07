"""Docker 容器相关 API。"""

import asyncio
import json
import logging
import queue
import threading

import docker
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.core.security import get_current_user, get_current_user_sse, require_admin
from app.database import async_session
from app.models.user import User
from app.schemas.docker import (
    ContainerAction,
    ContainerActionResponse,
    ContainerBatchActionRequest,
    ContainerCreateRequest,
    ContainerDetail,
    ContainerExecRequest,
    ContainerExecResponse,
    ContainerInfo,
)
from app.services.docker import docker_manager

logger = logging.getLogger(__name__)
router = APIRouter(route_class=CustomAPIRoute)


@router.get("/status")
async def docker_status(current_user: User = Depends(get_current_user)):
    """查询 Docker 服务状态。"""
    return {"available": await docker_manager.run_sync(docker_manager.check_available)}


@router.get("/containers", response_model=list[ContainerInfo])
async def list_containers(current_user: User = Depends(get_current_user)):
    """获取所有容器列表。"""
    return await docker_manager.run_sync(docker_manager.list_containers)


@router.get("/containers/{container_id}", response_model=ContainerInfo)
async def get_container(
    container_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取单个容器信息。"""
    container = await docker_manager.run_sync(docker_manager.get_container, container_id)
    if not container:
        raise APIException("容器不存在", 404)
    return container


@router.post("/containers/{container_id}/action", response_model=ContainerActionResponse)
async def container_action(
    container_id: str,
    data: ContainerAction,
    current_user: User = Depends(require_admin),
):
    """对容器执行启动、停止、重启操作。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    try:
        return await docker_manager.run_sync(docker_manager.container_action, container_id, data.action)
    except docker.errors.NotFound:
        raise APIException("容器不存在", 404)
    except ValueError as e:
        raise APIException(str(e), 400)
    except RuntimeError as e:
        raise APIException(str(e), 500)
    except Exception as e:
        raise APIException(f"容器操作失败: {e}", 500)


@router.get("/containers/{container_id}/logs")
async def container_logs(
    container_id: str,
    tail: int = 100,
    current_user: User = Depends(get_current_user),
):
    """获取容器最近日志。"""
    logs = await docker_manager.run_sync(docker_manager.get_container_logs, container_id, tail=tail)
    return {"logs": logs}


@router.post("/containers", response_model=ContainerInfo, status_code=status.HTTP_201_CREATED)
async def create_container(
    data: ContainerCreateRequest,
    current_user: User = Depends(require_admin),
):
    """创建容器。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    try:
        return await docker_manager.run_sync(docker_manager.create_container, data.model_dump())
    except docker.errors.ImageNotFound:
        raise APIException("镜像不存在", 404)
    except docker.errors.APIError as e:
        raise APIException(f"创建容器失败: {e}", 500)
    except Exception as e:
        raise APIException(f"创建容器失败: {e}", 500)


@router.post("/containers/batch-action")
async def batch_container_action(
    data: ContainerBatchActionRequest,
    current_user: User = Depends(require_admin),
):
    """批量操作容器。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    try:
        return await docker_manager.run_sync(docker_manager.batch_container_action, data.ids, data.action)
    except ValueError as e:
        raise APIException(str(e), 400)
    except Exception as e:
        raise APIException(f"批量操作失败: {e}", 500)


@router.get("/containers/{container_id}/detail", response_model=ContainerDetail)
async def get_container_detail(
    container_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取容器完整详情。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    detail = await docker_manager.run_sync(docker_manager.get_container_detail, container_id)
    if not detail:
        raise APIException("容器不存在", 404)
    return detail


@router.get("/containers/{container_id}/logs/stream")
async def container_logs_stream(
    container_id: str,
    tail: int = 100,
    follow: bool = True,
    timestamps: bool = True,
    current_user: User = Depends(get_current_user_sse),
):
    """流式获取容器日志（SSE）。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)

    def _stream_to_queue(q: queue.Queue, stop_event: threading.Event):
        try:
            detail = docker_manager.get_container_status(container_id)
            if detail is None:
                q.put({"error": "容器不存在"})
                return
            q.put(
                {
                    "meta": {
                        "container_id": detail["id"],
                        "name": detail["name"],
                        "status": detail["status"],
                        "state": detail["state"],
                    }
                }
            )

            for line in docker_manager.stream_container_logs(
                container_id,
                tail=tail,
                follow=follow,
                timestamps=timestamps,
            ):
                if stop_event.is_set():
                    break
                q.put({"line": line})
        except docker.errors.NotFound:
            q.put({"error": "容器不存在"})
        except docker.errors.APIError as e:
            message = str(e)
            if "configured logging driver does not support reading" in message:
                q.put({"error": "容器日志驱动不支持读取，请检查容器日志驱动配置"})
            elif "No such container" in message:
                q.put({"error": "容器不存在"})
            else:
                q.put({"error": f"日志流异常: {message}"})
        except Exception as e:
            q.put({"error": f"日志流异常: {e}"})
        finally:
            q.put(None)

    async def event_generator():
        q: queue.Queue = queue.Queue(maxsize=500)
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_stream_to_queue,
            args=(q, stop_event),
            daemon=True,
        )
        thread.start()
        try:
            while True:
                try:
                    item = await asyncio.to_thread(q.get, True, 1.0)
                except queue.Empty:
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            stop_event.set()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/containers/{container_id}/exec", response_model=ContainerExecResponse)
async def container_exec(
    container_id: str,
    data: ContainerExecRequest,
    current_user: User = Depends(require_admin),
):
    """在容器内执行命令。"""
    if not await docker_manager.run_sync(docker_manager.check_available):
        raise APIException("Docker 不可用", 503)
    try:
        exit_code, output = await docker_manager.run_sync(
            docker_manager.exec_container,
            container_id,
            data.command,
            workdir=data.workdir,
            user=data.user,
            environment=[item.model_dump() for item in (data.environment or [])],
        )
        return {"exit_code": exit_code, "output": output}
    except docker.errors.NotFound:
        raise APIException("容器不存在", 404)
    except Exception as e:
        raise APIException(f"执行命令失败: {e}", 500)


@router.websocket("/containers/{container_id}/exec")
async def container_terminal_websocket(
    websocket: WebSocket,
    container_id: str,
    shell: str = "/bin/sh",
    workdir: str | None = None,
    user: str | None = None,
    cols: int = 80,
    rows: int = 24,
):
    """容器交互式终端 WebSocket。"""
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008, reason="缺少认证令牌")
        return

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        sub = payload.get("sub")
        if sub is None:
            raise JWTError
        user_id = int(sub)
    except (JWTError, ValueError, TypeError):
        await websocket.close(code=1008, reason="认证失败")
        return

    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        current_user = result.scalar_one_or_none()
    if current_user is None:
        await websocket.close(code=1008, reason="认证失败")
        return
    if current_user.role != "admin":
        await websocket.close(code=1008, reason="需要管理员权限")
        return

    if not await asyncio.to_thread(docker_manager.check_available):
        await websocket.close(code=1011, reason="Docker 不可用")
        return

    try:
        resolved_shell = await asyncio.to_thread(
            docker_manager.resolve_terminal_shell,
            container_id,
            shell,
            user=user,
        )
        exec_id, exec_socket = await asyncio.to_thread(
            docker_manager.exec_container_interactive,
            container_id,
            command=[resolved_shell],
            workdir=workdir,
            user=user,
        )
        try:
            await asyncio.to_thread(
                docker_manager._client.api.exec_resize, exec_id, height=rows, width=cols
            )
        except Exception:
            logger.debug("初始化终端尺寸失败", exc_info=True)
    except docker.errors.NotFound:
        await websocket.close(code=1011, reason="容器不存在")
        return
    except Exception as e:
        await websocket.close(code=1011, reason=f"创建 exec 会话失败: {e}")
        return

    await websocket.accept()
    if resolved_shell != shell:
        await websocket.send_text(
            f"\x1b[33m[容器未提供 {shell}，已自动切换到 {resolved_shell}]\x1b[0m\r\n"
        )

    output_queue: queue.Queue = queue.Queue(maxsize=1000)

    def _read_socket():
        try:
            exec_socket._sock.settimeout(None)
        except Exception:
            logger.debug("设置终端 socket 超时失败", exc_info=True)
        try:
            while True:
                try:
                    chunk = exec_socket._sock.recv(4096)
                except Exception:
                    break
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                try:
                    output_queue.put(text, block=False)
                except queue.Full:
                    pass
        finally:
            try:
                output_queue.put(None, block=False)
            except queue.Full:
                pass

    reader_thread = threading.Thread(target=_read_socket, daemon=True)
    reader_thread.start()

    async def _forward_to_container():
        try:
            while True:
                message = await websocket.receive_text()
                try:
                    data = json.loads(message)
                    if isinstance(data, dict) and data.get("type") == "resize":
                        new_cols = int(data.get("cols", cols))
                        new_rows = int(data.get("rows", rows))
                        try:
                            await asyncio.to_thread(
                                docker_manager._client.api.exec_resize,
                                exec_id, height=new_rows, width=new_cols,
                            )
                        except Exception:
                            logger.debug("调整终端尺寸失败", exc_info=True)
                        continue
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

                await asyncio.to_thread(
                    exec_socket._sock.sendall, message.encode("utf-8")
                )
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("终端输入转发异常", exc_info=True)

    async def _forward_to_client():
        while True:
            try:
                item = await asyncio.to_thread(output_queue.get, True, 0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            await websocket.send_text(item)

    tasks = [
        asyncio.create_task(_forward_to_container()),
        asyncio.create_task(_forward_to_client()),
    ]
    try:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        try:
            exec_socket.close()
        except Exception:
            logger.debug("关闭 exec socket 失败", exc_info=True)
        try:
            await websocket.close()
        except Exception:
            logger.debug("关闭终端 WebSocket 失败", exc_info=True)
