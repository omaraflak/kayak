import { ComponentPropsWithoutRef } from 'react';

/**
 * Props react-markdown hands to a replacement element.
 *
 * It calls these with the intrinsic props of the tag being replaced, so each one is
 * typed as exactly that tag. Typing them as `any` meant a typo in a forwarded prop --
 * or a renamed one -- compiled happily and only showed up as markdown rendering wrongly.
 */
export type ElementProps<Tag extends keyof JSX.IntrinsicElements> =
  ComponentPropsWithoutRef<Tag>;

/** Fenced and inline code share a renderer; only fenced code carries a language. */
export type CodeProps = ElementProps<'code'> & { inline?: boolean };
