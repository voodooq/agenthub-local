/**
 * 模型断流/未反馈自动重试等待阶梯算法与状态定义
 *
 * 规则：
 * 默认每轮的时间长度逐步加长，第一轮等待10秒，后面每轮多等待前一轮的1/2秒，
 * 并取5、10的整数位（即 5 的整数倍）：
 * - 第 1 轮: 10 秒
 * - 第 2 轮: 15 秒 (10 + 5)
 * - 第 3 轮: 25 秒 (15 + 7.5 -> 22.5 -> 取整 25)
 * - 第 4 轮: 40 秒 (25 + 12.5 -> 37.5 -> 取整 40)
 * - 第 5 轮: 60 秒 (40 + 20 -> 60)
 * - 第 6 轮: 90 秒 (60 + 30 -> 90)
 * - 第 7 轮: 135 秒 (90 + 45 -> 135)
 * - 第 8 轮: 205 秒 (135 + 67.5 -> 202.5 -> 取整 205)
 * - 第 9 轮: 310 秒 (205 + 102.5 -> 307.5 -> 取整 310)
 */

export const MAX_AUTO_RETRY_ROUNDS = 9;

export const RETRY_DELAYS_PRESET = [10, 15, 25, 40, 60, 90, 135, 205, 310];

/**
 * 根据重试轮次获取等待秒数
 * @param round 1-based 轮次（1 到 9）
 */
export function getRetryDelaySeconds(round: number): number {
  if (round >= 1 && round <= RETRY_DELAYS_PRESET.length) {
    return RETRY_DELAYS_PRESET[round - 1];
  }
  let current = 10;
  for (let r = 2; r <= round; r++) {
    current = Math.round((current * 1.5) / 5) * 5;
  }
  return current;
}

/**
 * 格式化倒计时提示文案
 * 如：当前对话模型不可用，等待重试（1/9）15''
 */
export function formatRetryCountdownNotice(round: number, countdown: number, maxRounds = MAX_AUTO_RETRY_ROUNDS): string {
  return `当前对话模型不可用，等待重试（${round}/${maxRounds}）${countdown}''`;
}

/**
 * 自动重试上下文状态
 */
export interface AutoRetryState {
  isWaiting: boolean;
  round: number;          // 当前轮次 (1..9)
  maxRounds: number;      // 最大轮次 (9)
  countdown: number;      // 当前倒数秒数
  totalWaitSeconds: number; // 本轮总秒数
  convId: string;
  errorMessage?: string;
}

