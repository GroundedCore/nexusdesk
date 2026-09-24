import { t } from '../../i18n/index';
import { InputNumber } from 'antd';
import { type AgentConfig } from '../../shared/api/client';
import { Field } from '../../shared/components/ui';
import type { ModelResource } from '../models/ModelsPage';
interface Props {
    config: AgentConfig;
    onChange: (config: AgentConfig) => void;
    profiles: ModelResource[] | null;
    refresh: () => void;
    disabled: boolean;
    onConfigure: () => void;
}
export function AgentModelPicker({ config, onChange, profiles, refresh, disabled, onConfigure }: Props) {
    const choices = (profiles || []).filter(p => p.enabled && p.published_version && p.spec.operation === 'chat');
    return <div className="agent-setting-card">
    <Field label={t("Chat 模型方案")}><select aria-label={t("Chat 模型方案")} disabled={disabled} value={config.model_profile_id || ''} onChange={e => {
            const profile = choices.find(p => p.id === e.target.value);
            onChange({ ...config, model_profile_id: profile?.id || null, model_profile_version: profile?.published_version || null });
        }}><option value="" disabled>{t("请选择已发布的 Chat 配置方案")}</option>
      {config.model_profile_id && !choices.some(p => p.id === config.model_profile_id) && <option value={config.model_profile_id}>{config.model_profile_id}{t("（未加载或不可用）")}</option>}
      {choices.map(p => <option key={p.id} value={p.id}>{p.name} · v{p.published_version}</option>)}
    </select></Field>
    {config.model_profile_id && <Field label={t("绑定版本")}><InputNumber min={1} precision={0} value={config.model_profile_version} disabled={disabled} onChange={v => onChange({ ...config, model_profile_version: v || 1 })}/></Field>}
    <p className="agent-muted">{t("选择网关中已发布的 Chat 配置方案，保存并发布 Agent 后生效。")}</p>
    {!config.model_profile_id && <p role="status" className="agent-muted">{t("尚未配置模型：可先保存草稿，配置模型方案后才能发布和运行。")}</p>}
    {!choices.length && profiles && <p className="agent-muted">{t("暂无可选方案。请前往\u201C模型网关 \u2192 配置方案\u201D，创建并发布 Chat 方案，再刷新此处选项。")}</p>}
    <button type="button" className="text-button" disabled={disabled} onClick={refresh}>{t("刷新方案列表")}</button>
    <button type="button" className="text-button" disabled={disabled} onClick={onConfigure}>{t("前往模型网关配置")}</button>
  </div>;
}
