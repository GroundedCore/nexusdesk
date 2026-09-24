import { useEffect, useState } from 'react';
import { t } from '../../i18n/index';
import { api } from '../../shared/api/client';
import { Field, useAccess } from '../../shared/components/ui';
import type { ModelResource } from './ModelsPage';

export function ProviderModelField({ connection, value, onChange }: {
  connection?: ModelResource; value: string; onChange: (value: string) => void;
}) {
  const { admin } = useAccess();
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ items: { id: string }[]; truncated: boolean } | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const supported = connection?.enabled && connection.spec.protocol === 'openai_compatible';
  useEffect(() => {
    const controller = new AbortController();
    setResult(null); setFailed(false); setLoading(false);
    if (admin && supported && connection) {
      setLoading(true);
      void api<{ items: { id: string }[]; truncated: boolean }>(
        `/model-gateway/connections/${connection.id}/available-models`, { signal: controller.signal },
      ).then(data => { if (!controller.signal.aborted) setResult(data); })
        .catch(() => { if (!controller.signal.aborted) setFailed(true); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }
    return () => controller.abort();
  }, [connection?.id, connection?.revision, supported, admin, revision]);
  return <div>
    <Field label={t('模型标识')}><input required value={value} onChange={e => onChange(e.target.value)} placeholder={t('供应商提供的模型名称')} /></Field>
    {admin && <>
      <div className="actions">
        <select aria-label={t('从渠道选择模型')} value="" disabled={!result?.items?.length || loading} onChange={e => { if (e.target.value) onChange(e.target.value); }}>
          <option value="">{loading ? t('正在获取模型列表…') : t('从渠道选择模型')}</option>
          {result?.items?.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}
        </select>
        <button type="button" className="secondary" disabled={!supported || loading} onClick={() => setRevision(n => n + 1)}>{t('刷新模型列表')}</button>
      </div>
      <p className="hint" aria-live="polite">{!supported ? t('请选择已启用的兼容协议渠道；也可手动填写模型标识。') : failed ? t('获取失败，请检查渠道凭据和服务地址；也可手动填写模型标识。') : result && !result.items?.length ? t('渠道未返回可选模型，请手动填写模型标识。') : result?.truncated ? t('仅显示部分模型，其余模型可手动填写。') : t('选择模型后仍需保存，并在配置方案中发布后使用。')}</p>
    </>}
  </div>;
}
