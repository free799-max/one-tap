/**
 * NasDeck Logo 图标组件 — 全新设计
 *
 * 设计概念：层叠的数据面板 (Deck) + NAS 状态指示灯
 * - 紫色圆角底代表品牌主色
 * - 三层递减的白色圆角条代表层叠的服务面板/数据卡片
 * - 右上角青色发光圆点代表系统在线/健康状态
 *
 * inverse 模式：用于深色/紫色背景
 */

interface LogoIconProps {
  className?: string;
  size?: number;
  inverse?: boolean;
}

export function LogoIcon({ className = "", size = 32, inverse = false }: LogoIconProps) {
  const bgColor = inverse ? "white" : "#7c3aed";
  const bgOpacity = inverse ? 0.2 : 1;
  const barColor = "white";
  const dotColor = inverse ? "white" : "#22d3ee";
  const dotGlowColor = inverse ? "white" : "#22d3ee";

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* 外框背景 */}
      <rect
        x="2"
        y="2"
        width="28"
        height="28"
        rx="8"
        fill={bgColor}
        opacity={bgOpacity}
      />

      {/* 三层递减面板 */}
      <rect
        x="7"
        y="9"
        width="16"
        height="3.5"
        rx="1.75"
        fill={barColor}
        opacity={inverse ? 0.95 : 0.95}
      />
      <rect
        x="7"
        y="14.25"
        width="12"
        height="3.5"
        rx="1.75"
        fill={barColor}
        opacity={inverse ? 0.75 : 0.75}
      />
      <rect
        x="7"
        y="19.5"
        width="8"
        height="3.5"
        rx="1.75"
        fill={barColor}
        opacity={inverse ? 0.55 : 0.55}
      />

      {/* 状态指示灯外发光 */}
      <circle
        cx="25"
        cy="10.75"
        r="4"
        fill={dotGlowColor}
        opacity={inverse ? 0.25 : 0.35}
      />
      {/* 状态指示灯核心 */}
      <circle
        cx="25"
        cy="10.75"
        r="2.5"
        fill={dotColor}
        opacity={inverse ? 0.9 : 1}
      />
    </svg>
  );
}

/**
 * NasDeck 简洁版 Logo（仅图标，用于小尺寸场景）
 */
export function LogoIconSmall({ className = "", size = 20, inverse = false }: LogoIconProps) {
  return <LogoIcon className={className} size={size} inverse={inverse} />;
}
