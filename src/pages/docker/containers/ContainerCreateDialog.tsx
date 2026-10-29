/**
 * 创建容器弹窗组件
 *
 * 支持基础信息、端口映射、环境变量、卷挂载配置。
 */

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useCreateContainer } from "@/hooks/useDocker";
import { Plus, Trash2, Loader2, Container } from "lucide-react";

interface ContainerCreateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type PortMapping = { container: string; host: string };
type EnvVar = { key: string; value: string };
type VolumeMount = { host: string; container: string; mode: "rw" | "ro" };

export function ContainerCreateDialog({
  open,
  onOpenChange,
}: ContainerCreateDialogProps) {
  const create = useCreateContainer();

  const [image, setImage] = useState("");
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [network, setNetwork] = useState("");
  const [restartPolicy, setRestartPolicy] = useState<
    "no" | "unless-stopped" | "always" | "on-failure"
  >("no");
  const [autoStart, setAutoStart] = useState(true);
  const [ports, setPorts] = useState<PortMapping[]>([{ container: "", host: "" }]);
  const [envs, setEnvs] = useState<EnvVar[]>([{ key: "", value: "" }]);
  const [volumes, setVolumes] = useState<VolumeMount[]>([
    { host: "", container: "", mode: "rw" },
  ]);

  const resetForm = () => {
    setImage("");
    setName("");
    setCommand("");
    setNetwork("");
    setRestartPolicy("no");
    setAutoStart(true);
    setPorts([{ container: "", host: "" }]);
    setEnvs([{ key: "", value: "" }]);
    setVolumes([{ host: "", container: "", mode: "rw" }]);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!image.trim()) return;

    const data = {
      image: image.trim(),
      name: name.trim() || undefined,
      command: command.trim() || undefined,
      network: network.trim() || undefined,
      restart_policy: restartPolicy,
      auto_start: autoStart,
      ports: ports.filter((p) => p.container.trim() && p.host.trim()),
      environment: envs.filter((e) => e.key.trim()),
      volumes: volumes.filter((v) => v.host.trim() && v.container.trim()),
    };

    create.mutate(data, {
      onSuccess: () => {
        resetForm();
        onOpenChange(false);
      },
    });
  };

  const updatePort = (index: number, field: keyof PortMapping, value: string) => {
    const next = [...ports];
    next[index][field] = value;
    setPorts(next);
  };

  const addPort = () => setPorts([...ports, { container: "", host: "" }]);
  const removePort = (index: number) => {
    const next = ports.filter((_, i) => i !== index);
    setPorts(next.length ? next : [{ container: "", host: "" }]);
  };

  const updateEnv = (index: number, field: keyof EnvVar, value: string) => {
    const next = [...envs];
    next[index][field] = value;
    setEnvs(next);
  };

  const addEnv = () => setEnvs([...envs, { key: "", value: "" }]);
  const removeEnv = (index: number) => {
    const next = envs.filter((_, i) => i !== index);
    setEnvs(next.length ? next : [{ key: "", value: "" }]);
  };

  const updateVolume = (index: number, field: keyof VolumeMount, value: string) => {
    const next = [...volumes];
    if (field === "mode") {
      next[index][field] = value as "rw" | "ro";
    } else {
      next[index][field] = value;
    }
    setVolumes(next);
  };

  const addVolume = () =>
    setVolumes([...volumes, { host: "", container: "", mode: "rw" }]);
  const removeVolume = (index: number) => {
    const next = volumes.filter((_, i) => i !== index);
    setVolumes(next.length ? next : [{ host: "", container: "", mode: "rw" }]);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(open) => {
        if (!open) resetForm();
        onOpenChange(open);
      }}
    >
      <DialogContent className="sm:max-w-3xl max-h-[85vh] overflow-hidden flex flex-col p-0">
        <DialogHeader className="px-6 pt-5 pb-2 border-b shrink-0">
          <DialogTitle className="text-lg font-semibold flex items-center gap-2">
            <Container className="h-5 w-5 text-muted-foreground" />
            创建容器
          </DialogTitle>
        </DialogHeader>

        <form
          onSubmit={handleSubmit}
          className="flex flex-col flex-1 overflow-hidden"
        >
          <div className="flex-1 overflow-y-auto px-6 py-4">
            <Tabs defaultValue="basic" className="w-full">
              <TabsList className="mb-4">
                <TabsTrigger value="basic">基础</TabsTrigger>
                <TabsTrigger value="ports">端口</TabsTrigger>
                <TabsTrigger value="env">环境变量</TabsTrigger>
                <TabsTrigger value="volumes">卷挂载</TabsTrigger>
              </TabsList>

              <TabsContent value="basic" className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="image">镜像 *</Label>
                  <Input
                    id="image"
                    value={image}
                    onChange={(e) => setImage(e.target.value)}
                    placeholder="例如 nginx:latest"
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="name">容器名称</Label>
                  <Input
                    id="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="可选"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="command">启动命令</Label>
                  <Input
                    id="command"
                    value={command}
                    onChange={(e) => setCommand(e.target.value)}
                    placeholder="例如 nginx -g 'daemon off;'"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="network">网络</Label>
                  <Input
                    id="network"
                    value={network}
                    onChange={(e) => setNetwork(e.target.value)}
                    placeholder="例如 bridge 或自定义网络名称"
                  />
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="restart">重启策略</Label>
                    <select
                      id="restart"
                      value={restartPolicy}
                      onChange={(e) =>
                        setRestartPolicy(e.target.value as typeof restartPolicy)
                      }
                      className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
                    >
                      <option value="no">不重启</option>
                      <option value="unless-stopped">除非手动停止</option>
                      <option value="always">总是重启</option>
                      <option value="on-failure">失败时重启</option>
                    </select>
                  </div>
                  <div className="space-y-2 flex flex-col justify-center">
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={autoStart}
                        onChange={(e) => setAutoStart(e.target.checked)}
                        className="rounded border-input"
                      />
                      创建后自动启动
                    </label>
                  </div>
                </div>
              </TabsContent>

              <TabsContent value="ports" className="space-y-3">
                {ports.map((port, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <Input
                      placeholder="容器端口，如 80/tcp"
                      value={port.container}
                      onChange={(e) => updatePort(index, "container", e.target.value)}
                      className="flex-1"
                    />
                    <span className="text-muted-foreground">{"->"}</span>
                    <Input
                      placeholder="宿主机端口，如 8080"
                      value={port.host}
                      onChange={(e) => updatePort(index, "host", e.target.value)}
                      className="flex-1"
                    />
                    <Button
                      type="button"
                      size="icon-xs"
                      variant="ghost"
                      onClick={() => removePort(index)}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={addPort}
                  className="text-xs"
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  添加端口
                </Button>
              </TabsContent>

              <TabsContent value="env" className="space-y-3">
                {envs.map((env, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <Input
                      placeholder="变量名"
                      value={env.key}
                      onChange={(e) => updateEnv(index, "key", e.target.value)}
                      className="flex-1"
                    />
                    <span className="text-muted-foreground">=</span>
                    <Input
                      placeholder="变量值"
                      value={env.value}
                      onChange={(e) => updateEnv(index, "value", e.target.value)}
                      className="flex-1"
                    />
                    <Button
                      type="button"
                      size="icon-xs"
                      variant="ghost"
                      onClick={() => removeEnv(index)}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={addEnv}
                  className="text-xs"
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  添加环境变量
                </Button>
              </TabsContent>

              <TabsContent value="volumes" className="space-y-3">
                {volumes.map((volume, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <Input
                      placeholder="宿主机路径"
                      value={volume.host}
                      onChange={(e) => updateVolume(index, "host", e.target.value)}
                      className="flex-1"
                    />
                    <span className="text-muted-foreground">:</span>
                    <Input
                      placeholder="容器路径"
                      value={volume.container}
                      onChange={(e) => updateVolume(index, "container", e.target.value)}
                      className="flex-1"
                    />
                    <select
                      value={volume.mode}
                      onChange={(e) => updateVolume(index, "mode", e.target.value)}
                      className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                    >
                      <option value="rw">读写</option>
                      <option value="ro">只读</option>
                    </select>
                    <Button
                      type="button"
                      size="icon-xs"
                      variant="ghost"
                      onClick={() => removeVolume(index)}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={addVolume}
                  className="text-xs"
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  添加卷挂载
                </Button>
              </TabsContent>
            </Tabs>
          </div>

          <div className="flex justify-end gap-2 px-6 py-3 border-t shrink-0">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
            >
              取消
            </Button>
            <Button type="submit" size="sm" disabled={!image.trim() || create.isPending}>
              {create.isPending && (
                <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
              )}
              创建
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
