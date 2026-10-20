import { t } from '../../i18n';

// Memory item types: profile / preference (Phase 2), event (Phase 3), flag (Phase 4).
export const typeLabels = (): Record<string, string> => ({ profile: t('画像'), preference: t('偏好'), event: t('事件'), flag: t('服务标记') });
export const statusLabels = (): Record<string, string> => ({ pending: t('待确认'), active: t('生效中'), expired: t('已过期'), deleted: t('已删除') });
export const statusColors: Record<string, string> = { pending: 'warning', active: 'success', expired: 'default', deleted: 'default' };
export const sourceTypeLabels = (): Record<string, string> => ({ extracted: t('自动抽取'), human: t('人工维护'), ticket: t('工单事件'), rule: t('规则引擎') });
// Phase 4 governance flags: fixed key set, human- or rule-maintained.
export const flagKeys = ['vip', 'complaint_risk', 'blacklist'];
export const flagKeyLabels = (): Record<string, string> => ({ vip: t('VIP 客户'), complaint_risk: t('投诉风险'), blacklist: t('黑名单') });
export const flagKeyColors: Record<string, string> = { vip: 'gold', complaint_risk: 'warning', blacklist: 'error' };
export const identitySourceLabels = (): Record<string, string> => ({ channel: t('渠道会话'), open_platform: t('开放平台'), enterprise: t('企业嵌入') });
// Phase 6 · injection sections (D2: summary is not logged) and memory.* audit actions.
export const sectionLabels = (): Record<string, string> => ({ flag: t('服务标记'), profile: t('画像'), preference: t('偏好'), event: t('事件') });
export const auditActionLabels = (): Record<string, string> => ({
  'memory.extract.applied': t('自动抽取'),
  'memory.event.written': t('事件写入'),
  'memory.item.edited': t('人工编辑'),
  'memory.item.confirmed': t('确认'),
  'memory.item.rejected': t('拒绝'),
  'memory.item.deleted': t('删除'),
  'memory.item.restored': t('恢复'),
  'memory.item.released': t('恢复自动管理'),
  'memory.decay_pending': t('衰退迁移'),
  'memory.decay_expired': t('衰退过期'),
  'memory.evicted': t('淘汰'),
  'memory.flag.upserted': t('服务标记'),
  'memory.flag.removed': t('解除标记'),
  'memory.customer.merged': t('客户合并'),
  'memory.customer.deleted': t('客户删除'),
});
