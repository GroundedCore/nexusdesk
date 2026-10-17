import { useState } from 'react';
import { t } from '../../i18n/index';
import type { Agent } from '../../shared/api/client';
import { Alert, Empty, Field, useResource } from '../../shared/components/ui';
import { AgentEvaluationPanel } from './AgentEvaluationPanel';

export function EvaluationPage() {
    const agents = useResource<Agent[]>('/agents');
    const [agent, setAgent] = useState('');
    const selected = agents.data?.find(item => item.id === agent);

    return <>
        <Alert error={agents.error}/>
        <Field label={t("待评测的发布版本")}>
            <select value={agent} onChange={event => setAgent(event.target.value)}>
                <option value="">{t("选择 Agent")}</option>
                {agents.data?.filter(item => item.published_version).map(item => <option key={item.id} value={item.id}>{item.name} · v{item.published_version}</option>)}
            </select>
        </Field>
        {selected ? <AgentEvaluationPanel
            key={selected.id}
            agentId={selected.id}
            publishedVersion={selected.published_version}
            disabled={!!selected.archived}
        /> : <Empty>{t("选择 Agent")}</Empty>}
    </>;
}
