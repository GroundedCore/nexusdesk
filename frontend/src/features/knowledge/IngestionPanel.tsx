import { t } from '../../i18n/index';
import { useState } from 'react';
import { api, post, token } from '../../shared/api/client';
import { Alert, Field, Panel, useAccess, useAction, useResource } from '../../shared/components/ui';
import type { ModelResource } from '../models/ModelsPage';
export function IngestionPanel({ kid }: {
    kid: string;
}) {
    const { admin } = useAccess();
    const action = useAction();
    const [output, setOutput] = useState('');
    const [profile, setProfile] = useState('');
    const jobs = useResource<{
        id: string;
        filename: string;
        status: string;
        error_code: string | null;
    }[]>(kid ? `/knowledge-bases/${kid}/jobs` : null, 3000);
    const profiles = useResource<ModelResource[]>('/model-gateway/profiles');
    return <Panel title={t("文件解析与向量索引")}><Alert error={jobs.error || action.error} notice={action.notice}/><p className="hint">{t("文档由独立 knowledge Worker 处理。TXT/Markdown 可直接解析；PDF、Office、图片、OFD/CAJ/XPS 需要配置 Parser。向量索引需要 Milvus 与 Embedding 方案。")}</p>
    <Field label={t("上传文档")}><input disabled={!admin || action.busy} type="file" accept=".txt,.md,.pdf,.doc,.docx,.rtf,.odt,.ppt,.pptx,.xls,.xlsx,.png,.jpg,.jpeg,.ofd,.caj,.xps" onChange={e => { const file = e.target.files?.[0]; if (file)
        void action.run(async () => { const r = await fetch(`/api/v1/knowledge-bases/${kid}/uploads?filename=${encodeURIComponent(file.name)}`, { method: 'POST', headers: token() ? { Authorization: `Bearer ${token()}` } : {}, body: file }); if (!r.ok)
            throw new Error(JSON.stringify(await r.json())); jobs.refresh(); }, t("文档已进入解析队列")); }}/></Field>
    {jobs.data?.map(job => <div className="tool-item" key={job.id}><strong>{job.filename}</strong><p>{job.status} {job.error_code}</p><button className="secondary" onClick={() => void action.run(async () => setOutput(JSON.stringify(await api(`/knowledge-jobs/${job.id}/pages`), null, 2)), '')}>{t("查看解析页")}</button>{admin && ['queued', 'running'].includes(job.status) && <button onClick={() => void action.run(async () => { await post(`/knowledge-jobs/${job.id}/cancel`); jobs.refresh(); })}>{t("取消解析")}</button>}{admin && ['failed', 'cancelled'].includes(job.status) && <button onClick={() => void action.run(async () => { await post(`/knowledge-jobs/${job.id}/retry`); jobs.refresh(); })}>{t("重试")}</button>}</div>)}
    <Field label={t("向量化方案")}><select value={profile} onChange={e => setProfile(e.target.value)}><option value="">{t("选择已发布 Embedding")}</option>{profiles.data?.filter(p => p.enabled && p.published_version && p.spec.operation === 'embed').map(p => <option key={p.id} value={p.id}>{p.name} · v{p.published_version}</option>)}</select></Field><button disabled={!admin || !profile || action.busy} onClick={() => void action.run(async () => { const p = profiles.data!.find(p => p.id === profile)!; setOutput(JSON.stringify(await post(`/knowledge-bases/${kid}/vector-index`, { id: p.id, version: p.published_version }), null, 2)); }, t("向量索引已发布"))}>{t("重建向量索引")}</button><pre>{output}</pre>
  </Panel>;
}
