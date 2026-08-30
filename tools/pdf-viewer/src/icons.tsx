type IconProps = {
    d: string;
    size?: number;
    title?: string;
};

export function Icon({ d, size = 16, title }: IconProps) {
    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            overflow="visible"
            aria-hidden={title ? undefined : true}
        >
            {title ? <title>{title}</title> : null}
            <path d={d} />
        </svg>
    );
}

export const ICONS = {
    pointer: 'M4 4l7 16 2.5-6.5L20 11z',
    pan: 'M18 11V6a2 2 0 0 0-4 0v5M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8a6 6 0 0 0 6 6h2a6 6 0 0 0 6-6v-3.5a2 2 0 0 0-4 0',
    prev: 'M15 18l-6-6 6-6',
    next: 'M9 18l6-6-6-6',
    minus: 'M5 12h14',
    plus: 'M12 5v14M5 12h14',
    undo: 'M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5A5.5 5.5 0 0 1 14.5 20H11',
    redo: 'M15 14l5-5-5-5M20 9H9.5A5.5 5.5 0 0 0 4 14.5 5.5 5.5 0 0 0 9.5 20H13',
    chevronDown: 'M6 9l6 6 6-6',
    highlight: 'M15 5l4 4-9.5 9.5H5.5v-4L15 5zM13.2 6.8l4 4M4 20h8',
    underline: 'M6 4v7a6 6 0 0 0 12 0V4M4 20h16',
    copy: 'M8 8h12v12H8zM4 16V4h12',
} as const;
