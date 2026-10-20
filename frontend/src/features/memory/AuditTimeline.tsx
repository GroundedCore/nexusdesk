import { Empty, Tag, Timeline, Typography } from 'antd';
import { t } from '../../i18n';
import type { MemoryAuditEvent } from '../../shared/api/client';
import { time } from '../../shared/components/ui';
import { auditActionLabels } from './labels';

interface Props {
  events: MemoryAuditEvent[];
  emptyText?: string;
}

function detailText(details: Record<string, unknown>): string {
  const entries = Object.entries(details || {});
  if (!entries.length)
    return '';
  const text = JSON.stringify(details);
  return text.length > 160 ? `${text.slice(0, 160)}…` : text;
}

/**
 * memory.* audit timeline. Payloads never contain memory content or PII
 * (Phase 4 C4), so rendering details verbatim adds no new disclosure surface.
 */
export function AuditTimeline({ events, emptyText }: Props) {
  if (!events.length)
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText || t('暂无审计记录')} />;
  const labels = auditActionLabels();
  return (
    <Timeline
      className="memory-timeline"
      items={events.map(event => ({
        key: event.id,
        children: (
          <div className="memory-timeline-event">
            <div className="memory-timeline-heading">
              <Tag color="blue">{labels[event.action] || event.action}</Tag>
              <Typography.Text type="secondary">{event.actor}</Typography.Text>
              <Typography.Text type="secondary">{time(event.created_at)}</Typography.Text>
            </div>
            <Typography.Text type="secondary" className="memory-timeline-action" code>{event.action}</Typography.Text>
            {detailText(event.details) && (
              <Typography.Paragraph type="secondary" className="memory-timeline-details">
                {detailText(event.details)}
              </Typography.Paragraph>
            )}
          </div>
        ),
      }))}
    />
  );
}
