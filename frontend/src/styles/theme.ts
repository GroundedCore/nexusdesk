import type {ThemeConfig} from 'antd';

/** One management-console theme, derived from the model gateway. */
export const consoleTheme: ThemeConfig = {
  token: {
    colorPrimary:'#6562ff', colorInfo:'#6562ff', colorLink:'#6562ff',
    colorText:'#252936', colorTextSecondary:'#747b8d', colorBgLayout:'#ffffff',
    colorBorder:'#e7e9f1', colorBorderSecondary:'#eef0f5',
    borderRadius:6, controlHeight:36, fontSize:13,
    fontFamily:'Inter, "Microsoft YaHei", "Nirmala UI", "Noto Sans Devanagari", sans-serif',
    boxShadow:'none', boxShadowSecondary:'0 8px 28px rgba(35,40,70,.08)',
  },
  components:{
    Button:{borderRadius:6,primaryShadow:'none',fontWeight:500},
    Card:{borderRadiusLG:8,headerFontSize:15,headerHeight:52,bodyPadding:24},
    Table:{headerBg:'#f8f9fc',headerColor:'#656d7b',rowHoverBg:'#fafaff',cellPaddingBlock:16},
    Input:{activeBorderColor:'#8086fb',activeShadow:'0 0 0 2px rgba(101,98,255,.08)'},
    Select:{activeBorderColor:'#8086fb',optionSelectedBg:'#efefff'},
    Tabs:{inkBarColor:'#6562ff',itemSelectedColor:'#6562ff',horizontalItemGutter:26},
    Modal:{borderRadiusLG:10}, Drawer:{footerPaddingBlock:16,footerPaddingInline:24},
    Pagination:{itemActiveBg:'#efefff'},
  },
};
