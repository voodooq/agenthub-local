'use client';

import React, { useEffect, useState, useMemo, useCallback } from 'react';
import {
  Cpu,
  Terminal,
  FolderGit2,
  Wrench,
  Sparkles,
  Zap,
  BookOpen,
  Puzzle,
  Server,
  FileText,
  X,
  ArrowRight,
  Loader2,
} from 'lucide-react';
import { ExtensionSkillItem } from '@/types';
import {
  fetchExtensionSkillDetail,
  fetchExtensionSkills,
  getCachedExtensionSkills,
} from '@/utils/skillCache';

interface CommandItem {
  id: string;
  trigger: string;
  label: string;
  description: string;
  insertText: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
  version?: string;
  docBody?: string;
  hasDoc?: boolean;
  skillId?: string;
  mcpServer?: string;
  mcpEnabled?: boolean | null;
}

const MAX_AUTOCOMPLETE_ITEMS = 60;

const STATIC_COMMAND_LIST: CommandItem[] = [
  // @ 上下文引用
  {
    id: 'at_hardware',
    trigger: '@',
    label: '@hardware',
    description: '注入宿主机最新硬件指标快照 (CPU/内存/GPU/磁盘)',
    insertText: '@hardware 请分析当前硬件状态并评估项目承载力。',
    icon: Cpu,
    badge: '上下文',
    hasDoc: true,
    docBody:
      '# @hardware 宿主机硬件快照引用\n\n自动采集当前机器的 CPU 负载、物理内存余量、NVIDIA GPU 显存 (VRAM) 与磁盘空间指标，并注入当前对话上下文中供智能体诊断分析。',
  },
  {
    id: 'at_process',
    trigger: '@',
    label: '@process',
    description: '引用当前内存高占用进程列表进行深度诊断',
    insertText: '@process 帮我排查当前是否有僵尸或异常耗能进程。',
    icon: Terminal,
    badge: '上下文',
    hasDoc: true,
    docBody:
      '# @process 高占用进程列表引用\n\n实时枚举宿主机内存与 CPU 占用 Top 进程列表（含 PID 与进程名称），辅助排查脱离终端的孤儿进程或资源泄漏。',
  },
  {
    id: 'at_workspace',
    trigger: '@',
    label: '@workspace',
    description: '引用当前绑定的本地项目目录上下文',
    insertText: '@workspace 请阅读当前项目架构并给出重构建议。',
    icon: FolderGit2,
    badge: '上下文',
    hasDoc: true,
    docBody:
      '# @workspace 本地工作区目录引用\n\n将智能体的持久工作目录 (CWD) 与目录结构摘要挂载到当前提问中，适合代码审查与项目级重构。',
  },
  // / 内置系统快捷指令
  {
    id: 'slash_optimize_dev',
    trigger: '/',
    label: '/optimize-dev',
    description: '【开发专项】扫描脱离终端的孤儿 node/python 进程并释放端口',
    insertText: '/optimize-dev 请全面体检本地研发环境，排查端口冲突与编译缓存。',
    icon: Wrench,
    badge: '系统指令',
    hasDoc: true,
    docBody:
      '# /optimize-dev 研发环境专项体检\n\n## 功能说明\n1. 自动扫描本机残留的孤儿 `node`、`python` 构建进程；\n2. 排查常用开发端口（3000/8000/8080 等）占用情况；\n3. 给出安全释放建议并在审批后清理。',
  },
  {
    id: 'slash_optimize_design',
    trigger: '/',
    label: '/optimize-design',
    description: '【设计专项】分析显存(VRAM)与 Adobe/Blender 暂存盘空间',
    insertText: '/optimize-design 请进行显存护航，清理占用显存的后台程序。',
    icon: Sparkles,
    badge: '系统指令',
    hasDoc: true,
    docBody:
      '# /optimize-design 显存与创作环境护航\n\n## 功能说明\n1. 检查 GPU 显存 (VRAM) 实时占用分布；\n2. 排查占用大量显存或暂存盘空间的后台进程，保障本地大模型推理与图形渲染流畅。',
  },
  {
    id: 'slash_free_port',
    trigger: '/',
    label: '/free-port',
    description: '排查指定本地端口 (如 3000, 8080) 被哪个进程死锁占用',
    insertText: '/free-port 3000 排查 3000 端口被谁占用并释放。',
    icon: Zap,
    badge: '系统指令',
    hasDoc: true,
    docBody:
      '# /free-port 端口死锁排查与释放\n\n## 用法示例\n- `/free-port 8080`\n- `/free-port 3000`\n\n## 执行流程\n自动通过 `netstat -ano` 与 `Get-Process` 定位占用指定端口的进程 PID 与路径，并协助释放端口。',
  },
  {
    id: 'slash_stats',
    trigger: '/',
    label: '/stats',
    description: '查看当前会话 Token 消耗、KV Cache 命中率与分块坍缩收益看板（别名 /tokens）',
    insertText: '/stats',
    icon: Sparkles,
    badge: '监控看板',
    hasDoc: true,
    docBody:
      '# /stats 会话上下文与 KV Cache 性能看板\n\n## 功能说明\n- 输入 `/stats` 或 `/tokens` 并发送，立即在本地打开当前会话的完整 Token 统计弹窗（**不消耗任何模型推理配额**）；\n- 展示每轮 Prompt 实算 vs 缓存复用率、旁路工具脱水节省 Tokens、分块坍缩锚点与首字延迟 (TTFT / TPS)。',
  },
  {
    id: 'slash_ckpt',
    trigger: '/',
    label: '/ckpt',
    description: '建立工作区影子 Git 与会话状态双态检查点（支持 /ckpt list）',
    insertText: '/ckpt ',
    icon: BookOpen,
    badge: '时光机',
    hasDoc: true,
    docBody:
      '# /ckpt 建立双态时光机检查点（0-Token）\n\n## 用法示例\n- `/ckpt 重构前稳定版本`：立即对工作区代码与草稿纸生成隔离快照\n- `/ckpt list`：查看当前会话最近保存的检查点清单\n\n## 特性\n独立存储在 `.agenthub/shadow_git`，绝不污染项目原生的 `.git` 目录。',
  },
  {
    id: 'slash_rollback',
    trigger: '/',
    label: '/rollback',
    description: '将磁盘文件、草稿纸与历史消息回滚还原至指定检查点',
    insertText: '/rollback',
    icon: Wrench,
    badge: '时光机',
    hasDoc: true,
    docBody:
      '# /rollback 全量双回滚时光机（0-Token）\n\n## 用法示例\n- `/rollback`：一键回滚至最近一次检查点\n- `/rollback ckpt_xxx`：回滚至指定的历史检查点\n\n## 还原范围\n1. 物理还原工作区代码与 `.agenthub/scratchpad.md`；\n2. 截断回滚点之后的对话消息与事件账本，并自动刷新界面状态。',
  },
  {
    id: 'slash_remember',
    trigger: '/',
    label: '/remember',
    description: '0-Token 将团队规范或约束直接写入 L4 确定性业务规则库',
    insertText: '/remember ',
    icon: Zap,
    badge: '记忆契约',
    hasDoc: true,
    docBody:
      '# /remember 固化 L4 确定性契约规则\n\n## 用法示例\n- `/remember pkg_mgr 本项目统一使用 pnpm 安装依赖`\n- `/remember 严禁向只读 attachments/ 目录写文件`\n\n## 生效方式\n经隐私脱敏守卫校验后立即写入 L4 FactManager，后续每轮自动强置入上下文。',
  },
  {
    id: 'slash_distill',
    trigger: '/',
    label: '/distill',
    description: '正交双提纯：从当前实战轨迹抽离通用 Skill 与私有避坑规则',
    insertText: '/distill ',
    icon: Sparkles,
    badge: '技能蒸馏',
    hasDoc: true,
    docBody:
      '# /distill 正交双提纯与热加载\n\n## 用法示例\n- `/distill excel-audit 严格校验汇总行误差`\n\n## 正交产物\n1. 通用三阶段 SOP (`SKILL.md`) 与安全脚手架 (`scripts/pipeline.py`) 写入工作区 Skills；\n2. 私有业务规则写入 L4 FactManager，实战避坑经验写入 L5 微经验库。',
  },
  {
    id: 'slash_clean_cache',
    trigger: '/',
    label: '/clean-cache',
    description: '扫描 npm / pip / 系统 Temp 目录并生成勾选清理单',
    insertText: '/clean-cache 扫描本地开发包缓存与临时文件。',
    icon: Terminal,
    badge: '系统指令',
    hasDoc: true,
    docBody:
      '# /clean-cache 开发缓存与临时目录体检\n\n扫描本机 `npm-cache`、`pip/cache` 及系统 `Temp` 目录体积，生成可审计的空間释放清单。',
  },
];

