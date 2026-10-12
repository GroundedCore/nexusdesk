import { t } from '../../i18n/index';
import { useState } from 'react';
import {
    patch,
    post,
    type Agent,
    type Channel,
    type ChannelDelivery,
    type ChannelField,
    type ChannelProvider,
} from '../../shared/api/client';
import {
    Alert,
    Empty,
    Field,
    Panel,
    useAccess,
    useAction,
    useResource,
} from '../../shared/components/ui';

function ProviderInput({
    field,
    value,
    onChange,
}: {
    field: ChannelField;
    value: string;
    onChange: (value: string) => void;
}) {
    if (field.type === 'select') {
        return (
            <select
                required={field.required}
                value={value}
                onChange={event => onChange(event.target.value)}
            >
                <option value="">{t('请选择')}</option>
                {field.options?.map(option => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                ))}
            </select>
        );
    }
    return (
        <input
            required={field.required}
            type={field.secret ? 'password' : 'text'}
            value={value}
            onChange={event => onChange(event.target.value)}
            autoComplete="off"
        />
    );
}

function ProviderFields({
    fields,
    values,
    onChange,
    credentials = false,
}: {
    fields: ChannelField[];
    values: Record<string, string>;
    onChange: (name: string, value: string) => void;
    credentials?: boolean;
}) {
    return <>{fields.map(field => {
        const effective = credentials && field.secret
            ? { ...field, required: false }
            : field;
        return (
            <Field
                key={field.name}
                label={`${field.label}${effective.required ? '' : t('（可选）')}`}
            >
                <ProviderInput
                    field={effective}
                    value={values[field.name] || ''}
                    onChange={value => onChange(field.name, value)}
                />
            </Field>
        );
    })}</>;
}

function DeliveryList({ channelId }: { channelId: string }) {
    const rows = useResource<{ items: ChannelDelivery[]; total: number }>(
        `/channels/${channelId}/deliveries`,
        5000,
    );
    return (
        <Panel title={t('投递记录')}>
            <Alert error={rows.error}/>
            {!rows.loading && !rows.data?.items.length && <Empty>{t('暂无投递记录')}</Empty>}
            {rows.data?.items.map(item => (
                <article className="tool-item" key={item.id}>
                    <div className="row">
                        <strong>{item.role}</strong>
                        <span className={`badge ${item.status}`}>{item.status}</span>
                    </div>
                    <p className="mono small">{item.message_id}</p>
                    <p className="hint">
                        {t('尝试次数')}: {item.attempts}
                        {item.http_status ? ` · HTTP ${item.http_status}` : ''}
                        {item.error_code ? ` · ${item.error_code}` : ''}
                    </p>
                </article>
            ))}
        </Panel>
    );
}

