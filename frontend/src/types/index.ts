export interface Account {
  id: number;
  name: string;
  auth_type: string;
  priority: number;
  status: 'active' | 'cooldown' | 'disabled';
  cooldown_until: string | null;
  rpm_limit: number;
  rpd_limit: number;
  today_used: number;
  total_tokens: number;
  updated_at: string | null;
  email?: string;
  avatar_url?: string;
  cli_home_dir?: string;
  is_estimated?: boolean;
}

export interface StatusPillData {
  active_account_name: string;
  auth_type: string;
  status: string;
  today_used: number;
  current_rpm: number;
  rpm_limit: number;
  is_warning: boolean;
  is_relayed?: boolean;
  last_relay_info?: string | null;
  email?: string;
  avatar_url?: string;
}

export type SceneMode = 'oneshot' | 'chat' | 'agent';

export interface ToolExecutionCardItem {
  call_id: string;
  name: string;
  command: string;
  reason?: string;
  cwd?: string;
  status: 'running' | 'require_approval' | 'completed' | 'rejected' | 'denied';
  exit_code?: number;
  output?: string;
  compressed?: string;
}

export interface MemoryRefItem {
  id: string;
  category: string;
  content: string;
  confidence: number;
}

export interface ConfirmedFactItem {
  fact_key: string;
  fact_value: string;
  category?: 'user_pref' | 'biz_rule' | 'env_config' | string;
  workspace_scope?: 'workspace' | 'global';
}

export interface MemoryFactItem {
  fact_id: string;
  workspace_id: string;
  category: string;
  fact_key: string;
  fact_value: string;
  confidence: number;
  source_type: string;
  source_event_id?: string | null;
  valid_from: string;
  valid_to?: string | null;
  recorded_at: string;
  is_active: boolean;
  scope: 'workspace' | 'global';
  overridden_global?: boolean;
  global_fallback_value?: string | null;
}

export interface EventLedgerItem {
  id: string;
  workspace_id: string;
  conversation_id: string;
  turn_id: number;
  loop_id?: string | null;
  event_type: string;
  actor: string;
  skill_id?: string | null;
  payload?: Record<string, any>;
  redactions?: string[];
  is_redacted: boolean;
  created_at: string;
}

export interface ExperienceProposalTriple {
  problem: string;
  root_cause: string;
  solution: string;
}

export interface ExperienceProposalTargetSkillOption {
  id: string;
  name: string;
  command: string;
  version: string;
  scope: 'workspace' | 'global' | string;
  description?: string;
}

export interface ExperienceProposalPayload {
  target_file?: string;
  markdown_entry?: string;
  skill_id?: string;
  command?: string;
  description?: string;
  skill_md?: string;
  schema_json?: Record<string, any> | null;
  playbook_md?: string;
  update_mode?: 'upgrade_skill_md' | 'playbook_only' | string;
  target_skill_id?: string;
  current_version?: string;
  next_version?: string;
  candidate_skill_ids?: string[];
  new_skill_id?: string;
  new_command?: string;
  new_description?: string;
  new_skill_md?: string;
  new_schema_json?: Record<string, any> | null;
  new_playbook_md?: string;
}

export interface ExperienceProposalItem {
  id: string;
  workspace_id: string;
  conversation_id: string;
  loop_id?: string | null;
  proposal_type: 'skill_patch' | 'new_skill';
  target_skill_id?: string | null;
  proposed_skill_name?: string | null;
  target_scope: 'workspace' | 'global';
  trigger_reason: string;
  title: string;
  summary: string;
  experience_triple: ExperienceProposalTriple;
  diff_preview?: string;
  payload: ExperienceProposalPayload;
  status: 'pending' | 'applied' | 'dismissed';
  created_at: string;
  resolved_at?: string | null;
  written_path?: string;
  skill_dir?: string;
  applied_version?: string;
  available_skills?: ExperienceProposalTargetSkillOption[];
}

export interface LongTermMemoryItem {
  id: string;
  category: 'env_info' | 'preference' | 'rule';
  content: string;
  confidence: number;
  source?: string;
  is_pinned?: number;
  hit_count: number;
  last_accessed_at?: string;
  created_at?: string;
}

