import { useState } from 'react';
import { Alert, Button, Empty, Input, Skeleton, Tree } from 'antd';
import { AppstoreOutlined, FolderAddOutlined, FolderOutlined, FolderOpenOutlined, SearchOutlined } from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';

export type KnowledgeFolder = { id: string; name: string; parent_id: string | null };

export function folderPath(folders: KnowledgeFolder[], id?: string): KnowledgeFolder[] {
    const path: KnowledgeFolder[] = []; const seen = new Set<string>();
    let node = folders.find(f => f.id === id);
    while (node && !seen.has(node.id)) {
        seen.add(node.id); path.unshift(node); node = folders.find(f => f.id === node!.parent_id);
    }
    return path;
}

export function FolderSidebar({ folders, selected, admin, loading, error, onSelect, onCreate, onRefresh }: {
    folders: KnowledgeFolder[]; selected?: string; admin: boolean; loading: boolean; error: string;
    onSelect: (id?: string) => void; onCreate: (parent?: string) => void; onRefresh: () => void;
}) {
    const [query, setQuery] = useState('');
    const [expanded, setExpanded] = useState<React.Key[] | null>(null);
    const term = query.trim().toLocaleLowerCase();
    const matches = folders.filter(f => f.name.toLocaleLowerCase().includes(term));
    const visible = new Set(matches.flatMap(f => folderPath(folders, f.id).map(p => p.id)));
    const selectedFolder = folders.find(f => f.id === selected);
    function nodes(parent: string | null, seen = new Set<string>()): DataNode[] {
        return folders.filter(f => f.parent_id === parent && !seen.has(f.id) && (!term || visible.has(f.id))).map(f => ({
            key: f.id,
            title: <span className="knowledge-folder-name" title={folderPath(folders, f.id).map(p => p.name).join(' / ')}>{f.name}</span>,
            icon: ({ expanded: open }) => open ? <FolderOpenOutlined /> : <FolderOutlined />,
            children: nodes(f.id, new Set([...seen, f.id])),
        }));
    }
    return <aside className="knowledge-folders" aria-label="文档目录导航">
        <div className="knowledge-folders-heading"><div><h3>文档目录</h3><span>{folders.length} 个目录</span></div>{admin && <Button type="text" aria-label="新建根目录" title="新建根目录" icon={<FolderAddOutlined />} onClick={() => onCreate()} />}</div>
        <Input aria-label="搜索目录" placeholder="搜索目录名称" prefix={<SearchOutlined />} allowClear value={query} onChange={e => setQuery(e.target.value)} />
        <button type="button" className={`knowledge-all-documents ${!selected ? 'is-selected' : ''}`} aria-current={!selected ? 'page' : undefined} onClick={() => onSelect()}><AppstoreOutlined /><span>全部文档</span></button>
        <div className="knowledge-folder-tree-heading"><span>{term ? `找到 ${matches.length} 个目录` : '目录结构'}</span><Button size="small" type="text" disabled={!!term || !folders.length} onClick={() => setExpanded((expanded ?? folders.filter(f => !f.parent_id).map(f => f.id)).length ? [] : folders.map(f => f.id))}>{(expanded ?? folders.filter(f => !f.parent_id).map(f => f.id)).length ? '全部收起' : '全部展开'}</Button></div>
        {error ? <Alert type="error" title="目录加载失败" action={<Button size="small" onClick={onRefresh}>重试</Button>} /> : loading && !folders.length ? <Skeleton active paragraph={{ rows: 4 }} title={false} /> : <div className="knowledge-folder-tree">
            {(!folders.length || (term && !matches.length)) ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={term ? '没有匹配的目录' : '还没有目录'}>{!term && admin && <Button size="small" onClick={() => onCreate()}>创建第一个目录</Button>}</Empty> : <Tree aria-label="目录结构" blockNode showIcon showLine={{ showLeafIcon: false }} selectedKeys={selected ? [selected] : []} expandedKeys={term ? [...visible] : expanded ?? folders.filter(f => !f.parent_id).map(f => f.id)} onExpand={keys => setExpanded(keys)} treeData={nodes(null)} onSelect={keys => { if (keys.length) { const id = String(keys[0]); setExpanded(previous => [...new Set([...(previous ?? folders.filter(f => !f.parent_id).map(f => f.id)), ...folderPath(folders, id).map(f => f.id)])]); onSelect(id); } }} />}
        </div>}
        {admin && <div className="knowledge-folder-footer"><Button block icon={<FolderAddOutlined />} disabled={!selectedFolder} onClick={() => onCreate(selected)}>新建子目录</Button><p title={selectedFolder?.name}>{selectedFolder ? `创建位置：${selectedFolder.name}` : '选中目录后，可在其中创建子目录'}</p></div>}
    </aside>;
}
