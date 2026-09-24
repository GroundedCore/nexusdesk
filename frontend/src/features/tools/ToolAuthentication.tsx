import { t } from '../../i18n';
import { Field } from '../../shared/components/ui';

export interface HeaderReference { header: string; variable: string; source?: 'secret' | 'environment'; savedHeader?: string }

export function readHeaderReferences(spec: Record<string, unknown>): HeaderReference[] {
    return [
        ...Object.entries((spec.headers_from_env || {}) as Record<string, string>)
            .map(([header, variable]) => ({ header, variable, source: 'environment' as const })),
        ...((spec.stored_headers || []) as string[])
            .map(header => ({ header, variable: '', source: 'secret' as const, savedHeader: header })),
    ];
}

export function serializeHeaderReferences(rows: HeaderReference[]) {
    const names = new Set<string>();
    const environment: [string, string][] = [];
    const secrets: [string, string | null][] = [];
    rows.forEach(row => {
        const header = row.header.trim();
        if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(header))
            throw new Error(t('请输入有效的 HTTP 请求头名称'));
        if (names.has(header.toLowerCase()))
            throw new Error(t('请求头名称不能重复（不区分大小写）'));
        names.add(header.toLowerCase());
        if (row.source === 'environment') {
            const variable = row.variable.trim();
            if (!/^AGENT_TOOL_SECRET_[A-Za-z0-9_]+$/.test(variable))
                throw new Error(t('密钥变量名必须以 AGENT_TOOL_SECRET_ 开头，后接字母、数字或下划线'));
            environment.push([header, variable]);
        } else {
            if (!row.variable && row.savedHeader?.toLowerCase() === header.toLowerCase()) {
                secrets.push([header, null]);
            } else {
                if (!row.variable.trim() || /[^\x20-\x7e]/.test(row.variable) || row.variable.length > 8192)
                    throw new Error(t('请输入有效密钥；不能包含换行或控制字符'));
                secrets.push([header, row.variable]);
            }
        }
    });
    return { headers_from_env: Object.fromEntries(environment), header_secrets: Object.fromEntries(secrets) };
}

export function ToolAuthentication({ rows, onChange }: {
    rows: HeaderReference[];
    onChange: (rows: HeaderReference[]) => void;
}) {
    function update(index: number, patch: Partial<HeaderReference>) {
        onChange(rows.map((row, i) => i === index ? { ...row, ...patch } : row));
    }
    return <div>
        <Field label={t('接口鉴权')}>
            <select value={rows.length ? 'headers' : 'none'} onChange={e => onChange(e.target.value === 'none' ? [] : [{ header: 'Authorization', variable: '' }])}>
                <option value="none">{t('无需鉴权')}</option>
                <option value="headers">{t('密钥鉴权')}</option>
            </select>
        </Field>
        {rows.length > 0 && <>
            <p className="hint">{t('直接输入密钥，保存后加密存储且不回显。已保存的密钥留空保留，输入新值替换；移除请求头后保存可清除。')}</p>
            <p className="hint">{t('Authorization 请填写完整的 Bearer <实际密钥>；X-API-Key 直接填写实际密钥。修改接口地址或请求头名称时，请重新输入密钥。')}</p>
            {rows.map((row, index) => <div key={index}>
                <Field label={t('请求头名称')}><input required value={row.header} placeholder="Authorization / X-API-Key" onChange={e => update(index, { header: e.target.value })}/></Field>
                <Field label={t('密钥来源')}><select value={row.source || 'secret'} onChange={e => update(index, { source: e.target.value as 'secret' | 'environment', variable: '', savedHeader: undefined })}>
                    <option value="secret">{t('直接输入密钥')}</option><option value="environment">{t('环境变量引用（兼容已有配置）')}</option>
                </select></Field>
                {row.source === 'environment' ? <Field label={t('服务端密钥变量名')}><input required value={row.variable} placeholder="AGENT_TOOL_SECRET_ORDER_API" autoComplete="off" spellCheck={false} onChange={e => update(index, { variable: e.target.value })}/></Field>
                    : <Field label={t('密钥值')}><input type="password" required={!row.savedHeader || row.savedHeader.toLowerCase() !== row.header.trim().toLowerCase()} value={row.variable} placeholder={row.savedHeader ? t('已配置，留空保留') : t('请输入密钥')} autoComplete="new-password" spellCheck={false} maxLength={8192} onChange={e => update(index, { variable: e.target.value })}/></Field>}
                <button type="button" className="secondary" onClick={() => onChange(rows.filter((_, i) => i !== index))}>{t('移除鉴权请求头')}</button>
            </div>)}
            <button type="button" className="secondary" onClick={() => onChange([...rows, { header: '', variable: '' }])}>{t('添加鉴权请求头')}</button>
        </>}
    </div>;
}