export interface Workspace {
  id: string;
  name: string;
  type: 'virtual' | 'physical';
  root_path: string;
  context_prompt?: string | null;
  model_preference?: string | null;
  security_level: string;
  fts_last_indexed_at?: string | null;
  created_at: string;
  conversation_count?: number;
  is_path_accessible?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  source_type: 'local' | 'web_sync' | 'takeout';
  source_account: string | null;
  model_name: string;
  is_pinned: boolean;
  created_at: string;
  updated_at: string;
  message_count?: number;
  scene_mode?: SceneMode;
  incognito?: number | boolean;
  working_memory_json?: string;
  current_cwd?: string | null;
  workspace_id?: string;
}

export interface Message {
  id?: number;
  conversation_id: string;
  role: 'user' | 'model' | 'system' | 'tool';
  content: string;
  thinking?: string;
  model_name?: string;
  executed_by_account?: string | null;
  token_count?: number;
  created_at?: string;
  is_relayed?: boolean;
  turn_id?: number;
  compressed_content?: string | null;
  tool_calls_json?: string | null;
  tool_call_id?: string | null;
  tool_events?: ToolExecutionCardItem[];
  merged_tool_message_ids?: number[];
  memory_refs?: MemoryRefItem[];
  fact_refs?: MemoryFactItem[];
  experience_proposal?: ExperienceProposalItem;
  downgrade_info?: {
    original_model: string;
    current_model: string;
    reason?: string;
    message?: string;
  };
  step_extend_info?: {
    step: number;
    max_steps: number;
    message: string;
  };
  tool_request?: {
    tool_name: string;
    tool_input: Record<string, any>;
    status?: 'pending' | 'approved' | 'rejected';
  };
  token_stats?: TurnTokenMetrics | null;
}

/** 单轮聚合 Token 消耗、KV Cache 命中与推理延迟水表指标 */
export interface TurnTokenMetrics {
  turn_id: number;
  context_tokens: number;
  context_limit: number;
  context_usage_pct: number;
  cached_tokens: number;
  cache_hit_pct: number;
  eval_tokens: number;
  completion_tokens: number;
  saved_tokens: number;
  turn_saved_tokens?: number;
  session_collapsed_saved_tokens?: number;
  ttft_ms: number;
  prefill_ms?: number;
  tps: number;
  provider_type: string;
  provider_name: string;
  model_name: string;
  react_steps?: number;
  is_estimated: boolean;
}

export interface ConversationTokenStatsRoleBucket {
  agent_role: 'primary' | 'scrubber' | 'summarizer' | string;
  call_count: number;
  prompt_tokens: number;
  cached_tokens: number;
  eval_tokens: number;
  completion_tokens: number;
  saved_tokens: number;
}

export interface ConversationTokenStatsProviderBucket {
  provider_type: string;
  provider_name: string;
  model_name: string;
  call_count: number;
  prompt_tokens: number;
  cached_tokens: number;
  eval_tokens: number;
  completion_tokens: number;
}

export interface ConversationTokenStatsSummary {
  total_turns: number;
  total_calls: number;
  total_prompt_tokens: number;
  total_cached_tokens: number;
  total_eval_tokens: number;
  total_completion_tokens: number;
  avg_cache_hit_pct: number;
  total_saved_tokens: number;
  scrub_saved_tokens: number;
  collapsed_saved_tokens: number;
  collapse_anchor_turn_id: number;
  avg_ttft_ms: number;
  avg_tps: number;
  hints: string[];
}

export interface ConversationTokenStats {
  conversation_id: string;
  summary: ConversationTokenStatsSummary;
  by_role: ConversationTokenStatsRoleBucket[];
  by_provider: ConversationTokenStatsProviderBucket[];
  turns: TurnTokenMetrics[];
}

export type LLMProviderType =
  | 'google_pool'
  | 'google_oauth'
  | 'gemini_api'
  | 'openai_compat'
  | 'anthropic'
  | 'ollama'
  | 'llama_server';

export interface LLMProviderItem {
  id: string;
  name: string;
  provider_type: LLMProviderType;
  base_url: string;
  api_key_masked: string;
  has_api_key: boolean;
  has_refresh_token?: boolean;
  cooldown_until?: string | null;
  is_cooling_down?: boolean;
  models: string[];
  role_binding: 'primary' | 'light' | 'both';
  tool_call_mode: 'auto' | 'native' | 'xml_prompt';
  max_context_tokens?: number;
  priority: number;
  is_enabled: number;
}

