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
  memory_refs?: MemoryRefItem[];
  downgrade_info?: {
    original_model: string;
    current_model: string;
    reason?: string;
    message?: string;
  };
  tool_request?: {
    tool_name: string;
    tool_input: Record<string, any>;
    status?: 'pending' | 'approved' | 'rejected';
  };
}

export interface SearchResult {
  message_id: number;
  conversation_id: string;
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
