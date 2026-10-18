import type {ReactNode} from 'react';
import './brand-mark.css';

/**
 * Orbit glyph mirroring the login artwork (public/images/login-orbit.png):
 * two crossed orbital rings around a glowing core, plus a satellite node.
 * Kept as inline SVG so it stays crisp at any size, themable via the
 * surrounding `.brand-mark` CSS variables, and costs no extra request.
 */
export function BrandGlyph({className}:{className?:string}) {
  return <svg className={['brand-glyph',className].filter(Boolean).join(' ')} viewBox="0 0 52 52" fill="none" aria-hidden="true" focusable="false">
    <defs>
      <linearGradient id="brand-glyph-ring" x1="4" y1="6" x2="48" y2="46" gradientUnits="userSpaceOnUse">
        <stop offset="0" stopColor="#b9b7ff"/><stop offset="1" stopColor="#5b57f0"/>
      </linearGradient>
      <radialGradient id="brand-glyph-core" cx="0.35" cy="0.28" r="0.85">
        <stop offset="0" stopColor="#ffffff"/><stop offset="0.42" stopColor="#a9a6ff"/><stop offset="1" stopColor="#5b57f0"/>
      </radialGradient>
    </defs>
    <ellipse cx="26" cy="26" rx="22" ry="9.5" transform="rotate(-28 26 26)" stroke="url(#brand-glyph-ring)" strokeWidth="2.4"/>
    <ellipse cx="26" cy="26" rx="22" ry="9.5" transform="rotate(62 26 26)" stroke="url(#brand-glyph-ring)" strokeWidth="2.2" opacity=".8"/>
    <circle cx="26" cy="26" r="7" fill="url(#brand-glyph-core)"/>
    <circle cx="45.5" cy="16.5" r="2.7" fill="#c3c1ff"/>
  </svg>;
}

/**
 * Shared "nexusdesk" wordmark. The whole word carries one weight and a single
 * gradient (white -> #c3c1ff -> #8f8bff on the dark login panel), closing with a
 * small gradient dot. Colours are inherited from CSS variables so the same
 * markup also works on light surfaces such as the console sidebar, which swaps
 * in a darker gradient.
 */
export function BrandMark({size='md',subtitle,showGlyph=true,showDot=true,className}:{size?:'md'|'lg';subtitle?:ReactNode;showGlyph?:boolean;showDot?:boolean;className?:string}) {
  return <span className={['brand-mark','brand-mark--'+size,className].filter(Boolean).join(' ')}>
    {showGlyph&&<BrandGlyph/>}
    <span className="brand-mark__body">
      <span className="brand-mark__word"><span className="brand-mark__name">nexusdesk</span>{showDot&&<i className="brand-mark__dot" aria-hidden="true"/>}</span>
      {subtitle&&<small className="brand-mark__subtitle">{subtitle}</small>}
    </span>
  </span>;
}