export interface SearchResult {
  message_id: number;
  conversation_id: string;
  workspace_id?: string;
  role: string;
  content: string;
  executed_by_account: string | null;
  created_at: string;
  conversation_title: string;
  highlighted_snippet: string;
}

export interface AuditLogItem {
  id: number;
  event_type: string;
  actor: string;
  account_name: string | null;
  detail: string;
  approved: number | null;
  created_at: string;
}

export type LoopStatus =
  | 'idle'
  | 'planning'
  | 'executing'
  | 'verifying'
  | 'reflecting'
  | 'waiting_approval'
  | 'completed'
  | 'failed'
  | 'paused';

export interface PendingApprovalCall {
  call_id: string;
  tool_name: string;
  arguments: Record<string, any>;
  policy_level: 'L0' | 'L1' | 'L2';
  risk_reason: string;
  snapshot_path?: string | null;
}

export interface TaskLoopResponse {
  id: string;
  workspace_id: string;
  conversation_id: string;
  goal_prompt: string;
  scratchpad_md?: string | null;
  loop_status: LoopStatus;
  current_iteration: number;
  max_iterations: number;
  pending_approval_call?: PendingApprovalCall | null;
  error_fingerprints?: string[];
  created_at: string;
  updated_at: string;
}

export interface ArtifactItem {
  id: string;
  workspace_id: string;
  conversation_id?: string | null;
  file_path: string;
  file_name: string;
  file_type: 'document' | 'code' | 'media' | 'report';
  file_size: number;
  source: string;
  summary?: string | null;
  created_at: string;
}

export interface ScheduledTaskItem {
  id: string;
  workspace_id: string;
  name: string;
  cron_expr: string;
  prompt_template: string;
  status: 'active' | 'paused' | 'running';
  last_run_at?: string | null;
  next_run_at?: string | null;
  last_status?: 'success' | 'failed' | null;
  last_output?: string | null;
  created_at: string;
}

export interface SkillItem {
  id: string;
  name: string;
  command?: string;
  scope: 'global' | 'workspace';
  is_override?: boolean;
  description?: string;
  version?: string;
  has_sop?: boolean;
  has_playbook?: boolean;
  sop_content?: string;
  experience_highlights?: string;
}

export interface ExtensionSkillItem {
  id: string;
  name: string;
  command: string;
  version?: string;
  updated_at?: string;
  type?: 'skill' | 'mcp' | 'suite' | string;
  tier?: string;
  mcp_server?: string;
  mcp_enabled?: boolean | null;
  has_tools?: boolean;
  tools?: string[];
  has_knowledge?: boolean;
  knowledge_files?: string[];
  has_schema?: boolean;
  description: string;
  path?: string;
  is_builtin?: boolean;
  has_content?: boolean;
  content?: string;
  body?: string;
}

export type ExecutionStrategy = 'direct' | 'plan_first';

export type InquirySubmitAction = 'execute_now' | 'clarify_only';

export interface InquiryOption {
  value: string;
  label: string;
  desc?: string;
}

export interface InquiryQuestion {
  id: string;
  type: 'single_choice' | 'multi_choice';
  label: string;
  options: InquiryOption[];
  default?: string | string[];
  fact_key?: string;
  fact_category?: 'user_pref' | 'biz_rule' | 'env_config';
  fact_scope?: 'workspace' | 'global';
}

export interface FillTemplateInfo {
  label: string;
  template_text: string;
}

export interface PlanStepItem {
  step: number;
  title: string;
  tool_level?: 'L0' | 'L1' | 'L2' | string;
  description?: string;
  selected?: boolean;
}

export interface InteractiveInquiryPayload {
  inquiry_id?: string;
  phase?: 'clarification' | 'plan_approval';
  title?: string;
  summary?: string;
  questions?: InquiryQuestion[];
  fill_template?: FillTemplateInfo;
  plan_steps?: PlanStepItem[];
  // 向下兼容旧版协议格式
  type?: 'choices' | 'template';
  question?: string;
  options?: InquiryOption[];
  template?: string;
}