function mapExtensionSkillsToCommands(skills: ExtensionSkillItem[]): CommandItem[] {
  const items: CommandItem[] = [];
  const seenLabels = new Set<string>();

  for (const sk of skills) {
    const skillId = String(sk.id || sk.name || '').trim();
    if (!skillId) continue;
    const rawCmd = String(sk.command || `/${skillId}`).trim();
    const label = rawCmd.startsWith('/') ? rawCmd : `/${rawCmd}`;
    if (seenLabels.has(label.toLowerCase())) continue;
    seenLabels.add(label.toLowerCase());

    const isMcp = sk.type === 'mcp' || Boolean(sk.mcp_server);
    const isSuite = sk.type === 'suite' || sk.tier === 'commercial';
    const aliasSuffix = label.toLowerCase() !== `/${skillId.toLowerCase()}` ? ` [${skillId}]` : '';
    const tupleFlags: string[] = [];
    if (sk.has_tools) tupleFlags.push('🔧算子');
    if (sk.has_knowledge) tupleFlags.push('📚知识库');
    if (sk.has_schema) tupleFlags.push('📋Schema');
    const tupleSuffix = tupleFlags.length > 0 ? ` · ${tupleFlags.join('/')}` : '';

    let badgeText = sk.is_builtin ? '内置 SKILL' : '自定义 SKILL';
    if (isSuite) {
      badgeText = `Pro 套件${tupleSuffix}`;
    } else if (isMcp) {
      badgeText = sk.mcp_enabled ? 'MCP 封装 (已启用)' : 'MCP 封装 (按需)';
    } else if (tupleSuffix) {
      badgeText = `${badgeText}${tupleSuffix}`;
    }

    const docText = sk.body || sk.content;
    items.push({
      id: `skill_${skillId}`,
      trigger: '/',
      label,
      description: `${sk.description || `激活本地专业技能包 ${skillId}`}${aliasSuffix}`,
      insertText: `${label} `,
      icon: isMcp ? Server : sk.is_builtin ? Puzzle : BookOpen,
      badge: badgeText,
      version: sk.version ? String(sk.version) : undefined,
      docBody: docText ? String(docText) : undefined,
      hasDoc: Boolean(docText || sk.has_content || skillId),
      skillId,
      mcpServer: sk.mcp_server || undefined,
      mcpEnabled: sk.mcp_enabled ?? null,
    });
  }

  return items;
}

