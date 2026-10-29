/**
 * 日志流工具
 *
 * SSE follow 模式下日志会持续追加，必须限制内存中的行数上限，
 * 避免长时间跟随导致页面内存无限增长。
 */

/** 日志流最大保留行数（环形上限，超出后丢弃最早的行） */
export const MAX_LOG_LINES = 5000;

/** 追加一行日志，超过上限时丢弃最早的行 */
export function appendLogLine(prev: string[], line: string): string[] {
  if (prev.length >= MAX_LOG_LINES) {
    return [...prev.slice(prev.length - MAX_LOG_LINES + 1), line];
  }
  return [...prev, line];
}
