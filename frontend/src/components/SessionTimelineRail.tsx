'use client';

import React, { useMemo, useState, useEffect, useRef, useCallback } from 'react';
import { Message } from '@/types';
import { Clock, MessageSquare, ArrowRight, Layers } from 'lucide-react';

interface SessionTimelineRailProps {
  messages: Message[];
  containerRef: React.RefObject<HTMLDivElement | null>;
}

interface UserTurnItem {
  key: string;
  id?: number;
  turnId: number;
  content: string;
  createdAt: string;
  index: number;
  total: number;
}

interface SingleTimelineNode {
  type: 'single';
  key: string;
  item: UserTurnItem;
}

interface ClusterTimelineNode {
  type: 'cluster';
  key: string;
  startTime: string;
  endTime: string;
  items: UserTurnItem[];
  heightPx: number;
}

type TimelineNode = SingleTimelineNode | ClusterTimelineNode;

/**
 * 格式化时间戳
 */
function formatTurnTime(isoStr: string): string {
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const isToday =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();

    const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (isToday) return timeStr;

    const monthDay = d.toLocaleDateString([], { month: 'numeric', day: 'numeric' });
    return `${monthDay} ${timeStr}`;
  } catch {
    return '';
  }
}

/**
 * 格式化合集跨度时间标签
 */
function formatClusterSpan(startIso: string, endIso: string): string {
  try {
    const s = new Date(startIso);
    const e = new Date(endIso);
    if (isNaN(s.getTime())) return '历史对话合集';
    const monthDay = s.toLocaleDateString([], { month: 'numeric', day: 'numeric' });
    const sTime = s.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const eTime = !isNaN(e.getTime())
      ? e.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : sTime;
    return `${monthDay} ${sTime} – ${eTime}`;
  } catch {
    return '历史对话合集';
  }
}

/**
 * 截取清洗用户提问正文（去除换行、多余空格及附件内部标记）
 */
function sanitizeSummary(rawContent: string, maxLength = 80): string {
  if (!rawContent) return '（空提问）';
  const cleaned = rawContent
    .replace(/<agenthub_attachments>[\s\S]*?<\/agenthub_attachments>/g, '')
    .replace(/\[附件:[^\]]+\]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!cleaned) return '（发送了附件或指令）';
  return cleaned.length > maxLength ? `${cleaned.slice(0, maxLength)}...` : cleaned;
}

