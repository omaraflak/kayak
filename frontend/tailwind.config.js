/**
 * Material colour roles, defined as CSS variables in src/m3-tokens.css so that the
 * light and dark themes swap them in one place.
 */
const ROLES = [
  'primary', 'on-primary', 'primary-container', 'on-primary-container', 'inverse-primary',
  'secondary', 'on-secondary', 'secondary-container', 'on-secondary-container',
  'tertiary', 'on-tertiary', 'tertiary-container', 'on-tertiary-container',
  'surface', 'surface-dim', 'surface-bright', 'on-surface', 'surface-variant', 'on-surface-variant',
  'surface-container-lowest', 'surface-container-low', 'surface-container',
  'surface-container-high', 'surface-container-highest',
  'inverse-surface', 'inverse-on-surface',
  'outline', 'outline-variant', 'scrim',
  'error', 'on-error', 'error-container', 'on-error-container',
];

/**
 * A colour role usable with opacity modifiers. A bare `var()` is opaque to
 * Tailwind, so `bg-md-scrim/60` generated nothing at all; mixing with transparent
 * gives the modifier something to act on.
 */
const role = (name) =>
  `color-mix(in srgb, var(--md-sys-color-${name}) calc(<alpha-value> * 100%), transparent)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        md: Object.fromEntries(ROLES.map((name) => [name, role(name)])),
      },
      // The smallest steps of each scale, which the components are written with.
      boxShadow: {
        '2xs': '0 1px rgb(0 0 0 / 0.05)',
        xs: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
      },
      backdropBlur: { xs: '2px' },
      borderRadius: { xs: '0.125rem' },
      spacing: { 4.5: '1.125rem' },
      keyframes: {
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'scale-up': {
          from: { opacity: '0', transform: 'scale(0.96)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 150ms ease-out',
        'scale-up': 'scale-up 150ms ease-out',
        'slide-up': 'slide-up 200ms ease-out',
      },
    },
  },
  plugins: [],
};