export function ChannelsPage() {
    const rows = useResource<Channel[]>('/channels');
    const agents = useResource<Agent[]>('/agents');
    const providers = useResource<ChannelProvider[]>('/channels/providers');
    const action = useAction();
    const { admin } = useAccess();
    const [provider, setProvider] = useState('generic_webhook');
    const [signing, setSigning] = useState('');
    const [name, setName] = useState('');
    const [agent, setAgent] = useState('');
    const [config, setConfig] = useState<Record<string, string>>({});
    const [credentials, setCredentials] = useState<Record<string, string>>({});
    const [created, setCreated] = useState<Channel | null>(null);
    const [editing, setEditing] = useState<Channel | null>(null);
    const [editConfig, setEditConfig] = useState<Record<string, string>>({});
    const [editCredentials, setEditCredentials] = useState<Record<string, string>>({});
    const [deliveriesFor, setDeliveriesFor] = useState('');
    const selectedProvider = providers.data?.find(item => item.id === provider);
    const native = provider !== 'generic_webhook';
    const providerName = (value: string) =>
        providers.data?.find(item => item.id === value)?.name || value;

    function chooseProvider(value: string) {
        const definition = providers.data?.find(item => item.id === value);
        setProvider(value);
        setConfig(Object.fromEntries(
            (definition?.config_fields || []).map(field => [field.name, field.default || '']),
        ));
        setCredentials({});
    }

    function beginEdit(channel: Channel) {
        setEditing(channel);
        setEditConfig(Object.fromEntries(
            Object.entries(channel.config || {}).map(([key, value]) => [key, String(value ?? '')]),
        ));
        setEditCredentials({});
    }

    return (
        <div className="channel-workspace">
            <Alert
                error={rows.error || agents.error || providers.error || action.error}
                notice={action.notice}
            />
            <div className="split">
                <Panel title={t('接入渠道')}>
                    {rows.data?.map(channel => (
                        <article className="tool-item" key={channel.id}>
                            <div className="row">
                                <strong>{channel.name}</strong>
                                <span className={`badge ${channel.enabled ? 'success' : 'default'}`}>
                                    {channel.enabled ? t('已启用') : t('已停用')}
                                </span>
                            </div>
                            <p className="small">
                                {providerName(channel.provider)} · {channel.credentials_configured
                                    ? t('凭据已配置')
                                    : t('凭据未配置')}
                            </p>
                            <p className="mono small">{channel.id}</p>
                            <p>
                                Agent：{agents.data?.find(item => item.id === channel.agent_id)?.name
                                    || channel.agent_id}
                            </p>
                            {channel.provider !== 'generic_webhook' && (
                                <p className="mono small">
                                    POST /api/v1/ingress/channels/{channel.id}/webhook/{channel.provider}
                                </p>
                            )}
                            {channel.last_error_code && (
                                <p className="error-text">{channel.last_error_code}</p>
                            )}
                            <button
                                className="secondary"
                                disabled={!admin || action.busy}
                                onClick={() => void action.run(async () => {
                                    await patch(`/channels/${channel.id}`, {
                                        enabled: !channel.enabled,
                                    });
                                    rows.refresh();
                                })}
                            >
                                {channel.enabled ? t('停用') : t('启用')}
                            </button>
                            {channel.provider === 'generic_webhook' && (
                                <button
                                    className="secondary"
                                    disabled={!admin || action.busy}
                                    onClick={() => void action.run(async () => {
                                        setCreated(await post<Channel>(
                                            `/channels/${channel.id}/rotate-token`,
                                        ));
                                        rows.refresh();
                                    }, t('已轮换，旧令牌立即失效'))}
                                >
                                    {t('轮换渠道令牌')}
                                </button>
                            )}
                            {channel.provider !== 'generic_webhook' && (
                                <button
                                    className="secondary"
                                    disabled={!admin || action.busy}
                                    onClick={() => beginEdit(channel)}
                                >
                                    {t('配置凭据')}
                                </button>
                            )}
                            <button
                                className="text-button"
                                onClick={() => setDeliveriesFor(
                                    deliveriesFor === channel.id ? '' : channel.id,
                                )}
                            >
                                {deliveriesFor === channel.id ? t('收起投递记录') : t('查看投递记录')}
                            </button>
                        </article>
                    ))}
                    {rows.data?.length === 0 && <Empty>{t('创建渠道接入业务系统')}</Empty>}
                </Panel>
                <div className="stack">
                    <Panel title={t('创建渠道')}>
                        <form onSubmit={event => {
                            event.preventDefault();
                            void action.run(async () => {
                                const body = native
                                    ? { name, agent_id: agent, provider, config, credentials }
                                    : {
                                        name,
                                        agent_id: agent,
                                        provider,
                                        signing_secret_ref: signing || null,
                                    };
                                setCreated(await post<Channel>('/channels', body));
                                rows.refresh();
                                setName('');
                            }, native
                                ? t('渠道已创建，请完成平台回调配置')
                                : t('渠道已创建，请保存本次显示的令牌'));
                        }}>
                            <fieldset disabled={!admin || action.busy}>
                                <Field label={t('渠道名称')}>
                                    <input
                                        required
                                        value={name}
                                        onChange={event => setName(event.target.value)}
                                    />
                                </Field>
                                <Field label={t('渠道类型')}>
                                    <select
                                        required
                                        value={provider}
                                        onChange={event => chooseProvider(event.target.value)}
                                    >
                                        {providers.data?.map(item => (
                                            <option key={item.id} value={item.id}>{item.name}</option>
                                        ))}
                                    </select>
                                </Field>
                                <Field label={t('已发布 Agent')}>
                                    <select
                                        required
                                        value={agent}
                                        onChange={event => setAgent(event.target.value)}
                                    >
                                        <option value="">{t('请选择')}</option>
                                        {agents.data
                                            ?.filter(item => item.published_version)
                                            .map(item => (
                                                <option key={item.id} value={item.id}>{item.name}</option>
                                            ))}
                                    </select>
                                </Field>
                                {native && selectedProvider && (
                                    <>
                                        <ProviderFields
                                            fields={selectedProvider.config_fields}
                                            values={config}
                                            onChange={(key, value) => setConfig(
                                                old => ({ ...old, [key]: value }),
                                            )}
                                        />
                                        <ProviderFields
                                            fields={selectedProvider.credential_fields}
                                            values={credentials}
                                            onChange={(key, value) => setCredentials(
                                                old => ({ ...old, [key]: value }),
                                            )}
                                        />
                                    </>
                                )}
                                {!native && (
                                    <Field label={t('签名密钥环境变量引用（可选）')}>
                                        <input
                                            value={signing}
                                            onChange={event => setSigning(event.target.value)}
                                            placeholder="AGENT_CHANNEL_SECRET_WEBHOOK"
                                        />
                                    </Field>
                                )}
                                <button>{t('创建渠道')}</button>
                            </fieldset>
                        </form>
                    </Panel>
                    {editing && (
                        <Panel title={`${providerName(editing.provider)} · ${t('配置')}`}>
                            <form onSubmit={event => {
                                event.preventDefault();
                                void action.run(async () => {
                                    await post(`/channels/${editing.id}/config`, {
                                        config: editConfig,
                                        credentials: editCredentials,
                                    });
                                    setEditing(null);
                                    rows.refresh();
                                }, t('渠道配置已保存'));
                            }}>
                                <fieldset disabled={!admin || action.busy}>
                                    <ProviderFields
                                        fields={providers.data?.find(item => item.id === editing.provider)
                                            ?.config_fields || []}
                                        values={editConfig}
                                        onChange={(key, value) => setEditConfig(
                                            old => ({ ...old, [key]: value }),
                                        )}
                                    />
                                    <ProviderFields
                                        fields={providers.data?.find(item => item.id === editing.provider)
                                            ?.credential_fields || []}
                                        values={editCredentials}
                                        credentials
                                        onChange={(key, value) => setEditCredentials(
                                            old => ({ ...old, [key]: value }),
                                        )}
                                    />
                                    <button>{t('保存配置')}</button>
                                    <button
                                        type="button"
                                        className="secondary"
                                        onClick={() => setEditing(null)}
                                    >
                                        {t('取消')}
                                    </button>
                                </fieldset>
                            </form>
                        </Panel>
                    )}
                    {created && (
                        <Panel title={created.token ? t('本次创建的访问令牌') : t('渠道配置信息')}>
                            {created.token ? (
                                <>
                                    <p className="hint">{t('令牌仅显示这一次。服务端只保存摘要。')}</p>
                                    <code className="secret">{created.token}</code>
                                    <p className="small mono">
                                        POST /api/v1/ingress/channels/{created.id}/messages
                                    </p>
                                </>
                            ) : (
                                <>
                                    <p>{t('请在对应聊天软件中配置以下回调地址：')}</p>
                                    <code className="secret">
                                        /api/v1/ingress/channels/{created.id}/webhook/{created.provider}
                                    </code>
                                </>
                            )}
                        </Panel>
                    )}
                    {deliveriesFor && <DeliveryList channelId={deliveriesFor}/>}
                    <Panel title={t('接入说明')}>
                        <p className="hint">
                            {native
                                ? t('渠道使用平台原生签名校验，Agent 和人工回复会通过出站投递 Worker 主动发送。')
                                : t('通用 Webhook 使用渠道 Token；回复通过 replies 接口按 seq 拉取。')}
                        </p>
                    </Panel>
                </div>
            </div>
        </div>
    );
}
