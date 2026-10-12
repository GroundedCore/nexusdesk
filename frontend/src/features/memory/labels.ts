import { t } from '../../i18n';

// Memory item types: Phase 2 uses profile / preference (event in Phase 3, flag in Phase 4).
export const typeLabels = (): Record<string, string> => ({ profile: t('画像'), preference: t('偏好') });
export const statusLabels = (): Record<string, string> => ({ pending: t('待确认'), active: t('生效中'), expired: t('已过期'), deleted: t('已删除') });
export const statusColors: Record<string, string> = { pending: 'warning', active: 'success', expired: 'default', deleted: 'default' };
export const sourceTypeLabels = (): Record<string, string> => ({ extracted: t('自动抽取'), human: t('人工维护') });
export const identitySourceLabels = (): Record<string, string> => ({ channel: t('渠道会话'), open_platform: t('开放平台'), enterprise: t('企业嵌入') });
