export interface PlatformModule { id: string; name: string; description: string; status: string }
export interface AgentConfig { reply_language?: "auto"|"zh-CN"|"zh-TW"|"en"|"hi"; system_prompt: string; tool_names: string[]; knowledge_base_ids: string[]; max_model_rounds: number; model_profile_id?: string | null; model_profile_version?: number | null }
export interface Agent { created_by?: string | null; industry?:string; tags?:string[]; is_example?:boolean; updated_at?: string; created_at?: string; id: string; name: string; description: string; draft: AgentConfig; draft_revision: number; published_version: number | null; archived?: boolean }
export interface KnowledgeBase { id: string; name: string; document_count: number; enabled: boolean }
export interface Tool { revision?: number; published_version?: number; id: string; name: string; enabled: boolean; source: string; spec: Record<string, unknown> }
export interface Conversation { deleted_agent?: { id: string; name: string; published_version: number | null } | null; source?: "business" | "playground"; last_message?: string | null; revision?: number; id: string; external_id: string; mode: string; agent_id: string | null; assigned_to: string | null; updated_at: string }
export interface Run { id: string; conversation_id: string; status: string; error_code: string | null; created_at: string; output?: string }
export interface Message { id: string; seq: number; role: string; content: string; created_at: string }
export interface Action { id: string; status: string; payload: { title: string; description: string }; expires_at: string }
export interface ConversationDetail extends Conversation { messages: Message[]; runs: Run[]; actions: Action[] }
export interface Handoff { revision?: number; id: string; conversation_id: string; external_id: string; reason: string; summary: string; status: string; assignee: string | null }
export interface Ticket { ticket_no?: string; priority?: number; assignee_id?: string | null; created_at?: string; updated_at?: string; resolution?: string | null; id: string; title: string; description: string; status: string; note: string; revision: number }
export interface Channel { id: string; name: string; agent_id: string; enabled: boolean; token?: string }
export interface Document { id: string; title: string; content: string; current_version: number; enabled: boolean }
export interface SearchResult { chunk_id: string; title: string; version: number; excerpt: string; score: number }
export interface Summary { total: number; completed: number; failed: number; cancelled: number; running: number; queued: number; average_seconds: number | null; p95_seconds: number | null; tool_failures: number; tokens: number }
export interface EvaluationCase { id: string; name: string; spec: { input: string; expected_contains: string[] } }
export interface EvaluationReport { id: string; agent_version: number; created_at: string; results: { status?:string; judge?:unknown;judge_error?:string;similarity?:unknown; name: string; passed: boolean; output: string; tools: string[]; error: string | null }[] }
export interface Audit { id: number; actor: string; action: string; resource: string; created_at: string }

export function token() { return sessionStorage.getItem('agent-platform-token') || ''; }
export function setToken(value: string) { if (value) sessionStorage.setItem('agent-platform-token', value); else sessionStorage.removeItem('agent-platform-token'); }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const credential = token();
  if (options.body) headers.set('Content-Type', 'application/json');
  if (credential) headers.set('Authorization', `Bearer ${credential}`);
  const response = await fetch('/api/v1' + path, { ...options, headers });
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json() as { detail: unknown }).detail; } catch { detail = response.statusText; }
    if (response.status === 401 && credential === token() && ['invalid_api_token','invalid_local_session','invalid_enterprise_session','identity_provider_session_revoked'].includes(String(detail))) {
      window.dispatchEvent(new CustomEvent('nexusdesk:session-expired'));
    }
    throw new Error(`${response.status} · ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
export const post = <T,>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body: JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: 'PATCH', body: JSON.stringify(body) });
export const getModules = (signal?: AbortSignal) => api<PlatformModule[]>('/modules', { signal });
