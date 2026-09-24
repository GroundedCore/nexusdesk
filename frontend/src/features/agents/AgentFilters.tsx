import { t } from '../../i18n/index';
import { Button, Tag } from 'antd';
export const INDUSTRY_NAMES: Record<string, string> = { retail: '电商零售', saas: '企业软件（SaaS）', manufacturing: '制造业', education: '教育培训', travel: '酒店文旅', property: '房产物业', logistics: '物流运输', hr: '企业人力资源', gaming: '游戏' };
export const PURPOSE_NAMES: Record<string, string> = { consulting: '咨询导购', after_sales: '售后支持', operations: '业务办理', knowledge: '知识问答' };
export const INDUSTRIES = Object.keys(INDUSTRY_NAMES);
export const PURPOSES = Object.keys(PURPOSE_NAMES);
export function industryLabel(code: string) { return INDUSTRY_NAMES[code] ? t(INDUSTRY_NAMES[code]) : code; }
export function purposeLabel(code: string) { return PURPOSE_NAMES[code] ? t(PURPOSE_NAMES[code]) : code; }
function legacyCode(value: string, names: Record<string, string>) { return Object.entries(names).find(([, name]) => name === value)?.[0] || value; }
export interface Filters {
    q: string;
    status: string;
    archived: boolean;
    industries: string[];
    tags: string[];
    scope: 'all' | 'mine' | 'examples';
    page: number;
    pageSize: number;
}
export const emptyFilters: Filters = { q: '', status: 'all', archived: false, industries: [], tags: [], scope: 'all', page: 1, pageSize: 12 };
export function readFilters(route: string): Filters {
    const p = new URLSearchParams(route.split('?')[1]);
    return { q: (p.get('q') || '').slice(0, 100), status: ['published', 'unpublished'].includes(p.get('status') || '') ? p.get('status')! : 'all',
        archived: p.get('archived') === '1', industries: [...new Set(p.getAll('industry').map(v => legacyCode(v, INDUSTRY_NAMES)))].filter(v => INDUSTRIES.includes(v)),
        tags: [...new Set(p.getAll('tag').map(v => legacyCode(v, PURPOSE_NAMES)))].filter(v => PURPOSES.includes(v)), scope: p.get('scope') === 'mine' ? 'mine' : p.get('scope') === 'examples' || (!p.has('scope') && p.get('examples') === '1') ? 'examples' : 'all',
        page: Math.floor(Math.max(1, Math.min(100000, Number(p.get('page')) || 1))), pageSize: [12, 24, 48].includes(Number(p.get('size'))) ? Number(p.get('size')) : 12 };
}
export function filterSuffix(f: Filters) {
    const p = new URLSearchParams();
    if (f.q)
        p.set('q', f.q);
    if (f.status !== 'all')
        p.set('status', f.status);
    if (f.archived)
        p.set('archived', '1');
    f.industries.forEach(v => p.append('industry', v));
    f.tags.forEach(v => p.append('tag', v));
    if (f.scope !== 'all')
        p.set('scope', f.scope);
    if (f.page > 1)
        p.set('page', String(f.page));
    if (f.pageSize !== 12)
        p.set('size', String(f.pageSize));
    return p.size ? `?${p}` : '';
}
export function AgentFilters({ value, onChange }: {
    value: Filters;
    onChange: (patch: Partial<Filters>) => void;
}) {
    function toggle(field: 'industries' | 'tags', label: string) { onChange({ [field]: value[field].includes(label) ? value[field].filter(v => v !== label) : [...value[field], label] }); }
    return <div className="agent-filter-panel">
    {([{ field: 'industries', title: t("行业"), options: INDUSTRIES }, { field: 'tags', title: t("用途"), options: PURPOSES }] as const).map(group => <div className="agent-filter-row" key={group.field}>
      <strong>{group.title}</strong><div><Button size="small" type={!value[group.field].length ? 'primary' : 'text'} aria-label={t("{{v0}}：全部", { v0: group.title })} aria-pressed={!value[group.field].length} onClick={() => onChange({ [group.field]: [] })}>{t("全部")}</Button>
      {group.options.map(label => <Button key={label} size="small" type={value[group.field].includes(label) ? 'primary' : 'text'} aria-label={`${group.title}：${group.field === 'industries' ? industryLabel(label) : purposeLabel(label)}`} aria-pressed={value[group.field].includes(label)} onClick={() => toggle(group.field, label)}>{group.field === 'industries' ? industryLabel(label) : purposeLabel(label)}</Button>)}</div>
    </div>)}
    <div className="agent-filter-row"><strong>{t("范围")}</strong><div className="agent-scope-filter">{([{value: 'all', label: t("全部")}, {value: 'mine', label: t("我创建的")}, {value: 'examples', label: t("仅看案例")}] as const).map(option => <Button key={option.value} size="small" type={value.scope === option.value ? 'primary' : 'text'} aria-label={`${t("范围")}：${option.label}`} aria-pressed={value.scope === option.value} onClick={() => onChange({scope: option.value})}>{option.label}</Button>)}</div><span className="agent-muted">{t("同组多选满足任一项，行业与用途组合筛选")}</span></div>
    {(value.industries.length > 0 || value.tags.length > 0 || value.scope !== 'all') && <div className="agent-selected-filters"><span>{t("已选：")}</span>{value.industries.map(v => <Tag key={v} closable onClose={() => toggle('industries', v)}>{industryLabel(v)}</Tag>)}{value.tags.map(v => <Tag key={v} closable onClose={() => toggle('tags', v)}>{purposeLabel(v)}</Tag>)}{value.scope !== 'all' && <Tag closable onClose={() => onChange({ scope: 'all' })}>{value.scope === 'mine' ? t("我创建的") : t("仅看案例")}</Tag>}<Button type="link" size="small" onClick={() => onChange({ industries: [], tags: [], scope: 'all' })}>{t("清空标签")}</Button></div>}
  </div>;
}
