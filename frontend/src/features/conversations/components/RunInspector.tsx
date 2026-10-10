import { Drawer } from 'antd';
import { t } from '../../../i18n';
import { Trace } from '../../../shared/components/ui';

interface Props {
  runId: string;
  open: boolean;
  onClose: () => void;
}

export function RunInspector({ runId, open, onClose }: Props) {
  return (
    <Drawer open={open} title={t('运行详情')} width={440} onClose={onClose}>
      <Trace runId={open ? runId : null} />
    </Drawer>
  );
}

