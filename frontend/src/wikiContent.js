// Backlog B12.1 (2026-08-01): Chef's in-app WIKI/help tab, modeled
// directly on a validated pattern from the sibling Fiduciary project --
// data-driven help entries (there: W(id, category, title, def, read,
// use)) rendered in a searchable tab, deep-linkable from a "?" affordance
// next to whatever panel they explain. Chosen over a GitHub wiki (the
// author's first suggestion) once it turned out Fiduciary's own "wiki"
// is exactly this in-app pattern, not a GitHub-hosted one: it ships with
// `docker compose up` for every household that clones this repo, works
// offline, and never needs a separate publishing step the way editing a
// GitHub wiki page would.
//
// Each entry's `body` is a small array of typed blocks (not raw HTML --
// this file has no templating engine and no new dependency was added for
// one):
//   { type: "p", text }       -- a paragraph. `text` may contain
//                                 **bold** and `code` spans (see MdText
//                                 in WikiPage.jsx for the tiny inline
//                                 parser).
//   { type: "steps", items }  -- a numbered walkthrough.
//   { type: "note", text }    -- a callout for a warning/tip worth
//                                 visually separating from the main flow.
//
// Deliberately NOT exhaustive yet -- only what this pass actually needed
// (Getting started + the Google Calendar setup the author explicitly
// asked to land here) is populated. Backfilling entries for the rest of
// the app (inventory vision intake, recipe import, the auth gate, etc.)
// is real, reasonable future work -- see PROJECT-PLAN.md's B12 notes.

export const WIKI_CATEGORIES = ["Getting started", "Integrations", "Data"];

