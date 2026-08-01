import { useEffect, useMemo, useState } from "react";
import { WIKI_CATEGORIES, WIKI_ENTRIES } from "../wikiContent";

// Backlog B12.1 -- the in-app WIKI/help tab (see wikiContent.js's module
// comment for why this replaced the author's original "GitHub wiki"
// suggestion). A deep link like #/wiki?entry=google-calendar-setup
// (used by SettingsPage's Google Calendar card) opens straight to that
// entry, scrolled into view -- everything else here is intentionally
// small: a search box filtering title/body text, entries grouped by
// category, each collapsible.

// A tiny, deliberately non-exhaustive inline markdown renderer -- just
// **bold** and `code` spans, since that's all wikiContent.js's entries
// actually use. No new dependency (no `marked`/`remark`) for a feature
// this small, consistent with this app's existing preference for a
// hand-built parser over a library where the format needed is simple
// and well-specified (see calendar_export_service.py's same reasoning
// for the .ics builder).
function MdText({ text }) {
  const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          return <code key={i}>{part.slice(1, -1)}</code>;
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function WikiBlock({ block, i }) {
  if (block.type === "steps") {
    return (
      <ol className="wiki-steps" key={i}>
        {block.items.map((item, j) => (
          <li key={j}>
            <MdText text={item} />
          </li>
        ))}
      </ol>
    );
  }
  if (block.type === "note") {
    return (
      <p className="wiki-note" key={i}>
        <MdText text={block.text} />
      </p>
    );
  }
  return (
    <p key={i}>
      <MdText text={block.text} />
    </p>
  );
}

function entryMatchesQuery(entry, query) {
  if (!query) return true;
  const q = query.toLowerCase();
  if (entry.title.toLowerCase().includes(q) || entry.category.toLowerCase().includes(q)) return true;
  return entry.body.some((b) => (b.text || (b.items || []).join(" ")).toLowerCase().includes(q));
}

export default function WikiPage() {
  const [query, setQuery] = useState("");
  const [openIds, setOpenIds] = useState(() => new Set());

  // Deep link: #/wiki?entry=<id> opens (and later, once entries.length
  // grows, would scroll to) that specific entry -- read once on mount,
  // same "parse window.location.hash's query half" approach the OAuth
  // callback banner on SettingsPage uses, since this app's HashRouter
  // puts query params after the route's own '?', not the page URL's.
  useEffect(() => {
    const hashQuery = window.location.hash.split("?")[1] || "";
    const params = new URLSearchParams(hashQuery);
    const entryId = params.get("entry");
    if (entryId) {
      setOpenIds(new Set([entryId]));
      setTimeout(() => {
        document.getElementById(`wiki-${entryId}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 50);
    } else {
      // Nothing deep-linked -- open the first entry per category by default
      // so the page isn't a wall of collapsed titles on first visit.
      const firstPerCategory = new Set();
      const seen = new Set();
      for (const e of WIKI_ENTRIES) {
        if (!seen.has(e.category)) {
          firstPerCategory.add(e.id);
          seen.add(e.category);
        }
      }
      setOpenIds(firstPerCategory);
    }
  }, []);

  function toggle(id) {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const filtered = useMemo(() => WIKI_ENTRIES.filter((e) => entryMatchesQuery(e, query)), [query]);
  const grouped = useMemo(() => {
    return WIKI_CATEGORIES.map((cat) => ({
      category: cat,
      entries: filtered.filter((e) => e.category === cat),
    })).filter((g) => g.entries.length > 0);
  }, [filtered]);

  return (
    <div>
      <div className="card">
        <h3>WIKI</h3>
        <p className="hint">
          Help and setup guides that ship with the app itself -- no internet connection or GitHub lookup needed.
        </p>
        <input
          type="text"
          className="wiki-search"
          placeholder="Search the WIKI..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {grouped.length === 0 && (
        <div className="card">
          <p className="hint">No WIKI entries match "{query}".</p>
        </div>
      )}

      {grouped.map((group) => (
        <div className="card" key={group.category}>
          <h3>{group.category}</h3>
          {group.entries.map((entry) => {
            const isOpen = openIds.has(entry.id);
            return (
              <div className="wiki-entry" id={`wiki-${entry.id}`} key={entry.id}>
                <button type="button" className="wiki-entry-toggle" onClick={() => toggle(entry.id)}>
                  <span className={`wiki-entry-caret${isOpen ? " open" : ""}`}>&#9656;</span>
                  {entry.title}
                </button>
                {isOpen && (
                  <div className="wiki-entry-body">
                    {entry.body.map((block, i) => (
                      <WikiBlock block={block} i={i} key={i} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
