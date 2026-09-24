import { t } from '../../i18n/index';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../shared/api/client';
import type { ModelResource } from './ModelsPage';
// Dropdowns must not silently omit resources beyond the legacy 200-row endpoint.
export function useCatalogOptions(kind: 'connections' | 'models' | 'profiles', enabled = true) {
    const [data, setData] = useState<ModelResource[] | null>(null);
    const [error, setError] = useState('');
    const [revision, setRevision] = useState(0);
    const refresh = useCallback(() => setRevision(value => value + 1), []);
    useEffect(() => {
        if (!enabled) {
            setData(null);
            setError('');
            return;
        }
        const controller = new AbortController();
        setError('');
        async function load() {
            try {
                const rows: ModelResource[] = [];
                for (let page = 1;; page += 1) {
                    const result = await api<{
                        items: ModelResource[];
                        total: number;
                    }>(`/model-gateway/catalog/${kind}?page=${page}&page_size=200`, { signal: controller.signal });
                    rows.push(...result.items);
                    if (rows.length >= result.total || !result.items.length)
                        break;
                }
                if (!controller.signal.aborted)
                    setData(rows);
            }
            catch (e) {
                if (!controller.signal.aborted)
                    setError(e instanceof Error ? e.message : t("\u8D44\u6E90\u9009\u9879\u52A0\u8F7D\u5931\u8D25"));
            }
        }
        void load();
        return () => controller.abort();
    }, [kind, revision, enabled]);
    return { data, error, refresh };
}
