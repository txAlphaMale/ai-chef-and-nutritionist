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

export const WIKI_CATEGORIES = ["Getting started", "Integrations", "Data", "Security"];

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
          "**Work out your redirect URI first** -- you'll need it in step 6. **Read the note right after this " +
            "list before picking one** -- Google rejects a plain LAN IP address here (the obvious first choice " +
            "for a self-hosted app), so the right value depends on how you'll click \"Connect.\" Chef's Settings " +
            "page auto-suggests a working value for you (the **\"Use localhost\"** / **\"Use this browser's " +
            "address\"** buttons next to the field) -- the note below explains why it picks the one it does.",
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
          "**Why a plain LAN address (like `http://10.11.24.21:8095/...`) doesn't work here, corrected " +
          "2026-08-01.** An earlier version of this guide suggested using the LAN address you normally reach " +
          "Chef at directly -- that's wrong, and Google Cloud Console will reject it with \"Invalid Redirect: " +
          "must end with a public top-level domain\" / \"must use a domain that is a valid top private domain.\" " +
          "This is Google's own OAuth redirect URI policy, not a Chef limitation: per Google's documented " +
          "validation rules, a redirect URI's host **cannot be a raw IP address** (with one exception, below), " +
          "and non-localhost URIs must use HTTPS, which this LAN-only app deliberately doesn't set up (see the " +
          "known plain-HTTP simplification elsewhere in this WIKI/PROJECT-PLAN). Two real ways forward:",
      },
      {
        type: "steps",
        items: [
          "**Loopback (`http://localhost:<port>` or `http://127.0.0.1:<port>`) -- Chef's default suggestion, " +
            "zero extra setup.** Google explicitly exempts localhost/127.0.0.1 addresses from BOTH restrictions " +
            "above (any port, plain HTTP, no certificate) -- it's a documented carve-out, not a workaround. The " +
            "catch: the OAuth \"Connect\" click has to happen from a browser that reaches Chef's backend AS " +
            "`localhost`, which in practice means either sitting at the server machine itself for that one click, " +
            "or opening an SSH tunnel from another device first (e.g. `ssh -L 8095:localhost:8095 " +
            "user@your-server` from a laptop, then browse to `http://localhost:8095` on THAT laptop and click " +
            "Connect there). This only matters for the one-time connect step -- once connected, ongoing calendar " +
            "sync runs entirely on the backend with no browser involved at all, from any device, same as before.",
          "**A public-DNS-to-LAN-IP hostname (e.g. sslip.io/nip.io) -- lets ANY device on the LAN click Connect, " +
            "no server-machine/tunnel needed.** Services like `sslip.io` and `nip.io` publish real, public DNS " +
            "records that embed an IP address in the hostname itself and resolve back to it -- for example " +
            "`http://chef.10-11-24-21.sslip.io:8095/api/calendar/google/callback` is a real domain name (passes " +
            "Google's check) that any device's normal DNS resolves straight back to `10.11.24.21` -- the actual " +
            "HTTP connection still goes directly over your LAN, never through sslip.io itself; only the one-time " +
            "DNS lookup touches the public internet. That lookup is not a new requirement in practice: the same " +
            "browser has to reach `accounts.google.com` to complete the consent screen anyway, so if it can do " +
            "that, it can already resolve a public DNS name too. Trade-off: it depends on a free third-party " +
            "DNS service staying up, which loopback doesn't. To use this, register the sslip.io-style URL as " +
            "the \"Authorized redirect URI\" in step 6 below instead of localhost, and type that same value into " +
            "Chef's **Google OAuth redirect URI** field (the auto-suggest buttons only offer localhost or this " +
            "browser's raw address, so this path needs a manual paste).",
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
    id: "recipe-folder-import",
    category: "Integrations",
    title: "Recipe folder import: batch-import from a synced folder",
    body: [
      {
        type: "p",
        text:
          "Backlog B13.1 -- if you've already got a folder of recipes (text files, PDFs, saved webpages, or " +
          "schema.org JSON exports from another app), Chef can scan the whole folder in one go instead of " +
          "uploading each file one at a time from the Recipes page's normal import card. This works with any " +
          "folder that syncs to this server as a normal directory -- **OneDrive, Dropbox, Google Drive desktop, " +
          "or just a plain folder you copy files into** -- Chef never talks to any of those services' cloud APIs; " +
          "it only ever reads whatever files are sitting in the folder at scan time.",
      },
      {
        type: "steps",
        items: [
          "Make sure the folder is actually present on **this server's own filesystem** -- if it's a OneDrive/" +
            "Dropbox/Google Drive folder, install that service's normal desktop sync client on the server (or " +
            "wherever Docker runs) so the folder appears as a real local directory there, the same way it would " +
            "on your own PC.",
          "Open **docker-compose.yml** and find the commented-out volume line under the `backend` service " +
            "(`# - /path/on/this/host/to/your/recipes:/app/data/recipe_import:ro`). Uncomment it and replace the " +
            "left side with the real path to your synced folder on this host.",
          "In **.env**, set `RECIPE_IMPORT_FOLDER_PATH=/app/data/recipe_import` (matching the right side of the " +
            "line above) -- or skip this and just paste the same value into Chef's **Settings > Integrations > " +
            "\"Recipe import folder path\"** field after the container's running; either way ends up at the same " +
            "setting.",
          "Run `docker compose up -d --build` to pick up the new volume mount (a plain restart isn't enough -- " +
            "volume changes need a recreate).",
          "On the **Recipes** page, click **Import from folder**, then **Scan folder**. This can take a few " +
            "minutes for a large collection -- one file means one background parse, the same as a single manual " +
            "upload, just looped. Check the Connection status card on Settings, or the jobs badge, for progress.",
          "Review the results table: each row shows the file, its parsed title (editable), how many ingredients " +
            "were found, and whether it parsed cleanly. Uncheck anything you don't want, then click **Add N " +
            "recipe(s)**. Nothing is saved to your recipe collection until this step.",
        ],
      },
      {
        type: "note",
        text:
          "**Files are only ever read, never modified or deleted.** Chef treats this as your own source folder, " +
          "not a working directory it owns -- rescanning the same folder later just re-parses whatever's there " +
          "again (including files already imported once), so it's normal to see the same file show up again on a " +
          "second scan; just leave it unchecked if you don't want a duplicate.",
      },
      {
        type: "p",
        text:
          "**Supported file types:** `.txt`, `.md`, `.pdf`, `.json`/`.jsonld` (schema.org Recipe data, parsed " +
          "directly without using Ollama at all -- the most reliable path if your source app can export in that " +
          "format), and `.html`/`.htm` (saved web pages, also checked for embedded schema.org data first). Plain " +
          "photo files aren't included in a folder scan -- that would mean running the much slower vision model " +
          "over potentially dozens of images at once; import a photo one at a time from the Recipes page instead. " +
          "A single scan is capped at 300 files and skips anything over 5 MB, to keep one click from turning into " +
          "an unbounded job -- narrow the folder (e.g. by cuisine or subfolder) if you hit that cap.",
      },
    ],
  },
  {
    id: "https-setup",
    category: "Security",
    title: "HTTPS / secure context: fixing the camera and location warnings",
    body: [
      {
        type: "p",
        text:
          "**Why this exists:** the barcode scanner's camera and the Dining Out page's \"use my location\" button " +
          "both rely on browser APIs (`getUserMedia`, `navigator.geolocation`) that most browsers only allow on a " +
          "**secure context** -- a page loaded over HTTPS, or from `localhost` specifically. Chef has served plain " +
          "HTTP over your LAN IP since it was first set up, which is exactly what those two features can't work " +
          "over. Nothing else in the app needs this -- meal planning, inventory, recipes, chat, and everything " +
          "else work fine over plain HTTP.",
      },
      {
        type: "p",
        text:
          "**A self-signed certificate is the right fix for a LAN-only household setup like this one** -- it's " +
          "free, works entirely offline, and takes under a minute from the **Settings > Security** tab. The " +
          "trade-off: since it isn't signed by a public Certificate Authority, every browser that connects shows " +
          "a one-time \"your connection isn't private\" warning that has to be clicked through -- expected and " +
          "safe on your own private network, not a sign anything is wrong.",
      },
      {
        type: "steps",
        items: [
          "Open **Settings > Security** and, under **Certificate (HTTPS)**, check the **Hostnames / IP " +
            "addresses to cover** field -- it's pre-filled with the address this browser is already using to " +
            "reach Chef. Add any OTHER address you also use (e.g. both a LAN IP and a `.local` hostname, or " +
            "`localhost` if you sometimes browse from the server itself) -- separated by commas or spaces. A " +
            "browser rejects the certificate on any address not listed here, even if it's otherwise valid.",
          "Click **Generate self-signed certificate**. The backend restarts itself in place within a couple of " +
            "seconds to start serving HTTPS; the frontend container notices the same shared certificate " +
            "independently and switches over shortly after (it polls every few seconds, no action needed).",
          "**This is the step almost everyone misses:** visit and click through the browser warning at BOTH " +
            "addresses separately, not just one. Chef's frontend (the page you're using) and its backend API run " +
            "as two different origins/ports -- accepting the warning on one does NOT also trust the other, and " +
            "the background API calls this page makes fail silently as generic network errors if only the " +
            "frontend is trusted, which looks exactly like a stuck \"Loading...\" screen with no obvious cause.",
          "Visit `https://<the address you chose>:5174` (the frontend's default HTTPS port -- see the note below " +
            "if you changed `FRONTEND_HTTPS_PORT` in `.env`) and click through the warning (**Advanced > Proceed** " +
            "in Chrome; **Advanced > Accept the Risk and Continue** in Firefox; similar wording elsewhere).",
          "Then separately visit `https://<the same address>:8446` (the backend's default HTTPS port -- " +
            "`BACKEND_HTTPS_PORT` in `.env` if changed) and click through the SAME warning there too. You'll " +
            "land on a bare JSON response (`{\"status\":\"ok\"}` or similar) -- that's expected, this address is " +
            "the API, not a page meant to be browsed. Trusting it is all that matters.",
          "Reload the frontend address from step 4 (not a hard requirement, but clears up any page that loaded " +
            "mid-transition). The camera and location features now work. The old plain-HTTP address on port " +
            "5173 now auto-redirects to the HTTPS one -- an old bookmark or browser history entry still lands " +
            "you in the right place, no manual re-typing needed.",
        ],
      },
      {
        type: "note",
        text:
          "**Every device that connects needs to repeat the \"visit both addresses\" step once**, not just the " +
          "device used to generate the certificate -- a self-signed certificate has no automatic way to tell a " +
          "phone or tablet's browser to trust it. This is a one-time step per device, not per visit.",
      },
      {
        type: "p",
        text:
          "**Certificate expiry:** self-signed certificates generated here are valid for about 2.25 years. The " +
          "Settings page shows the exact expiry date and a days-remaining count -- when it's getting close, " +
          "generate a new one the same way (it replaces the old one and every device just needs to click through " +
          "the trust warning once more).",
      },
      {
        type: "p",
        text:
          "**Advanced: certificates from your own Certificate Authority.** If your household already runs an " +
          "internal CA (or you'd rather avoid the per-device trust-warning step by installing your CA's root " +
          "certificate on each device once, which makes every cert it issues automatically trusted with no " +
          "warning), the same Settings card has a **Generate CSR** flow under \"Advanced\": Chef generates a " +
          "private key (which never leaves the server) and a Certificate Signing Request you submit to your CA, " +
          "then paste the signed certificate back in to install it.",
      },
      {
        type: "p",
        text:
          "**Reverting to plain HTTP:** click **Remove certificate** on the Settings page. Both containers " +
          "revert to plain HTTP within a few seconds -- camera and location features stop working again, exactly " +
          "as before any of this was set up.",
      },
      {
        type: "note",
        text:
          "**Troubleshooting a stuck \"Loading...\" page after generating a certificate:** this almost always " +
          "means the backend address (port 8446 by default) hasn't been trusted yet -- see step 5 above. Open " +
          "your browser's developer console (F12) and check the Network tab for a request to port 8446 failing " +
          "with something like `ERR_CERT_AUTHORITY_INVALID` -- visiting that exact address directly and clicking " +
          "through the warning resolves it immediately, no restart needed.",
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