export function SessionTimelineRail({ messages, containerRef }: SessionTimelineRailProps) {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<{
    key: string;
    node: TimelineNode;
    anchorTop: number;
  } | null>(null);

  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const railContainerRef = useRef<HTMLDivElement>(null);

  // 1. 抽取所有有效的 user 提问轮次
  const userTurns = useMemo<UserTurnItem[]>(() => {
    const turns: UserTurnItem[] = [];
    let turnCounter = 0;

    messages.forEach((m, idx) => {
      if (m.role === 'user') {
        turnCounter += 1;
        const msgKey = m.id ? String(m.id) : `idx_${idx}`;
        turns.push({
          key: msgKey,
          id: m.id,
          turnId: m.turn_id ?? turnCounter,
          content: m.content || '',
          createdAt: m.created_at || new Date().toISOString(),
          index: turnCounter,
          total: 0,
        });
      }
    });

    const totalCount = turns.length;
    turns.forEach((t) => {
      t.total = totalCount;
    });

    return turns;
  }, [messages]);

  // 2. 节点组织：<= 10 轮时全部作为清晰的独立单点！> 10 轮且有密集远期时才做部分合集
  const timelineNodes = useMemo<TimelineNode[]>(() => {
    if (userTurns.length === 0) return [];

    // 若轮数不多（10 轮以内），全部保持独立单点，保证每一个快捷点都清晰排布！
    if (userTurns.length <= 10) {
      return userTurns.map((item) => ({
        type: 'single',
        key: `node_${item.key}`,
        item,
      }));
    }

    // 超过 10 轮时：最近 8 轮保持单点，较早历史如果很密集才做时间块压缩
    const KEEP_RECENT_SINGLES = 8;
    const splitIndex = Math.max(0, userTurns.length - KEEP_RECENT_SINGLES);
    const olderTurns = userTurns.slice(0, splitIndex);
    const recentTurns = userTurns.slice(splitIndex);

    const nodes: TimelineNode[] = [];
    let currentClusterItems: UserTurnItem[] = [];

    const flushCluster = () => {
      if (currentClusterItems.length === 0) return;
      if (currentClusterItems.length < 3) {
        currentClusterItems.forEach((it) => {
          nodes.push({ type: 'single', key: `node_${it.key}`, item: it });
        });
      } else {
        const first = currentClusterItems[0];
        const last = currentClusterItems[currentClusterItems.length - 1];
        const dynamicHeight = Math.min(48, 18 + currentClusterItems.length * 3);
        nodes.push({
          type: 'cluster',
          key: `cluster_${first.key}_${last.key}`,
          startTime: first.createdAt,
          endTime: last.createdAt,
          items: [...currentClusterItems],
          heightPx: dynamicHeight,
        });
      }
      currentClusterItems = [];
    };

    for (let i = 0; i < olderTurns.length; i++) {
      const it = olderTurns[i];
      if (currentClusterItems.length === 0) {
        currentClusterItems.push(it);
        continue;
      }

      const prev = currentClusterItems[currentClusterItems.length - 1];
      const prevTime = new Date(prev.createdAt).getTime();
      const currTime = new Date(it.createdAt).getTime();
      const diffMinutes = (currTime - prevTime) / (1000 * 60);

      if (diffMinutes <= 30 && currentClusterItems.length < 10) {
        currentClusterItems.push(it);
      } else {
        flushCluster();
        currentClusterItems.push(it);
      }
    }
    flushCluster();

    recentTurns.forEach((it) => {
      nodes.push({ type: 'single', key: `node_${it.key}`, item: it });
    });

    return nodes;
  }, [userTurns]);

  // 3. 点击节点快速平滑跳转
  const handleScrollToTarget = useCallback(
    (keyOrId: string | number) => {
      const container = containerRef.current;
      if (!container) return;

      const targetId = `chat-msg-${keyOrId}`;
      const targetEl = document.getElementById(targetId);
      if (targetEl) {
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // 添加醒目的翡翠绿高光脉冲动画
        targetEl.classList.add(
          'ring-2',
          'ring-emerald-500/70',
          'bg-emerald-500/10',
          'rounded-2xl',
          'transition-all',
          'duration-300'
        );
        setTimeout(() => {
          targetEl.classList.remove('ring-2', 'ring-emerald-500/70', 'bg-emerald-500/10');
        }, 1800);
      }
    },
    [containerRef]
  );

  // 4. 视口滚动监听（ScrollSpy）
  useEffect(() => {
    const container = containerRef.current;
    if (!container || userTurns.length === 0) return;

    let ticking = false;
    const handleScroll = () => {
      if (ticking) return;
      window.requestAnimationFrame(() => {
        const containerRect = container.getBoundingClientRect();
        const centerLine = containerRect.top + containerRect.height * 0.45;

        let closestKey: string | null = null;
        let minDistance = Infinity;

        for (const turn of userTurns) {
          const el = document.getElementById(`chat-msg-${turn.key}`);
          if (el) {
            const rect = el.getBoundingClientRect();
            const dist = Math.abs(rect.top - centerLine);
            if (dist < minDistance) {
              minDistance = dist;
              closestKey = turn.key;
            }
          }
        }

        if (closestKey) {
          setActiveKey(closestKey);
        }
        ticking = false;
      });
      ticking = true;
    };

    container.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll();

    return () => {
      container.removeEventListener('scroll', handleScroll);
    };
  }, [containerRef, userTurns]);

  // 悬停交互
  const handleMouseEnterNode = (node: TimelineNode, elem: HTMLElement) => {
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
    const railEl = railContainerRef.current;
    if (!railEl) return;
    const railRect = railEl.getBoundingClientRect();
    const elemRect = elem.getBoundingClientRect();
    const relativeTop = elemRect.top - railRect.top + elemRect.height / 2;

    setHoveredNode({
      key: node.key,
      node,
      anchorTop: relativeTop,
    });
  };

  const handleMouseLeaveNode = () => {
    hoverTimerRef.current = setTimeout(() => {
      setHoveredNode(null);
    }, 150);
  };

  const handlePopoverMouseEnter = () => {
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
  };

  const handlePopoverMouseLeave = () => {
    setHoveredNode(null);
  };

  // 只要有 2 条及以上用户提问，就始终在左侧常驻显示
  if (userTurns.length < 2) {
    return null;
  }

  return (
    <div
      ref={railContainerRef}
      className="absolute left-2 top-10 bottom-16 z-30 hidden md:flex flex-col items-center select-none group/rail"
      style={{ width: '28px' }}
      title="会话快速定位轨（鼠标移至节点预览，点击快速跳转）"
    >
      {/* 贯穿中轴导轨线：清晰可见的细线，hover 时轻微加深 */}
      <div className="absolute top-3 bottom-3 w-[2px] bg-slate-300/40 dark:bg-slate-700/60 group-hover/rail:bg-slate-400/60 dark:group-hover/rail:bg-slate-600/80 transition-colors pointer-events-none rounded-full" />

      {/* 垂直节点链表容器：不再用 justify-between 拉散，采用紧凑自然的间距自顶向下排布 */}
      <div className="w-full flex-1 flex flex-col items-center justify-start py-2 relative space-y-2 overflow-visible">
        {timelineNodes.map((node) => {
          if (node.type === 'cluster') {
            const hasActiveItem = node.items.some((it) => it.key === activeKey);
            return (
              <div
                key={node.key}
                className="w-full flex items-center justify-center cursor-pointer py-0.5"
                onMouseEnter={(e) => handleMouseEnterNode(node, e.currentTarget)}
                onMouseLeave={handleMouseLeaveNode}
                onClick={() => handleScrollToTarget(node.items[0].key)}
              >
                {/* 聚合长胶囊条：更高、清晰可见 */}
                <div
                  className={`w-[5px] rounded-full transition-all duration-200 ${
                    hasActiveItem
                      ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)] w-[6px]'
                      : 'bg-slate-400/80 dark:bg-slate-500 hover:bg-emerald-400 hover:w-[6px]'
                  }`}
                  style={{ height: `${node.heightPx}px` }}
                />
              </div>
            );
          }

          // 单点：实体感强、高对比度、带足够鼠标捕获热区的圆点
          const isCurrentActive = node.item.key === activeKey;
          return (
            <div
              key={node.key}
              className="w-full h-5 flex items-center justify-center cursor-pointer group/dot relative"
              onMouseEnter={(e) => handleMouseEnterNode(node, e.currentTarget)}
              onMouseLeave={handleMouseLeaveNode}
              onClick={() => handleScrollToTarget(node.item.key)}
            >
              {/* 实体清晰小圆点：默认 6px 实心、激活 8px 带翡翠绿光环 */}
              <div
                className={`rounded-full transition-all duration-200 ${
                  isCurrentActive
                    ? 'w-[8px] h-[8px] bg-emerald-500 ring-4 ring-emerald-500/25 shadow-sm scale-110'
                    : 'w-[6px] h-[6px] bg-slate-400/75 dark:bg-slate-400/85 hover:bg-emerald-400 hover:scale-135 shadow-[0_1px_2px_rgba(0,0,0,0.15)]'
                }`}
              />
            </div>
          );
        })}
      </div>

      {/* 悬停详情浮窗 (Popover)：紧贴小圆点右侧，点击浮窗本身也能跳转 */}
      {hoveredNode && (
        <div
          onMouseEnter={handlePopoverMouseEnter}
          onMouseLeave={handlePopoverMouseLeave}
          className="absolute left-8 z-50 w-72 rounded-xl bg-surface-100/95 dark:bg-surface-200/95 text-foreground border border-border shadow-2xl backdrop-blur-xl p-3 text-xs animate-scale-in origin-left transition-all duration-150 pointer-events-auto"
          style={{
            top: Math.max(0, Math.min(window.innerHeight - 360, hoveredNode.anchorTop - 28)),
          }}
        >
          {hoveredNode.node.type === 'cluster' ? (
            /* ────────────────── 合集浮窗内容 ────────────────── */
            <div className="space-y-2">
              <div className="flex items-center justify-between border-b border-border/70 pb-1.5">
                <div className="flex items-center space-x-1.5 min-w-0">
                  <Layers className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                  <span className="font-semibold text-foreground text-[11px] truncate">
                    {formatClusterSpan(hoveredNode.node.startTime, hoveredNode.node.endTime)}
                  </span>
                </div>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-mono font-medium shrink-0 ml-1">
                  {hoveredNode.node.items.length} 轮
                </span>
              </div>

              {/* 合集内微缩列表 */}
              <div className="max-h-56 overflow-y-auto space-y-1 pr-1 font-sans">
                {hoveredNode.node.items.map((it) => (
                  <div
                    key={it.key}
                    onClick={() => {
                      handleScrollToTarget(it.key);
                      setHoveredNode(null);
                    }}
                    className="p-1.5 rounded-lg hover:bg-surface-300/70 cursor-pointer flex items-center justify-between space-x-2 transition-colors group/item"
                    title="点击定位到该轮对话"
                  >
                    <span className="text-[10px] text-muted-foreground font-mono shrink-0 group-hover/item:text-emerald-500">
                      {formatTurnTime(it.createdAt)}
                    </span>
                    <span className="text-muted-foreground group-hover/item:text-foreground truncate text-[11px] flex-1 leading-tight">
                      {sanitizeSummary(it.content, 26)}
                    </span>
                    <ArrowRight className="w-3 h-3 text-emerald-500 shrink-0 opacity-0 group-hover/item:opacity-100 transition-opacity" />
                  </div>
                ))}
              </div>
            </div>
          ) : (
            /* ────────────────── 单点浮窗内容 ────────────────── */
            <div
              className="space-y-2 cursor-pointer group/card"
              onClick={() => {
                if (hoveredNode.node.type === 'single') {
                  handleScrollToTarget(hoveredNode.node.item.key);
                  setHoveredNode(null);
                }
              }}
              title="点击快速跳转到此轮对话"
            >
              <div className="flex items-start justify-between gap-1 border-b border-border/60 pb-1.5">
                <div className="flex items-center space-x-1.5 text-emerald-600 dark:text-emerald-400 font-semibold text-[11px]">
                  <MessageSquare className="w-3 h-3" />
                  <span>第 {hoveredNode.node.item.index} 轮发言</span>
                </div>
                <span className="text-[10px] text-muted-foreground font-mono">
                  {hoveredNode.node.item.index} / {hoveredNode.node.item.total}
                </span>
              </div>

              <div className="font-medium text-foreground text-[11px] line-clamp-3 leading-snug">
                {sanitizeSummary(hoveredNode.node.item.content, 90)}
              </div>

              <div className="pt-1 flex items-center justify-between text-[10px] text-muted-foreground font-mono">
                <span className="flex items-center space-x-1">
                  <Clock className="w-2.5 h-2.5" />
                  <span>{formatTurnTime(hoveredNode.node.item.createdAt)}</span>
                </span>
                <span className="text-emerald-600 dark:text-emerald-400 font-sans font-medium flex items-center space-x-0.5 group-hover/card:translate-x-0.5 transition-transform">
                  <span>点击跳转</span>
                  <ArrowRight className="w-2.5 h-2.5" />
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
