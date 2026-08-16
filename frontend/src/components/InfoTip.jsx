import { useEffect, useId, useRef, useState } from "react";

/** A small "?" next to a control, explaining it in one or two sentences
 * without sending the household to the WIKI for something that fits in a
 * sentence.
 *
 * Capstone review 2026-08-16. The app had five native `title=""` attributes
 * and nothing else. `title` is the wrong tool here for a specific reason:
 * it only appears on mouse HOVER, and the two devices this app was built
 * for -- the phone at the store and the iPad on the counter -- have no
 * hover. Every explanation in the app was therefore invisible on exactly
 * the hardware the feature exists for. This is click/tap-driven, so it
 * behaves identically on both.
 *
 * Accessibility (continuing B7.4's pass): the trigger is a real `<button>`
 * so it is reachable and activatable from the keyboard, it carries
 * `aria-expanded` and an `aria-label` naming what it explains, and the
 * bubble is `role="note"` referenced by `aria-describedby`. Escape closes
 * it and returns focus to the trigger, and an outside click dismisses it.
 *
 * Where a topic genuinely needs more than a sentence, pass `wikiEntry` --
 * the bubble then ends with a deep link into that WIKI entry, which is the
 * same `#/wiki?entry=<id>` convention SettingsPage already uses. The
 * tooltip is the short answer, the WIKI is the long one; a tooltip that
 * needs three paragraphs is a WIKI entry that has not been written yet.
 */
export default function InfoTip({ label, children, wikiEntry, wikiLabel = "Read more in the WIKI" }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const buttonRef = useRef(null);
  const id = useId();

  useEffect(() => {
    if (!open) return undefined;

    function onPointerDown(event) {
      if (!wrapRef.current?.contains(event.target)) setOpen(false);
    }
    function onKeyDown(event) {
      if (event.key === "Escape") {
        setOpen(false);
        // Returning focus matters for keyboard users: without it, focus is
        // left on a node that just stopped existing and the tab order
        // restarts from the top of the document.
        buttonRef.current?.focus();
      }
    }

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <span className="infotip" ref={wrapRef}>
      <button
        type="button"
        ref={buttonRef}
        className="infotip-trigger"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={label ? `What is ${label}?` : "More information"}
        onClick={() => setOpen((v) => !v)}
      >
        ?
      </button>
      {open && (
        <span className="infotip-bubble" id={id} role="note">
          {label && <strong className="infotip-title">{label}</strong>}
          <span className="infotip-body">{children}</span>
          {wikiEntry && (
            <a className="infotip-link" href={`#/wiki?entry=${wikiEntry}`} onClick={() => setOpen(false)}>
              {wikiLabel} &rarr;
            </a>
          )}
        </span>
      )}
    </span>
  );
}
