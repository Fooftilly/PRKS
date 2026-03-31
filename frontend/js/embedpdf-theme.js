/**
 * EmbedPDF (@embedpdf/snippet) theme: mockup-aligned violet accent, soft gray
 * viewer chrome in light mode only. Dark mode uses deep surfaces without the light gray wash.
 * @see https://www.embedpdf.com/docs/snippet/theme
 */
function getPrksEmbedPdfTheme() {
    const preference = window.localStorage.getItem('prks-theme') || 'system';
    return {
        preference,
        light: {
            accent: {
                primary: '#9333ea',
                primaryHover: '#7e22ce',
                primaryActive: '#6b21a8',
                primaryLight: '#f3e8ff',
                primaryForeground: '#ffffff',
            },
            background: {
                app: '#e8eaef',
                surface: '#ffffff',
                surfaceAlt: '#f8fafc',
                elevated: '#ffffff',
            },
            foreground: {
                primary: '#1e293b',
                secondary: '#475569',
                muted: '#64748b',
            },
            border: {
                default: '#e2e8f0',
                subtle: '#f1f5f9',
            },
            interactive: {
                hover: 'rgba(147, 51, 234, 0.08)',
                active: 'rgba(147, 51, 234, 0.14)',
                selected: 'rgba(147, 51, 234, 0.14)',
                focus: '#9333ea',
            },
        },
        dark: {
            accent: {
                primary: '#a855f7',
                primaryHover: '#9333ea',
                primaryActive: '#7e22ce',
                primaryLight: 'rgba(168, 85, 247, 0.2)',
                primaryForeground: '#ffffff',
            },
            background: {
                app: '#131d2b',
                surface: '#1f2937',
                surfaceAlt: '#17212e',
                elevated: '#273449',
            },
            foreground: {
                primary: '#f9fafb',
                secondary: '#94a3b8',
                muted: '#64748b',
            },
            border: {
                default: '#2b3748',
                subtle: '#1f2937',
            },
            interactive: {
                hover: 'rgba(168, 85, 247, 0.12)',
                active: 'rgba(168, 85, 247, 0.2)',
                selected: 'rgba(168, 85, 247, 0.18)',
                focus: '#a855f7',
            },
        },
    };
}

window.getPrksEmbedPdfTheme = getPrksEmbedPdfTheme;
