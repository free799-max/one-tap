/**
 * 容器状态指示点组件
 *
 * 根据容器运行状态显示不同颜色的圆点：
 * - running（运行中）：绿色
 * - exited（已停止）：红色
 * - 其他状态：黄色
 */

export function StatusDot({ status }: { status: string }) {
  const color =
    status === "running"
      ? "bg-green-500"
      : status === "exited"
      ? "bg-red-500"
      : "bg-yellow-500";
  return <span className={`inline-block w-2 h-2 rounded-full ${color} mr-2`} />;
}
