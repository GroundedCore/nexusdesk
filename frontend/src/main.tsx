import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';
import { EmbedChat } from './features/open-platform/EmbedChat';
import { App as AntApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import zhTW from 'antd/locale/zh_TW';
import enUS from 'antd/locale/en_US';
import hiIN from 'antd/locale/hi_IN';
import { useTranslation } from 'react-i18next';
import { locale } from './i18n';
import './styles/global.css';
import './styles/console.css';
import './styles/design-system.css';
import {consoleTheme} from './styles/theme';

function Root() { useTranslation(); return <ConfigProvider locale={{'zh-CN':zhCN,'zh-TW':zhTW,en:enUS,hi:hiIN}[locale()]} theme={consoleTheme}><AntApp>{location.pathname.startsWith('/embed/')?<EmbedChat/>:<App />}</AntApp></ConfigProvider>; }
createRoot(document.getElementById('root')!).render(<StrictMode><Root /></StrictMode>);
