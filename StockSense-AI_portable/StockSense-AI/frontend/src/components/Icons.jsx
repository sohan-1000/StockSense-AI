// Small inline SVG icons (no icon library, to keep the bundle light).
const base = { fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };

export const Logo = ({ size }) => (
  <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true">
    <rect x="5" y="17" width="5.5" height="10" fill="var(--accent)" />
    <rect x="13.25" y="11" width="5.5" height="16" fill="var(--accent)" />
    <rect x="21.5" y="5" width="5.5" height="22" fill="var(--ink)" />
  </svg>
);

export const ChatIcon = () => <svg viewBox="0 0 24 24" {...base}><path d="M21 12a8 8 0 0 1-11.6 7.1L3 20l1-4.6A8 8 0 1 1 21 12Z" /></svg>;
export const ClockIcon = () => <svg viewBox="0 0 24 24" {...base}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;
export const SendIcon = () => <svg viewBox="0 0 24 24" {...base}><path d="m22 2-7 20-4-9-9-4 20-7Z" /><path d="M22 2 11 13" /></svg>;
export const UploadIcon = () => <svg viewBox="0 0 24 24" {...base}><path d="M12 16V4m0 0L7 9m5-5 5 5" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" /></svg>;
export const UpIcon = () => <svg viewBox="0 0 24 24" {...base}><path d="M7 10v11H3V10h4Zm0 0 4-8a3 3 0 0 1 3 3v4h6a2 2 0 0 1 2 2.3l-1.3 7A2 2 0 0 1 18.7 21H7" /></svg>;
export const DownIcon = () => <svg viewBox="0 0 24 24" {...base}><path d="M17 14V3h4v11h-4Zm0 0-4 8a3 3 0 0 1-3-3v-4H4a2 2 0 0 1-2-2.3l1.3-7A2 2 0 0 1 5.3 3H17" /></svg>;
export const MoonIcon = () => <svg viewBox="0 0 24 24" {...base}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></svg>;
