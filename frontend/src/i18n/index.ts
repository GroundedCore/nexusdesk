import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import zhCN from './locales/zh-CN.json';
import zhTW from './locales/zh-TW.json';
import en from './locales/en.json';
import hi from './locales/hi.json';

export type Locale = 'zh-CN' | 'zh-TW' | 'en' | 'hi';
export const LOCALE_KEY = 'agent-platform-language';
export function resolveLocale(value: string): Locale | null {
  if (/^zh-(TW|HK|MO|Hant)(-|$)/i.test(value)) return 'zh-TW';
  if (/^zh($|-)/i.test(value)) return 'zh-CN';
  if (/^en($|-)/i.test(value)) return 'en';
  if (/^hi($|-)/i.test(value)) return 'hi';
  return null;
}
function initialLocale(): Locale {
  try { const saved=localStorage.getItem(LOCALE_KEY); if(saved && ['zh-CN','zh-TW','en','hi'].includes(saved)) return saved as Locale; } catch { /* Storage can be disabled. */ }
  return navigator.languages.map(resolveLocale).find(Boolean) || 'zh-CN';
}
void i18n.use(initReactI18next).init({
  lng:initialLocale(), fallbackLng:'zh-CN', supportedLngs:['zh-CN','zh-TW','en','hi'],
  resources:{'zh-CN':{translation:zhCN},'zh-TW':{translation:zhTW},en:{translation:en},hi:{translation:hi}},
  keySeparator:false, nsSeparator:false, initAsync:false,
  interpolation:{escapeValue:false}, react:{useSuspense:false},
});
export function t(key:string, options?:Record<string,unknown>):string {return i18n.t(key, options) as string;}
export function locale():Locale {return i18n.language as Locale;}
export function changeLanguage(value:Locale) {void i18n.changeLanguage(value);}
function updateDocument() {
  document.documentElement.lang=locale();document.title='nexusdesk · '+t('智能客服平台');
}
i18n.on('languageChanged',()=>{try{localStorage.setItem(LOCALE_KEY,locale());}catch{/* Preference remains for this session. */}updateDocument();});
updateDocument();
export const number=(value:number,options?:Intl.NumberFormatOptions)=>new Intl.NumberFormat(locale(),options).format(value);
export function dateTime(value:string) { const date=new Date(value); return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat(locale(),{dateStyle:'medium',timeStyle:'medium',hour12:false}).format(date); }
export function errorText(message:string) {
  const code=message.match(/^(?:\d{3} · )?([a-z][a-z0-9_]+)$/)?.[1];
  const known:Record<string,string>={
    tool_host_not_allowed:"服务地址不在服务器允许的主机列表中，请检查地址或联系管理员。",
    tool_revision_conflict_or_archived:"配置已修改或归档，请刷新后重试。",
    tool_revision_conflict_or_disabled:"配置已修改或停用，请刷新并检查启用状态。",
    invalid_tool_configuration:"工具配置无效，请检查地址、请求头和参数格式。",
    tool_name_exists:"调用标识已存在，请使用其他标识。",
    published_tools_archive_instead:"已有发布历史，请使用归档保留历史记录。",
    tool_has_agent_references:"仍有 Agent 引用，请先移除引用。",
    duplicate_paths_cannot_export_openapi:"存在重复接口路径，请调整后再导出 OpenAPI。",
    invalid_openapi_document:"OpenAPI 文档无效或超出解析限制，请检查文件。",

    tool_credential_reentry_required:'接口地址或鉴权请求头已改变，请重新输入密钥后保存。',
    tool_credential_key_unavailable:'无法读取密钥加密文件，请检查服务端密钥库配置。',
    agent_model_required:'请先选择已发布的 Chat 配置方案；没有可选方案时，请前往模型网关配置并发布。',
    model_not_configured:'尚未配置对话模型，请在模型网关发布 Chat 配置方案并绑定到 Agent。',
    agent_not_found:'该智能体已不存在，请关闭弹窗并刷新列表。',
    agent_archived:'此 Agent 已归档，请返回列表恢复后编辑。',
    agent_not_published:'请先保存并发布 Agent',
    database_unavailable:'无法连接后端或读取权限：',
    agent_missing_or_revision_conflict:'智能体已被修改或恢复，请关闭弹窗并刷新列表后重新操作。',
    agent_revision_conflict:'智能体已被修改或恢复，请关闭弹窗并刷新列表后重新操作。',
    draft_revision_conflict:'智能体已被修改或恢复，请关闭弹窗并刷新列表后重新操作。',
    model_resource_disabled:'模型方案不可用，请检查模型网关配置',
    model_resource_not_found:'模型方案不可用，请检查模型网关配置',
    profile_version_not_found:'模型方案不可用，请检查模型网关配置',
    ticket_revision_conflict:'工单已被修改，请加载最新版本后重试',
    forbidden:'当前角色没有执行此操作的权限',
    unauthorized:'访问凭据无效，请重新配置',
  };
  if(code)return `${t(known[code]||'请求失败，请重试或联系管理员')} (${message})`;
  if(/^422 ·/.test(message))return `${t('请检查必填项和输入格式')} (422)`;
  return i18n.exists(message)?t(message):message;
}
export default i18n;