export const WIKI_ENTRIES = [
  {
    id: "overview",
    category: "Getting started",
    title: "What Chef is",
    body: [
      {
        type: "p",
        text:
          "A self-hosted, AI-driven meal planner, pantry/fridge inventory tracker, and recipe manager. Everything " +
          "runs in your own Docker containers against your own Ollama instance (and, optionally, your own Tavily " +
          "key for web-grounded recipe/nutrition lookups) -- nothing about your household's data leaves your own " +
          "machine unless a feature you've explicitly connected (like Google Calendar, below) needs to.",
      },
      {
        type: "p",
        text:
          "The app plans a week of meals around your household size, dietary restrictions, and what's already in " +
          "your pantry -- favoring ingredients that are about to expire or have been sitting unused -- then builds " +
          "a grocery list for whatever's left to buy.",
      },
    ],
  },
  {
    id: "settings-secrets",
    category: "Getting started",
    title: "Settings & secrets: where your API keys live",
    body: [
      {
        type: "p",
        text:
          "Every user-editable setting (Ollama connection, the Tavily key, USDA FoodData Central key, the Google " +
          "Calendar OAuth client below, your household timezone) lives in the database, not in a `.env` file -- " +
          "changes take effect on the next request, no container rebuild needed. This is all reachable from the " +
          "**Settings** page.",
      },
      {
        type: "p",
        text:
          "Anything marked a secret (API keys, OAuth client secrets, the Google refresh token) is encrypted at " +
          "rest with a key file generated on first run and stored alongside the database. Secrets are never sent " +
          "back to the browser in the clear -- the Settings page shows `********` for anything already set, and " +
          "saving only overwrites it if you type a new value.",
      },
      {
        type: "note",
        text:
          "Back up the encryption key file (`secrets.key`, in the same Docker volume as the database) along with " +
          "the rest of your data. If it's ever lost, every encrypted setting becomes permanently unreadable and " +
          "has to be re-entered from scratch.",
      },
    ],
  },
  {
    id: "google-calendar-setup",
    category: "Integrations",
    title: "Google Calendar setup: push your meal plan to a real calendar",
    body: [
      {
        type: "p",
        text:
          "Once connected, Chef automatically creates a dedicated **\"Chef Meal Plan\"** calendar in your Google " +
          "account and keeps it in sync with whatever's currently planned -- add a plan, swap a recipe, skip a " +
          "meal, and the calendar updates on its own within a few seconds (via Chef's background job queue, " +
          "visible in the badge at the top of the app). Share that one calendar with the rest of your household " +
          "from Google Calendar's own \"Settings and sharing\" screen and everyone sees the same plan on their own " +
          "phone/calendar app -- nothing extra to build or configure in Chef for that part.",
      },
      {
        type: "p",
        text:
          "**This is genuinely the most fiddly setup step in the whole app**, and that's on Google, not Chef: " +
          "there's no way for a self-hosted app like this one to avoid you registering your own OAuth client in " +
          "your own Google Cloud project -- Chef can't bundle or share one on your behalf. The good news: it's a " +
          "one-time, ~10 minute setup, it's completely free (no billing account needed), and every step below is " +
          "exact, not \"click around until you find it.\" If you'd rather skip Google entirely, the Meal Plan " +
          "page's **Calendar (.ics)** link works with zero setup in any calendar app that can subscribe to a URL " +
          "-- one-way and not shareable the same way, but instant.",
      },
      {
        type: "steps",
        items: [
          "**Work out your redirect URI first** -- you'll need it in step 6. It's " +
            "`http://<the address you use to reach Chef's backend>:<BACKEND_PORT>/api/calendar/google/callback`. " +
            "Chef auto-suggests this for you the first time you open the Google OAuth redirect URI field on the " +
            "Settings page (a **\"Use this browser's address\"** button also lets you re-fill it any time) -- it " +
            "reads BACKEND_PORT the same way the rest of the app does, so you don't need to open `.env` yourself. " +
            "Just confirm the suggested value matches whichever address you'll actually click \"Connect\" from: if " +
            "you always connect from the same machine Chef's backend runs on, `http://localhost:8095/api/calendar/google/callback` " +
            "works. If you (or anyone else in the household) will click \"Connect\" from a phone/tablet/other " +
            "computer on your LAN, it needs to be that machine's real LAN address instead, e.g. " +
            "`http://10.11.24.21:8095/api/calendar/google/callback` -- Google needs the exact address a *browser* " +
            "can reach, and a phone can't reach \"localhost\" meaning the Chef server.",
          "Go to **console.cloud.google.com** and create a new project (top-left project picker → \"New project\"). " +
            "Any name works. Billing is NOT required for any of this.",
          "In the left menu, open **APIs & Services → Library**, search for **Google Calendar API**, and click " +
            "**Enable**. This is a separate step from the OAuth setup below -- both are required.",
          "Open **APIs & Services → Google Auth Platform** (Google renamed this from \"OAuth consent screen\" in " +
            "2025-2026 -- if you see a page titled \"OAuth consent screen\" instead, that's the same thing, an " +
            "older UI). Click **Get started** and fill in the 4-step wizard: an app name (anything, e.g. \"Chef\") " +
            "and your email for **App information**; **External** for **Audience/User type** (this is the one " +
            "choice that can't be changed later without starting over -- always pick External for a personal app " +
            "like this one, even though only you'll use it); your email again for **Contact information**; then " +
            "accept and **Create**.",
          "Still in Google Auth Platform, open the **Audience** tab and, under **Test users**, click **Add users** " +
            "and add your own Google account's email (and any other household member's, up to 100). Because this " +
            "app is never \"published\" for the general public, ONLY accounts on this list can ever complete the " +
            "connect flow -- this is expected and fine for a household app, not a step to skip.",
          "Open the **Clients** tab and click **Create Client**. Application type: **Web application** (not " +
            "\"Desktop app\" or any other option). Name it anything (e.g. \"Chef\"). Under **Authorized redirect " +
            "URIs**, click **Add URI** and paste the *exact* redirect URI you worked out in step 1 -- a trailing " +
            "slash, wrong port, or `http` vs `https` mismatch here is the single most common thing that goes " +
            "wrong, and Google will reject the connection with `redirect_uri_mismatch` if it's off by even one " +
            "character. Click **Create**.",
          "A dialog shows your **Client ID** and **Client Secret**. Copy both now -- the Secret is shown exactly " +
            "once and Google will not show it again (you'd have to issue a new one if you lose it).",
          "In Chef's **Settings** page, paste the Client ID into **Google OAuth client ID**, the Client Secret " +
            "into **Google OAuth client secret**, and your step-1 redirect URI into **Google OAuth redirect URI**. " +
            "Save each. While you're there, double-check **Household timezone** (an IANA name like " +
            "`America/Chicago`) -- Google needs a real timezone so a 6pm dinner actually shows at 6pm.",
          "Click **Connect Google Calendar** in the Google Calendar card. You'll be sent to Google's consent " +
            "screen, sign in, and land on an **\"unverified app\"** warning -- this is expected (see the note " +
            "below), click **Advanced**, then **Go to \"Chef\" (unsafe)**, then **Continue** to grant Calendar " +
            "access. You'll be sent back to Chef's Settings page, now showing your connected account and the " +
            "dedicated \"Chef Meal Plan\" calendar -- and sync is turned on automatically.",
        ],
      },
      {
        type: "note",
        text:
          "**The \"Google hasn't verified this app\" warning is normal and expected -- click through it.** " +
          "Google requires a lengthy security-review process to remove that warning, which only matters for apps " +
          "used by the general public. Since only the test-user emails you explicitly added in step 5 can ever " +
          "sign in at all, there's no real audience for Google to verify this app for -- it's exactly as safe to " +
          "click through as it is for any personal script that uses your own Google account.",
      },
      {
        type: "p",
        text:
          "**Troubleshooting:** `redirect_uri_mismatch` almost always means the redirect URI saved in Chef's " +
          "Settings doesn't character-for-character match one of the \"Authorized redirect URIs\" on the OAuth " +
          "client (Google Auth Platform → Clients → your client → edit). `access_blocked`/`admin_policy_enforced` " +
          "means the signed-in Google account isn't on the Audience tab's Test users list yet. If sync ever seems " +
          "stuck or out of date, use the **Force resync** button on the Settings page -- it re-pushes every " +
          "current meal-plan entry to Google and cleans up anything stale.",
      },
    ],
  },
  {
    id: "backup-and-restore",
    category: "Data",
    title: "Backups: what's included, and how to restore one",
    body: [
      {
        type: "p",
        text:
          "The **Backup** card on the Settings page downloads a single `.tar.gz` containing everything Chef " +
          "stores: the SQLite database (a consistent point-in-time snapshot, not a raw file copy, so it's safe " +
          "to download while the app is in normal use), the encryption key and keyring that decrypt every " +
          "secret setting (Tavily/USDA/Google OAuth client secret/refresh token, etc.), the session-cookie " +
          "signing key, and any uploaded recipe images or knowledge files.",
      },
      {
        type: "note",
        text:
          "**Treat the downloaded file like a password export.** It contains both your encrypted settings and " +
          "the key that decrypts them together, in the same archive -- anyone who gets the file has everything " +
          "needed to read those secrets. Store or transmit it with the same care you'd give an exported password " +
          "vault, not like an ordinary document backup.",
      },
      {
        type: "p",
        text:
          "**There is deliberately no in-app restore button.** Restoring means overwriting a live database and " +
          "key files out from under a running app -- real, destructive, and easy to get wrong with a one-click " +
          "UI. Restoring by replacing the files in Chef's own Docker volume, with the container stopped first, " +
          "is safer and just as effective for a self-hosted single-household app.",
      },
      {
        type: "steps",
        items: [
          "From the same directory as `docker-compose.yml`, stop the stack: `docker compose down`.",
          "Find the exact name of Chef's data volume -- Docker Compose prefixes it with a project name derived " +
            "from the folder the repo was cloned into, so it usually isn't just `chef-data`. Run `docker volume " +
            "ls` and look for whatever ends in `chef-data` (e.g. `chef_chef-data`) -- confirm it rather than " +
            "guessing, since a wrong volume name silently does nothing rather than erroring loudly.",
          "Extract the backup into that volume using a throwaway container (replace `<volume-name>` with what " +
            "you found above, and run this from the directory containing the downloaded backup file): " +
            "`docker run --rm -v <volume-name>:/app/data -v \"$(pwd)\":/backup alpine sh -c \"cd /app/data && " +
            "tar xzf /backup/chef-backup-<timestamp>.tar.gz\"`.",
          "Restart the stack: `docker compose up -d`.",
        ],
      },
      {
        type: "note",
        text:
          "This extracts (overlays) the backup's files into the volume -- it overwrites `chef.db` and the other " +
          "backed-up files by name, but doesn't first erase anything else already in the volume. That's the " +
          "right behavior for restoring onto a fresh volume or recovering after a problem, but it is not a " +
          "guaranteed byte-for-byte return to exactly the state at backup time if other files have since been " +
          "added outside of what this app itself writes there.",
      },
      {
        type: "p",
        text:
          "**Recipe export** is a separate, lighter-weight option for just recipes: every recipe detail page has " +
          "an **Export recipe (JSON-LD)** button, and a full-collection export is available at " +
          "`/api/recipes/export/jsonld`. This uses the same schema.org format Chef's own URL/file importer " +
          "reads, so an exported recipe -- from this Chef install or, in principle, another one -- can be " +
          "brought back in through the normal **Import recipe** flow rather than needing a full backup restore.",
      },
    ],
  },
];
