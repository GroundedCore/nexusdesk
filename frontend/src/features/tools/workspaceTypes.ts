export type AuthKind = 'none' | 'bearer' | 'api_key' | 'custom';
export interface Definition {
  name: string; description: string; url: string; method?: 'GET' | 'POST'; parameters: Record<string, any>;
  headers?: Record<string, string>; headers_from_env?: Record<string, string>;
  stored_headers?: string[]; credential_id?: string | null; auth_kind?: AuthKind;
  response_schema?: Record<string, unknown> | null; timeout_seconds?: number; max_response_bytes?: number;
}
export interface Collection { id: string; name: string; description: string; icon: string; spec: Definition; revision: number; enabled: boolean; archived: boolean; api_count?: number; reference_count?: number; status?: string }
export interface ToolApi { id: string; name: string; display_name: string; spec: Definition; relative_path: string; auth_mode: 'inherit' | 'custom' | 'none'; revision: number; published_version: number | null; enabled: boolean; archived: boolean; changed: boolean; collection_changed: boolean; reference_count: number; updated_at: string }
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Detail extends Page<ToolApi> { collection: Collection }
export interface Reference { agent_id: string; agent_name: string; version: number; is_current: boolean; tool_id: string; tool_name: string; display_name: string }
export interface Change { field: string; before: unknown; after: unknown }
export interface Preview { revision: number; collection_revision: number; definition: Definition; changes: Change[]; references: Reference[] }
export interface Version { tool_id: string; version: number; display_name: string; name: string; created_at: string; changes: Change[] }
export interface Call { id: string; run_id: string | null; tool_name: string; display_name: string; tool_version: number; status: string; error_code: string | null; duration_ms: number; created_at: string }
export const ROOT = '/tool-workspace';
export const emptyParameters = { type: 'object', properties: {}, required: [], additionalProperties: false };
