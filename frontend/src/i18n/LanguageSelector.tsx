import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Dropdown } from 'antd';
import { CheckOutlined, DownOutlined, GlobalOutlined } from '@ant-design/icons';
import { changeLanguage, locale, t, type Locale } from './index';
import './language-selector.css';

const languages: { key: Locale; name: string; description: string; mark: string }[] = [
  { key: 'zh-CN', name: '简体中文', description: 'Simplified Chinese', mark: '简' },
  { key: 'zh-TW', name: '繁體中文', description: 'Traditional Chinese', mark: '繁' },
  { key: 'en', name: 'English', description: 'English', mark: 'EN' },
  { key: 'hi', name: 'हिन्दी', description: 'Hindi', mark: 'हि' },
];

export function LanguageSelector() {
  useTranslation();
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const current = languages.find(language => language.key === locale())!;
  return <Dropdown
    trigger={['click']}
    placement="bottomRight"
    autoFocus
    open={open}
    onOpenChange={setOpen}
    classNames={{ root: 'language-dropdown' }}
    menu={{
      selectable: true,
      selectedKeys: [current.key],
      'aria-label': t('界面语言'),
      items: languages.map(language => ({
        key: language.key,
        label: <span className="language-option">
          <span className="language-option-mark" aria-hidden>{language.mark}</span>
          <span className="language-option-copy"><span lang={language.key}>{language.name}</span><small lang="en">{language.description}</small></span>
          {language.key === current.key && <CheckOutlined className="language-option-check" aria-hidden />}
        </span>,
      })),
      onClick: ({ key }) => {
        changeLanguage(key as Locale);
        setOpen(false);
        button.current?.focus();
      },
    }}
    popupRender={menu => <div className="language-menu"><div className="language-menu-heading">{t('界面语言')}</div>{menu}</div>}
  >
    <Button ref={button} type="text" className={`language-switch ${open ? 'is-open' : ''}`}
      aria-label={`${t('界面语言')}：${current.name}`} aria-haspopup="menu" aria-expanded={open}
      onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true); } }}>
      <GlobalOutlined className="language-switch-globe" aria-hidden />
      <span className="language-switch-name" lang={current.key}>{current.name}</span>
      <DownOutlined className="language-switch-chevron" aria-hidden />
    </Button>
  </Dropdown>;
}