interface CommandAutocompleteProps {
  input: string;
  cursorPosition?: number;
  onSelect: (insertText: string, replaceRange?: { start: number; end: number }) => void;
  onClose: () => void;
}

export function CommandAutocomplete({
  input,
  cursorPosition,
  onSelect,
  onClose,
}: CommandAutocompleteProps) {
  const [dynamicItems, setDynamicItems] = useState<CommandItem[]>([]);
  const [previewItemId, setPreviewItemId] = useState<string | null>(null);
  const [detailCache, setDetailCache] = useState<Record<string, string>>({});
  const [loadingDocId, setLoadingDocId] = useState<string | null>(null);

  // 基于光标所在位置的当前分隔词判定触发状态，支持单条消息串联输入多个 /skill
  const activeTokenInfo = useMemo(() => {
    const pos =
      typeof cursorPosition === 'number'
        ? Math.max(0, Math.min(cursorPosition, input.length))
        : input.length;
    const isLeftDelimiter = (ch: string) => /\s|[([{"'（【]/.test(ch);
    const isRightDelimiter = (ch: string) => /\s|[)\]}"'）】]/.test(ch);

    let start = pos;
    while (start > 0 && !isLeftDelimiter(input[start - 1])) {
      start -= 1;
    }
    let end = pos;
    while (end < input.length && !isRightDelimiter(input[end])) {
      end += 1;
    }
    const token = input.slice(start, end);
    // 排除多斜杠路径（例如 ./src/app 或 /etc/hosts），仅识别命令形式 /command 或 @context
    const isTriggered =
      (token.startsWith('@') || token.startsWith('/')) &&
      (token.startsWith('@') || !token.slice(1).includes('/'));
    return {
      token,
      start,
      end,
      isTriggered,
      isSlashTriggered: isTriggered && token.startsWith('/'),
    };
  }, [input, cursorPosition]);

  const { token: lastWord, start: tokenStart, end: tokenEnd, isTriggered, isSlashTriggered } = activeTokenInfo;

  // 仅在首次触发 '/' 时同步读取缓存秒显并后台刷新一次，不再随敲字重复请求全量扫描接口
  useEffect(() => {
    if (!isSlashTriggered) return;
    let cancelled = false;

    const cached = getCachedExtensionSkills();
    if (cached) {
      setDynamicItems(mapExtensionSkillsToCommands(cached));
    }

    fetchExtensionSkills({ force: true })
      .then((skills) => {
        if (!cancelled) {
          setDynamicItems(mapExtensionSkillsToCommands(skills));
        }
      })
      .catch(() => {
        // 静默忽略网络抖动，回退静态列表或缓存
      });

    return () => {
      cancelled = true;
    };
  }, [isSlashTriggered]);

  const handleTogglePreview = useCallback(
    async (item: CommandItem) => {
      if (previewItemId === item.id) {
        setPreviewItemId(null);
        return;
      }

      setPreviewItemId(item.id);
      if (item.docBody || !item.skillId || detailCache[item.skillId]) {
        return;
      }

      const sid = item.skillId;
      setLoadingDocId(item.id);
      try {
        const detail = await fetchExtensionSkillDetail(sid);
        const text = detail?.body || detail?.content || detail?.description || item.description;
        setDetailCache((prev) => ({ ...prev, [sid]: String(text) }));
      } catch {
        setDetailCache((prev) => ({ ...prev, [sid]: item.description }));
      } finally {
        setLoadingDocId((curr) => (curr === item.id ? null : curr));
      }
    },
    [previewItemId, detailCache]
  );

  const allCommands = useMemo(() => {
    const merged: CommandItem[] = [...dynamicItems];
    const existingLabels = new Set(dynamicItems.map((i) => i.label.toLowerCase()));
    for (const st of STATIC_COMMAND_LIST) {
      if (!existingLabels.has(st.label.toLowerCase())) {
        merged.push(st);
      }
    }
    return merged;
  }, [dynamicItems]);

  const filtered = useMemo(() => {
    if (!isTriggered) return [];
    const trigger = lastWord[0];
    const filterKey = lastWord.slice(1).toLowerCase();
    return allCommands
      .filter(
        (item) =>
          item.trigger === trigger &&
          (item.label.toLowerCase().includes(filterKey) ||
            item.description.toLowerCase().includes(filterKey) ||
            (item.skillId && item.skillId.toLowerCase().includes(filterKey)))
      )
      .slice(0, MAX_AUTOCOMPLETE_ITEMS);
  }, [isTriggered, lastWord, allCommands]);

  if (!isTriggered || filtered.length === 0) {
    return null;
  }

  const activePreviewItem = filtered.find((item) => item.id === previewItemId) || null;
  const activePreviewBody = activePreviewItem
    ? activePreviewItem.docBody ||
      (activePreviewItem.skillId ? detailCache[activePreviewItem.skillId] : undefined) ||
      ''
    : '';

  return (
    <div
      className={`absolute bottom-full left-0 mb-2 w-full ${
        activePreviewItem ? 'max-w-3xl' : 'max-w-xl'
      } bg-surface-100/95 backdrop-blur-xl border border-border rounded-2xl shadow-2xl p-2.5 z-30 animate-scale-in transition-all`}
    >
      <div className="px-3 py-1.5 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider border-b border-border/60 flex items-center justify-between">
        <span>快捷指令、本地 SKILL 与 MCP 封装规程 (@上下文 / /技能包)</span>
        <span className="text-muted-foreground font-mono">点击指令直接填入 · 点击「📖 说明」查看完整规程 · Esc 关闭</span>
      </div>

      <div className={`mt-1.5 ${activePreviewItem ? 'grid grid-cols-1 md:grid-cols-12 gap-2.5' : ''}`}>
        {/* 左侧：命令与技能列表 */}
        <div
          className={`max-h-72 overflow-y-auto space-y-1 pr-0.5 ${
            activePreviewItem ? 'md:col-span-6 border-r border-border/50 pr-2' : ''
          }`}
        >
          {filtered.map((item) => {
            const Icon = item.icon;
            const isPreviewing = activePreviewItem?.id === item.id;
            const isMcpBadge = item.badge?.startsWith('MCP');
            const isSkillBadge = item.badge?.includes('SKILL');

            return (
              <div
                key={item.id}
                onClick={() => onSelect(item.insertText, { start: tokenStart, end: tokenEnd })}
                className={`flex items-center justify-between p-2 rounded-xl cursor-pointer transition-colors group ${
                  isPreviewing
                    ? 'bg-emerald-500/10 border border-emerald-500/30'
                    : 'hover:bg-surface-200/80 border border-transparent'
                }`}
              >
                <div className="flex items-center space-x-2.5 flex-1 min-w-0">
                  <div
                    className={`p-1.5 rounded-lg transition-colors shrink-0 ${
                      isMcpBadge
                        ? 'bg-sky-500/15 text-sky-500'
                        : 'bg-surface-200 group-hover:bg-emerald-500/20 text-muted-foreground group-hover:text-emerald-500'
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center space-x-2">
                      <span className="font-mono text-xs font-semibold text-foreground group-hover:text-emerald-500">
                        {item.label}
                      </span>
                      {item.version && (
                        <span className="text-[9px] font-mono px-1.5 py-0.2 rounded bg-surface-200 text-muted-foreground border border-border/70">
                          v{item.version}
                        </span>
                      )}
                      {item.badge && (
                        <span
                          className={`text-[9px] px-1.5 py-0.2 rounded font-medium ${
                            isMcpBadge
                              ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400 border border-sky-500/30'
                              : isSkillBadge
                              ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30'
                              : 'bg-surface-200 text-muted-foreground border border-border'
                          }`}
                        >
                          {item.badge}
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-muted-foreground truncate">{item.description}</div>
                  </div>
                </div>

                {/* 右侧：查看说明按钮 */}
                {(item.hasDoc || item.docBody) && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleTogglePreview(item);
                    }}
                    title="查看该 SKILL / MCP 的详细使用说明与参数规程"
                    className={`ml-2 px-2 py-1 rounded-lg text-[10px] font-medium flex items-center space-x-1 shrink-0 transition-all ${
                      isPreviewing
                        ? 'bg-emerald-500 text-white shadow-sm'
                        : 'bg-surface-200/80 hover:bg-emerald-500/20 text-muted-foreground hover:text-emerald-500 border border-border/60'
                    }`}
                  >
                    <FileText className="w-3 h-3" />
                    <span>{isPreviewing ? '收起说明' : '说明'}</span>
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* 右侧：SKILL / MCP 完整说明文档预览面板 */}
        {activePreviewItem && (
          <div className="md:col-span-6 max-h-72 flex flex-col bg-surface-200/40 border border-border/70 rounded-xl p-3 overflow-hidden">
            <div className="flex items-center justify-between pb-2 mb-2 border-b border-border/60 shrink-0">
              <div className="flex items-center space-x-2 min-w-0">
                <span className="font-mono text-xs font-bold text-emerald-500">{activePreviewItem.label}</span>
                {activePreviewItem.version && (
                  <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-surface-200 text-muted-foreground border border-border/70 shrink-0">
                    v{activePreviewItem.version}
                  </span>
                )}
                {activePreviewItem.skillId && (
                  <span className="text-[10px] font-mono text-muted-foreground truncate">
                    ({activePreviewItem.skillId})
                  </span>
                )}
              </div>
              <div className="flex items-center space-x-1.5 shrink-0">
                <button
                  type="button"
                  onClick={() => onSelect(activePreviewItem.insertText, { start: tokenStart, end: tokenEnd })}
                  className="px-2 py-1 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white text-[10px] font-semibold flex items-center space-x-1 transition-colors"
                >
                  <span>填入指令</span>
                  <ArrowRight className="w-2.5 h-2.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setPreviewItemId(null)}
                  className="p-1 rounded-lg hover:bg-surface-300 text-muted-foreground transition-colors"
                  title="关闭说明面板"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {activePreviewItem.mcpServer && (
              <div className="mb-2 px-2 py-1 rounded-lg bg-sky-500/10 border border-sky-500/20 text-[10px] text-sky-600 dark:text-sky-400 flex items-center justify-between shrink-0">
                <span>绑定 MCP 服务: {activePreviewItem.mcpServer}</span>
                <span>{activePreviewItem.mcpEnabled ? '● 已就绪' : '○ 按需启用'}</span>
              </div>
            )}

            <div className="flex-1 overflow-y-auto pr-1 text-[11px] leading-relaxed text-foreground/90 whitespace-pre-wrap font-sans">
              {loadingDocId === activePreviewItem.id ? (
                <div className="flex items-center space-x-1.5 text-muted-foreground py-4">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-500" />
                  <span>正在按需加载完整规程...</span>
                </div>
              ) : (
                activePreviewBody || activePreviewItem.description
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
