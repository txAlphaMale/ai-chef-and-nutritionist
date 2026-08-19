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
// Backfilled across the whole app in the capstone review (2026-08-16).
// It started as six entries covering Getting started plus the Google
// Calendar setup, with everything else -- inventory intake, recipe
// import, the auth gate, the whole health and planning half of the app --
// listed as future work. That gap mattered more than it looked: this
// WIKI is the ONLY documentation that ships with a `docker compose up`,
// so anything not written here is undocumented for every household that
// clones the repo.
//
// Two rules held while writing these, both of which a future entry
// should keep. First, describe what the code actually does -- several
// entries below name specific defaults, limits and file paths, and every
// one of those was read out of the source rather than remembered.
// Second, never state a safety fact the app itself refuses to state:
// Chef will not assert that a dish is free of an allergen (see the
// "Restrictions, cross-contact, and what Chef will not claim" entry),
// and its documentation must not do it on Chef's behalf.

export const WIKI_CATEGORIES = [
  "Getting started",
  "Kitchen inventory",
  "Recipes",
  "Meal planning",
  "Health & nutrition",
  "AI & the Chef",
  "Integrations",
  "Data",
  "Security",
];

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
          "**The redirect URI must be the address your browser actually reaches Chef at.** Google sends the " +
          "browser back there after consent, so it has to be an address that browser can resolve -- Chef's own " +
          "`APP_PORT`/`APP_HTTPS_PORT`, whatever YOU have those set to, not necessarily the `5173`/`5174` " +
          "defaults. Two places have to agree: **Google OAuth redirect URI** in Chef's Settings (use the " +
          "auto-suggest buttons below the field, which read your browser's actual current address rather than " +
          "assuming a default) and the matching \"Authorized redirect URI\" on your OAuth client in Google Cloud " +
          "Console (Google Auth Platform → Clients → your client → edit). If they drift apart, reconnecting " +
          "fails; the Settings page shows a warning banner on this card when it detects the saved value doesn't " +
          "match the current page's address.",
      },
      {
        type: "note",
        text:
          "**Why a plain LAN address (like `http://10.11.24.21:<port>/...`) doesn't work here.** Google Cloud " +
          "Console rejects it with \"Invalid Redirect: must end with a public top-level domain\" / \"must use a " +
          "domain that is a valid top private domain.\" This is Google's own OAuth redirect URI policy, not a " +
          "Chef limitation: per Google's documented validation rules, a redirect URI's host **cannot be a raw IP " +
          "address** (with one exception, below), and non-localhost URIs must use HTTPS. Chef can serve HTTPS " +
          "(see the HTTPS entry in this WIKI), but a self-signed certificate does not satisfy Google here -- the " +
          "host still has to be a real domain name rather than an IP. Two real ways forward:",
      },
      {
        type: "steps",
        items: [
          "**Loopback (`http://localhost:<port>` or `http://127.0.0.1:<port>`) -- Chef's default suggestion, " +
            "zero extra setup.** Google explicitly exempts localhost/127.0.0.1 addresses from BOTH restrictions " +
            "above (any port, plain HTTP, no certificate) -- it's a documented carve-out, not a workaround. The " +
            "catch: the OAuth \"Connect\" click has to happen from a browser that reaches Chef as `localhost`, " +
            "since that's the address Google redirects back to. **Use whatever your own `APP_PORT` (or " +
            "`APP_HTTPS_PORT`, if you've set up HTTPS) is actually set to in your " +
            "`.env`** -- the examples here show `5173`, Chef's own default, only as a placeholder; if you changed " +
            "it (e.g. because 5173 conflicted with something else on your machine), use your real value " +
            "everywhere below instead, including in the redirect URI itself. In practice this means either " +
            "sitting at the server machine itself for the one-time Connect click, or opening an SSH tunnel from " +
            "another device first (e.g. `ssh -L <your-port>:localhost:<your-port> user@your-server` from a " +
            "laptop, then browse to `http://localhost:<your-port>` on THAT laptop and click Connect there). This " +
            "only matters for the one-time connect step -- once connected, ongoing calendar sync runs entirely on " +
            "the backend with no browser involved at all, from any device, same as before.",
          "**A public-DNS-to-LAN-IP hostname (e.g. sslip.io/nip.io) -- lets ANY device on the LAN click Connect, " +
            "no server-machine/tunnel needed.** Services like `sslip.io` and `nip.io` publish real, public DNS " +
            "records that embed an IP address in the hostname itself and resolve back to it -- for example " +
            "`http://chef.10-11-24-21.sslip.io:<your-app-port>/api/calendar/google/callback` is a real " +
            "domain name (passes Google's check) that any device's normal DNS resolves straight back to " +
            "`10.11.24.21` -- the actual HTTP connection still goes directly over your LAN, never through " +
            "sslip.io itself; only the one-time DNS lookup touches the public internet. That lookup is not a new " +
            "requirement in practice: the same browser has to reach `accounts.google.com` to complete the consent " +
            "screen anyway, so if it can do that, it can already resolve a public DNS name too. Trade-off: it " +
            "depends on a free third-party DNS service staying up, which loopback doesn't. To use this, register " +
            "the sslip.io-style URL as the \"Authorized redirect URI\" in step 6 below instead of localhost -- " +
            "using YOUR actual port (`APP_PORT` in your `.env`), not necessarily 5173 -- and type " +
            "that same value into Chef's **Google OAuth redirect URI** field (the auto-suggest buttons only offer " +
            "localhost or this browser's raw address, so this path needs a manual paste).",
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
        type: "p",
        text:
          "**Only ONE address needs trusting.** Chef serves the whole app -- pages and API alike -- from a " +
          "single address, so there is exactly one certificate warning to accept, on any device.",
      },
      {
        type: "steps",
        items: [
          "Open **Settings > Security** and, under **Certificate (HTTPS)**, check the **Hostnames / IP " +
            "addresses to cover** field -- it's pre-filled with the address this browser is already using to " +
            "reach Chef. Add any OTHER address you also use (e.g. both a LAN IP and a `.local` hostname, or " +
            "`localhost` if you sometimes browse from the server itself) -- separated by commas or spaces. A " +
            "browser rejects the certificate on any address not listed here, even if it's otherwise valid.",
          "Click **Generate self-signed certificate**. Chef restarts itself in place within a couple of " +
            "seconds and comes back serving HTTPS -- no separate command, and no second service to wait on.",
          "Visit `https://<the address you chose>:5174` (Chef's default HTTPS port -- see the note below " +
            "if you changed `APP_HTTPS_PORT` in `.env`) and click through the warning (**Advanced > Proceed** " +
            "in Chrome; **Advanced > Accept the Risk and Continue** in Firefox; similar wording elsewhere).",
          "Reload the page (not a hard requirement, but clears up anything that loaded mid-transition). The " +
            "camera and location features now work. The old plain-HTTP address on port 5173 now auto-redirects " +
            "to the HTTPS one -- an old bookmark or browser history entry still lands you in the right place, no " +
            "manual re-typing needed.",
        ],
      },
      {
        type: "note",
        text:
          "**Every device that connects needs to accept this warning once**, not just the device used to " +
          "generate the certificate -- a self-signed certificate has no automatic way to tell a phone or tablet's " +
          "browser to trust it. This is a one-time step per device, not per visit. (If you're hitting the " +
          "backend's own port directly for scripting/debugging -- see the README's note on that being optional -- " +
          "that's a separate origin and would need its own warning accepted too, but normal app use never touches " +
          "it.)",
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
          "**iOS/iPadOS: when the per-origin click-through isn't enough.** Author-reported 2026-08-03, from a " +
          "real iPad: Safari sometimes doesn't offer a \"visit this website anyway\" option at all for a " +
          "self-signed certificate, and even when it does, that per-tab trust doesn't extend to a Chef PWA " +
          "installed to the home screen (see the PWA entry) -- a standalone installed app has no browser chrome " +
          "to show that warning in, so it needs the device to already trust the certificate before it's ever " +
          "opened. If you've gone looking for that trust toggle yourself and found **Settings > General > About > " +
          "Certificate Trust Settings** completely empty -- no toggle, just a version number -- that's expected: " +
          "that screen only ever shows anything once a certificate has actually been installed as a system " +
          "profile, which the per-tab click-through never does. It's not broken; there was just nothing to " +
          "install yet.",
      },
      {
        type: "steps",
        items: [
          "On the iPad or iPhone itself, open **Settings > Security** in Chef and, under **Certificate " +
            "(HTTPS)**, tap **Download certificate for iOS/iPadOS**.",
          "Safari will ask whether to allow a configuration profile download -- allow it. It downloads " +
            "immediately but does nothing on its own yet; nothing is trusted at this point.",
          "Open the **Settings** app. A **Profile Downloaded** banner appears near the top (under your Apple " +
            "ID row) -- tap it, then tap **Install** in the top-right corner, enter your device passcode, and " +
            "tap **Install** twice more to confirm (once on a warning screen that the profile is unsigned, which " +
            "a self-signed certificate for your own LAN always is).",
          "This step alone is NOT enough -- go to **Settings > General > About > Certificate Trust Settings** " +
            "(near the bottom of the About page). You should now see the Chef certificate listed with a toggle, " +
            "off by default. Turn it **on**. This is the toggle that was missing before -- it only appears for a " +
            "certificate that's been installed as a profile, which step 3 just did.",
          "Reload Chef. The warning should be gone in Safari, and a home-screen-installed Chef PWA will now work " +
            "with the camera and location features too.",
        ],
      },
      {
        type: "note",
        text:
          "This profile-install path and the per-origin \"click through the warning\" path both work and can be " +
          "used together or separately -- installing the profile on one device doesn't affect any other device, " +
          "same as the click-through approach. If you ever remove or replace the certificate (Settings > " +
          "Security > **Remove certificate**, or generating a new one), the old installed profile on each iOS " +
          "device stops matching and should be removed too, from **Settings > General > VPN & Device " +
          "Management**, then reinstalled against the new certificate the same way.",
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
          "**Reverting to plain HTTP:** click **Remove certificate** on the Settings page. Chef reverts to " +
          "plain HTTP within a few seconds -- camera and location features stop working again, exactly " +
          "as before any of this was set up.",
      },
      {
        type: "note",
        text:
          "**Troubleshooting a stuck \"Loading...\" page after generating a certificate:** the API is served " +
          "from the same address as the page, so there is no second certificate for the browser to be missing " +
          "and no internal hop that can fail on its own. If you're still stuck after accepting the certificate " +
          "warning, open your browser's developer console (F12) and check the Network tab. Requests to " +
          "`/api/...` or `/health` failing to connect at all means the container isn't up or isn't listening on " +
          "the port you're using -- check `docker compose ps` (a crash loop reports `unhealthy` rather than " +
          "`running`) and `docker compose logs chef` for why, and confirm the port in your URL matches " +
          "`APP_HTTPS_PORT` in `.env`.",
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
  // --- Getting started ---------------------------------------------------
  {
    id: "first-run-checklist",
    category: "Getting started",
    title: "New here? A first-run checklist",
    body: [
      {
        type: "p",
        text:
          "Chef works with nothing configured, but four settings unlock most of it. Work down this list in " +
          "order -- each step is useful on its own, so you can stop anywhere and come back.",
      },
      {
        type: "steps",
        items: [
          "**Point Chef at Ollama.** Settings > AI & Models > Ollama base URL. The default " +
            "`http://host.docker.internal:11434` reaches an Ollama running on the same machine as the container. " +
            "The Connection status strip at the top of Settings turns green when it can reach it.",
          "**Tell Chef about your household.** Health > Household: how many people, dietary restrictions, and " +
            "any goals. This is the single biggest influence on what gets planned.",
          "**Turn on the knowledge files you want.** Health > Knowledge files. Chef ships with a small " +
            "public-domain nutrition reference set, all switched OFF by default so nothing is grounding your " +
            "meal plans without you choosing it.",
          "**Put something in the pantry.** Inventory > Add item, or scan a barcode, or photograph a receipt. " +
            "Meal-plan generation prefers what you already have and what is closest to expiring, so an empty " +
            "inventory means it is planning blind.",
        ],
      },
      {
        type: "note",
        text:
          "Optional but worth doing early: a **Tavily** key (Settings > AI & Models) for web-grounded lookups, " +
          "a **USDA FoodData Central** key for real nutrition numbers instead of AI estimates, and **HTTPS** " +
          "(Settings > Security) -- the camera and location features do not work over plain HTTP on a LAN address.",
      },
    ],
  },
  {
    id: "themes",
    category: "Getting started",
    title: "Themes and appearance",
    body: [
      {
        type: "p",
        text:
          "Settings > Appearance switches the whole app between bundled themes. The choice is stored in the " +
          "database, not just the browser, so it follows you to another device and survives a container rebuild.",
      },
      {
        type: "p",
        text:
          "Every colour, spacing value and font size in the app comes from one stylesheet (`theme.css`) and one " +
          "set of CSS variables. That is deliberate: it means a theme change is a variable swap rather than a " +
          "hunt through components, and it is why the app looks consistent across pages that were built months apart.",
      },
    ],
  },
  {
    id: "background-jobs",
    category: "Getting started",
    title: "Background jobs: why some things queue",
    body: [
      {
        type: "p",
        text:
          "Anything that has to ask the AI -- importing a recipe, reading a receipt photo, generating a meal " +
          "plan, indexing a knowledge file -- runs as a background **job** rather than blocking the page. A " +
          "badge appears at the top of the app showing what is running and how long it has been going, and it " +
          "stays visible while you move around the app.",
      },
      {
        type: "p",
        text:
          "Jobs run **one at a time, in order**. If you start a recipe import while a meal plan is generating, " +
          "the import waits its turn rather than fighting for the same model. The elapsed time is shown against " +
          "a typical duration learned from your own past runs, so \"8:04 of ~41:52 typical\" means this kind of " +
          "job has historically taken about forty minutes on your hardware.",
      },
      {
        type: "note",
        text:
          "You can navigate away, switch tabs, or reload the page without losing a job -- progress is tracked " +
          "server-side and the page picks it back up. Closing the browser entirely does not cancel it either.",
      },
    ],
  },

  // --- Kitchen inventory --------------------------------------------------
  {
    id: "inventory-intake",
    category: "Kitchen inventory",
    title: "Five ways to add food to the inventory",
    body: [
      {
        type: "p",
        text:
          "Inventory quality decides whether everything else works, so there are five ways in. All of them " +
          "except manual entry show you a preview to review and edit BEFORE anything is saved.",
      },
      {
        type: "steps",
        items: [
          "**Add item** -- type it in. The only path with no AI involved, and the one to fall back on when " +
            "something is misread.",
          "**Scan barcode** -- uses the device camera to read a UPC/EAN and looks it up in Open Food Facts. " +
            "Best for packaged goods. There is a manual barcode-number box for worn labels or when no camera " +
            "is available. Requires HTTPS (see the Security section).",
          "**Add from photo** -- photograph the open fridge or a counter full of groceries and the vision model " +
            "lists what it sees, with quantities where it can tell.",
          "**Import receipt/list** -- a photo or PDF of a receipt, or just pasted text of what you bought. " +
            "Captures prices too, which is what makes cost-per-serving work.",
          "**Import order history** -- a CSV or XLSX export of an online grocery order, mapped through a " +
            "per-retailer column profile. Carries prices and purchase dates.",
        ],
      },
      {
        type: "note",
        text:
          "Open Food Facts is crowd-sourced, so store-brand and local items often are not in it. A barcode " +
          "that comes back empty is normal, not a fault -- add it by hand and it works the same afterwards.",
      },
      {
        type: "p",
        text:
          "**Adding a lot at once?** Two things are built for that. The manual form has a **Save & add " +
          "another** button that keeps the form open, clears the item but carries the category, location and " +
          "unit over, and puts the cursor back in the Name field -- so a shelf of pantry items is type, Enter, " +
          "type, Enter. And the barcode scanner **stays running between items**: scan, confirm, scan again, " +
          "without the camera closing and taking a couple of seconds to wake up each time. A running count of " +
          "what you have added this session sits beside the toolbar.",
      },
    ],
  },
  {
    id: "inventory-quantities",
    category: "Kitchen inventory",
    title: "Quantity vs. package size, and why they are separate",
    body: [
      {
        type: "p",
        text:
          "An inventory row tracks two different numbers that are easy to conflate. **Quantity on hand** is how " +
          "much food is left -- this is what a cooked recipe deducts from. **Package** describes how it was " +
          "bought (\"1 Bag in Box of 7 oz each\"), and the purchase-time amount is kept separately.",
      },
      {
        type: "p",
        text:
          "They are separate because collapsing them broke two things. Using half a bag would zero out the " +
          "whole row, and cost-per-serving drifted upward as a package got used, because the maths divided by " +
          "the shrinking on-hand amount instead of what was actually paid for.",
      },
    ],
  },
  {
    id: "expiration-urgency",
    category: "Kitchen inventory",
    title: "Expiration dates, urgency, and the digest banner",
    body: [
      {
        type: "p",
        text:
          "Add an item without an expiration date and Chef suggests one from the USDA **FoodKeeper** dataset, " +
          "based on the food and where it is stored. It is a suggestion in the field, not a silent write -- " +
          "you can accept, edit or ignore it.",
      },
      {
        type: "p",
        text:
          "**Urgency** is computed from how close an expiry is and how long something has sat unused, and it " +
          "feeds directly into meal-plan generation: the planner is told which ingredients to build the week " +
          "around. A banner at the top of the app warns about items expiring soon or already past date, from " +
          "whichever page you are on.",
      },
      {
        type: "note",
        text:
          "FoodKeeper is an official USDA dataset, but its content has not been revised since 2018. Treat its " +
          "suggestions as a reasonable default, not a guarantee, and trust the package date when there is one.",
      },
    ],
  },
  {
    id: "food-classification",
    category: "Kitchen inventory",
    title: "NOVA group and Nutri-Score",
    body: [
      {
        type: "p",
        text:
          "When you add an item by scanning its **barcode**, Chef also records two classifications that Open " +
          "Food Facts publishes for that product, at no extra lookup. They appear as small chips under the " +
          "item name in your inventory.",
      },
      {
        type: "p",
        text:
          "**NOVA** is a food-classification system from the University of São Paulo. It groups foods by how " +
          "much industrial processing they have had, not by their nutrients: group 1 is unprocessed or " +
          "minimally processed, group 2 is a culinary ingredient like oil or butter, group 3 is a processed " +
          "food, and group 4 is ultra-processed. Open Food Facts works the group out from the product's " +
          "ingredient list.",
      },
      {
        type: "p",
        text:
          "**Nutri-Score** is the front-of-pack grade used across much of Europe, A (best) through E (worst). " +
          "It scores nutrient composition per 100g -- energy, sugar, saturated fat and salt weighed against " +
          "fibre, protein and fruit/vegetable content.",
      },
      {
        type: "note",
        text:
          "Neither grade is a verdict on whether a food belongs in your diet, and neither is Chef's opinion. " +
          "Nutri-Score in particular compares like with like within a category and nothing else, which is why " +
          "olive oil grades poorly and diet soda grades well.",
      },
      {
        type: "note",
        text:
          "A blank means Open Food Facts does not classify that product -- it is crowd-sourced, and a NOVA " +
          "group needs a readable ingredient list. Blank never means unprocessed. It is common: Nutella has a " +
          "Nutri-Score and no NOVA group at all, because too many of its ingredients could not be identified.",
      },
      {
        type: "p",
        text:
          "Items already in your pantry, and items added by hand, by photo or by import, carry no " +
          "classification. There is no barcode on those rows to look one up by, and Chef will not guess one " +
          "from the name you typed.",
      },
    ],
  },
  {
    id: "recall-awareness",
    category: "Kitchen inventory",
    title: "Recall awareness",
    body: [
      {
        type: "p",
        text:
          "Chef checks your inventory item names and brands against the USDA FSIS recall feed and the openFDA " +
          "food enforcement API. A match raises a banner across the app with the recall date and a link to the " +
          "notice; the Inventory page shows when the last check ran.",
      },
      {
        type: "note",
        text:
          "A match is a prompt to go and read the official notice -- names are matched by text, so it can flag " +
          "something you own that is not the recalled lot, and it will miss a recall whose wording does not " +
          "resemble what you typed. It is an early warning, not a clearance.",
      },
    ],
  },

  // --- Recipes ------------------------------------------------------------
  {
    id: "recipe-import",
    category: "Recipes",
    title: "Importing a recipe: what happens to it",
    body: [
      {
        type: "p",
        text:
          "Paste a URL, paste text, or upload a PDF, photo, HTML or JSON file. Chef tries the cheapest accurate " +
          "path first and only falls back to the AI when it has to.",
      },
      {
        type: "steps",
        items: [
          "**Structured data first.** Most recipe sites publish a machine-readable schema.org `Recipe` block. " +
            "If one is there, the ingredients and steps are read from it directly -- faster and far more " +
            "accurate on quantities than any model.",
          "**Text extraction second.** No structured block means the readable article is extracted from the " +
            "page, discarding navigation, ads and the story above the recipe.",
          "**AI extraction last**, in two passes -- one to find the ingredient block, one to read the lines in " +
            "it. Splitting the job is what stopped method steps being filed as ingredients.",
          "**Review before saving.** Nothing is stored until you look at the parsed result and press save. The " +
            "review screen tells you which path was used and whether it was verified against the source.",
        ],
      },
      {
        type: "note",
        text:
          "The prompts used for import are editable in Settings > AI & Models (advanced). If a particular site " +
          "or your particular model parses badly, that is adjustable without a code change or a rebuild.",
      },
    ],
  },
  {
    id: "bookmarks-import",
    category: "Recipes",
    title: "Bulk import from a browser bookmarks export",
    body: [
      {
        type: "p",
        text:
          "Export bookmarks from your browser as HTML and hand the file to Chef, and it will work through the " +
          "saved recipe pages in batches, fetching and parsing each one. This is a long job -- it fetches real " +
          "web pages and runs a model over each -- so it runs in the background with visible progress.",
      },
      {
        type: "p",
        text:
          "It is resumable and safe to run repeatedly. A URL that has already been tried is remembered, so the " +
          "next batch spends its attempts on pages it has not seen rather than retrying the same failures. " +
          "Pages that parse cleanly but are not recipes are flagged rather than silently saved.",
      },
      {
        type: "note",
        text:
          "**Batch size** is a setting (Settings > Preferences). A larger batch clears a big export in fewer " +
          "runs but each run takes proportionally longer. A bookmarks file is a record of what somebody reads " +
          "-- keep it out of any repository you publish.",
      },
    ],
  },
  {
    id: "recipe-components-tags",
    category: "Recipes",
    title: "Components, tags, and SmartTags",
    body: [
      {
        type: "p",
        text:
          "**Components** are the parts of a multi-part recipe -- a crust and a filling, a sauce and a base. " +
          "Ingredients are tagged with the part they belong to, and the recipe renders them under section " +
          "headings instead of as one undifferentiated list.",
      },
      {
        type: "p",
        text:
          "**Tags** are short labels you or the importer apply: `quick`, `one_pot`, `make_ahead`, " +
          "`freezer_friendly`. They are used for filtering and are given to the planner as hints.",
      },
      {
        type: "p",
        text:
          "**SmartTags** are derived automatically from what is actually in the recipe -- they are evidence, " +
          "and the recipe detail page shows you the evidence behind each one. They are used on the Recipes page " +
          "as an EXCLUSION filter (hide things containing X) rather than as a positive claim, for the reason in " +
          "the allergen entry below.",
      },
    ],
  },
  {
    id: "cook-mode-timers",
    category: "Recipes",
    title: "Cook mode and step timers",
    body: [
      {
        type: "p",
        text:
          "**Cook mode** is a full-screen, one-step-at-a-time view in large type, with a collapsible ingredient " +
          "panel and a jump-to-step list so you never have to leave it to check a quantity. It keeps the screen " +
          "awake while it is open (there is a toggle -- it does cost battery), and it remembers your place if " +
          "you navigate away by accident.",
      },
      {
        type: "p",
        text:
          "**Step timers**: a step that mentions a duration gets a start button. A range like \"25-30 minutes\" " +
          "starts at the shorter end. Running timers appear in a badge that stays visible everywhere in the " +
          "app, and they are computed from a stored start time -- so a timer is still correct after you reload " +
          "the page or switch tabs, rather than having quietly drifted.",
      },
      {
        type: "note",
        text:
          "Timers finish with a chime and, if you allowed notifications, a browser notification. Some browsers " +
          "refuse to play audio that was not started by a tap, so treat the sound as a bonus and the " +
          "notification as the reliable one.",
      },
    ],
  },
  {
    id: "units-and-scaling",
    category: "Recipes",
    title: "Servings scaling and the unit toggle",
    body: [
      {
        type: "p",
        text:
          "A recipe can be re-rendered at any serving count and in **Imperial**, **Metric** or **Weight**. " +
          "Nothing is converted destructively -- the stored recipe is untouched, this only changes what is " +
          "displayed, and your preferred default lives in Settings.",
      },
      {
        type: "p",
        text:
          "Weight mode is the one that matters for gluten-free baking, where flour blends measured by volume " +
          "are notoriously unreliable. It needs a density for the specific ingredient, which comes from the " +
          "food database. **Where no density is known, the toggle is unavailable for that ingredient rather " +
          "than guessing** -- an invented conversion in a recipe is worse than no conversion.",
      },
    ],
  },

  // --- Meal planning ------------------------------------------------------
  {
    id: "meal-plan-generation",
    category: "Meal planning",
    title: "How a week gets planned",
    body: [
      {
        type: "p",
        text:
          "Generating a week gives the model a specific brief, not a vague request. It receives your household " +
          "size and restrictions, the ingredients most worth using up, your saved recipes (staples and " +
          "highly-rated ones first), your kitchen equipment, any body metrics and targets you have logged, and " +
          "passages retrieved from whichever knowledge files you switched on.",
      },
      {
        type: "p",
        text:
          "The result is a **preview you edit before it becomes a plan**. Slots the catalog cannot fill become " +
          "new recipes. Once saved, each entry can be confirmed (which deducts the ingredients from inventory), " +
          "skipped, or marked as eating out.",
      },
      {
        type: "note",
        text:
          "Per-day guidance is worth using -- \"something quick\", \"leftovers\", \"we are out Thursday\" -- " +
          "it steers the week far more reliably than editing afterwards.",
      },
    ],
  },
  {
    id: "leftovers-prep-day",
    category: "Meal planning",
    title: "Leftovers and prep-day mode",
    body: [
      {
        type: "p",
        text:
          "**Leftovers** are a real link between plan entries, not a note. One cook event can fill several " +
          "slots, and the ingredients are deducted once rather than once per meal, so the grocery list does not " +
          "buy for the same dinner twice.",
      },
      {
        type: "p",
        text:
          "**Prep-day mode** asks for a week built around a single cooking session -- shared components cooked " +
          "once, weekday meals assembled from them. It fits a quick-preparation household better than seven " +
          "independent cook events.",
      },
    ],
  },
  {
    id: "grocery-list",
    category: "Meal planning",
    title: "The grocery list, and what it leaves out",
    body: [
      {
        type: "p",
        text:
          "The list is everything the week's recipes need, minus what the inventory already has, grouped by " +
          "store aisle. It stays in sync as you confirm, skip or edit entries.",
      },
      {
        type: "p",
        text:
          "Two things are deliberately excluded. Anything on your **pantry staples** list -- salt, pepper, oil " +
          "-- so the list is not the same five lines every week. And quantities that cannot be combined are " +
          "kept as separate lines rather than being merged with a guessed conversion.",
      },
    ],
  },
  {
    id: "dining-out",
    category: "Meal planning",
    title: "Dining out",
    body: [
      {
        type: "p",
        text:
          "Finds restaurants near a location you give it -- typed in, or from the browser (which needs HTTPS) " +
          "-- within a radius you set, and filters against your household's dietary restrictions. An option can " +
          "be slotted into the meal plan so the week stays coherent and the grocery list does not over-buy for " +
          "a meal nobody is cooking.",
      },
      {
        type: "note",
        text:
          "Restaurant diet data is crowd-sourced and often stale. Chef presents results as **candidates to " +
          "verify** and shows where each claim came from. It will not tell you a restaurant is safe, because " +
          "no available data source can support that claim -- call ahead.",
      },
    ],
  },

  // --- Health & nutrition -------------------------------------------------
  {
    id: "household-and-targets",
    category: "Health & nutrition",
    title: "Household members, body metrics, and daily targets",
    body: [
      {
        type: "p",
        text:
          "Each household member can carry age, sex, height, weight and activity level. Those are not decoration " +
          "-- they produce DRI-based daily nutrient targets, which the weekly nutrition roll-up is measured " +
          "against and which are injected into meal-plan generation.",
      },
      {
        type: "p",
        text:
          "Log weight, blood pressure, and a lipid panel over time and the Health page charts the trend. The " +
          "point of the charts is the feedback loop: what was planned, what it contained, and what moved.",
      },
    ],
  },
  {
    id: "biomarkers",
    category: "Health & nutrition",
    title: "ApoB, Lp(a), HbA1c and waist: what Chef tracks and why",
    body: [
      {
        type: "p",
        text:
          "Chef tracks four values beyond the standard lipid panel. All four are things a doctor may hand you " +
          "on a report, and all four had nowhere to go in this app until 2026-08-18 -- which meant they were " +
          "transcribed nowhere and trended never, in an app built around lowering cholesterol.",
      },
      {
        type: "steps",
        items: [
          "**ApoB (apolipoprotein B)** counts the atherogenic particles themselves rather than the cholesterol " +
            "they carry. The 2026 ACC/AHA multi-society dyslipidemia guideline treats it as a measurement that " +
            "can change risk assessment, particularly when it disagrees with LDL-C.",
          "**Lp(a) (lipoprotein(a))** is largely genetic and stable across a lifetime, so a single measurement " +
            "is usually enough. The same guideline recommends that adults have it measured at least once.",
          "**HbA1c** is a three-month average of blood glucose, which a single fasting glucose reading is not.",
          "**Waist circumference**, from which Chef computes waist-to-height. Shown beside BMI, never instead " +
            "of it: BMI cannot tell where mass sits, and the 2025 Lancet Commission on clinical obesity moved " +
            "diagnosis toward BMI *plus* an anthropometric measure like this one.",
        ],
      },
      {
        type: "note",
        text:
          "**Lp(a) is entered with its unit, and Chef will not convert between them.** Labs report it in both " +
          "mg/dL and nmol/L, and the two are not reliably interconvertible -- the factor depends on a protein " +
          "size that varies between people, so any fixed conversion is an approximation that different labs " +
          "disagree about. A number without its scale would be ambiguous by roughly 2.5x and would silently " +
          "corrupt the trend it joined, so Chef stores the unit alongside the value and always displays both.",
      },
      {
        type: "note",
        text:
          "**Chef attaches no thresholds, targets or risk labels to any of these.** It records what you enter, " +
          "charts the trend, and passes the current values into meal-plan generation so the planner can see " +
          "what the household is working on. What a given number means for you is a conversation with your " +
          "doctor -- this app is a meal planner, and it is not qualified to have that one.",
      },
    ],
  },
  {
    id: "bloodwork-import",
    category: "Health & nutrition",
    title: "Importing bloodwork and wearable data",
    body: [
      {
        type: "p",
        text:
          "Nobody keeps up with typing six numbers off a lab report every quarter, so a panel can be imported " +
          "from a CSV, a PDF, a **photo of the printed report**, or pasted text. Lab wording varies wildly " +
          "even within one lab, so this goes through free-text AI extraction rather than a fixed column map.",
      },
      {
        type: "p",
        text:
          "Apple Health exports (`export.xml` or the zip) are parsed directly for weight and daily steps. " +
          "Google Health Connect has no stable documented export format, so its files go through the same " +
          "free-text extraction path rather than being guessed at.",
      },
      {
        type: "note",
        text:
          "Every import produces a **preview you confirm row by row**. Nothing reaches your health history " +
          "until you accept it, and each accepted row goes through the ordinary create path -- so an imported " +
          "entry is identical to a typed one.",
      },
    ],
  },
  {
    id: "nutrition-provenance",
    category: "Health & nutrition",
    title: "Where nutrition numbers come from, and how to tell",
    body: [
      {
        type: "p",
        text:
          "Every recipe's nutrition carries a label saying where it came from, and this is the most important " +
          "thing to understand about the numbers in this app.",
      },
      {
        type: "steps",
        items: [
          "**Computed** -- every ingredient was matched to a real food record (USDA FoodData Central, or Open " +
            "Food Facts for packaged goods) and the totals were summed from actual composition data.",
          "**Partial** -- some ingredients matched and some did not. The number is real for part of the recipe " +
            "and incomplete overall.",
          "**AI estimated** -- nothing matched, and the figure is the model's guess. Treat it as an order of " +
            "magnitude, not a number.",
        ],
      },
      {
        type: "note",
        text:
          "A **USDA FoodData Central API key** (free, from their site, entered in Settings) is what moves " +
          "recipes from estimated to computed. It is the single highest-value key you can add.",
      },
    ],
  },
  {
    id: "diet-quality-and-patterns",
    category: "Health & nutrition",
    title: "Diet quality score and dietary patterns",
    body: [
      {
        type: "p",
        text:
          "A generated week can be scored for diet quality on a 0-100 scale modeled on the Healthy Eating Index " +
          "(HEI-2020), which is the published US standard and has a national average around 58 -- so the number " +
          "has something to mean against.",
      },
      {
        type: "p",
        text:
          "**This is an honest approximation, not the certified index.** Computing HEI exactly requires food " +
          "group data Chef does not have for every ingredient. It is a signal that moves in the right " +
          "direction, useful for comparing your own weeks to each other, not a clinical measurement.",
      },
      {
        type: "p",
        text:
          "Separately, a **dietary pattern** preset concretely biases ingredient selection rather than leaving " +
          "a goal as free text for the model to reinterpret each week. The Portfolio pattern -- plant sterols, " +
          "viscous fibre, nuts, soy protein -- is the one specifically studied for LDL reduction.",
      },
    ],
  },
  {
    id: "allergens-and-claims",
    category: "Health & nutrition",
    title: "Restrictions, cross-contact, and what Chef will not claim",
    body: [
      {
        type: "p",
        text:
          "Dietary restrictions are structured data, not free text, and they are checked by ordinary code -- " +
          "not by asking a model -- at recipe import, at plan generation and at plan confirmation. A recipe " +
          "containing something you have excluded is flagged every time, deterministically.",
      },
      {
        type: "p",
        text:
          "For gluten there is an **observance level**: avoiding gluten only, or also avoiding cross-contact. " +
          "At the stricter level, known shared-equipment risks are flagged too -- non-certified oats being the " +
          "common one, since oats are not a gluten grain but are usually milled alongside wheat.",
      },
      {
        type: "note",
        text:
          "**Chef flags what it finds. It never certifies what it did not find.** No part of this app will tell " +
          "you a dish is gluten-free, or free of any allergen -- because a recogniser that flags what it " +
          "recognises turns every miss into a false all-clear the moment you invert it, and a false all-clear " +
          "in a celiac household is not a bug, it is harm. An unflagged recipe means nothing was recognised, " +
          "not that nothing is there. Read the label.",
      },
    ],
  },

  // --- AI & the Chef ------------------------------------------------------
  {
    id: "chat-and-actions",
    category: "AI & the Chef",
    title: "The Chef chat, and the actions it proposes",
    body: [
      {
        type: "p",
        text:
          "The chat panel is available from every page and keeps its history. It is given real, current context " +
          "-- your household and restrictions, the active meal plan with real entry ids, your current " +
          "inventory, recipes relevant to what you just asked (with their ingredient lists), and passages " +
          "retrieved from your active knowledge files.",
      },
      {
        type: "p",
        text:
          "Say \"we're out of milk\" or \"we made the lentil soup\" and the Chef will **propose** an action -- " +
          "a button you press. Nothing happens until you press it, and when you do, it runs the exact same " +
          "endpoint the rest of the app uses. Chat has no private path to your data.",
      },
      {
        type: "note",
        text:
          "The recipe list the Chef can see by id is capped, and it is told when it is only seeing part of the " +
          "catalog, so it should say it is unsure rather than declaring you have no such recipe. If it ever " +
          "does claim something does not exist, search the Recipes page before believing it.",
      },
    ],
  },
  {
    id: "knowledge-files",
    category: "AI & the Chef",
    title: "Knowledge files: grounding the Chef in real references",
    body: [
      {
        type: "p",
        text:
          "Upload nutrition references -- a dietitian's guidance, a diet plan, clinical handouts -- and Chef " +
          "extracts the text, indexes it, and retrieves the passages relevant to each meal-plan generation and " +
          "each chat message. This is what makes the advice grounded in a document you chose rather than in " +
          "whatever the model remembers.",
      },
      {
        type: "p",
        text:
          "A small public-domain reference set ships with the app: the Dietary Guidelines for Americans, the " +
          "DASH eating pattern, the Portfolio diet for LDL reduction, and FDA/NIAID food-allergen material. " +
          "**All of it is inactive by default** -- nothing grounds your plans until you switch it on.",
      },
      {
        type: "note",
        text:
          "Indexing runs as a background job and needs the embedding model to be available. A file that is " +
          "uploaded but not yet indexed is stored but not yet retrievable.",
      },
    ],
  },
  {
    id: "models-and-prompts",
    category: "AI & the Chef",
    title: "Which model does what, and editing the prompts",
    body: [
      {
        type: "p",
        text:
          "Chef uses four Ollama models, all configurable in Settings > AI & Models: a **chat** model (chat, " +
          "meal planning, recipe generation), an **extraction** model (structured parsing), a **vision** model " +
          "(photos of food, receipts, printed reports), and an **embedding** model (knowledge file indexing).",
      },
      {
        type: "p",
        text:
          "**Context size** (`num_ctx`) and **timeout** matter more than they look. A recipe import hands the " +
          "model a long document; too small a context window silently truncates it, and the symptom is a " +
          "recipe that parses with half its ingredients missing rather than an error.",
      },
      {
        type: "p",
        text:
          "Every import and extraction prompt is editable in the same place, under advanced settings. If your " +
          "particular model keeps producing output Chef cannot parse, that is fixable by editing a prompt " +
          "rather than waiting for a code change.",
      },
    ],
  },

  // --- Integrations -------------------------------------------------------
  {
    id: "icloud-calendar-setup",
    category: "Integrations",
    title: "iCloud Calendar sync",
    body: [
      {
        type: "p",
        text:
          "Pushes your meal plan into a calendar on iCloud, kept in sync as the plan changes. Simpler to set up " +
          "than Google Calendar because there is no OAuth flow -- it uses CalDAV with an Apple " +
          "**app-specific password**, not your Apple ID password.",
      },
      {
        type: "steps",
        items: [
          "Sign in at appleid.apple.com and generate an app-specific password for Chef.",
          "In Settings > Integrations, enter your iCloud account name and that generated password.",
          "Pick which calendar to write to, then enable sync.",
        ],
      },
      {
        type: "note",
        text:
          "This is a one-way push: Chef writes the plan out, and does not read your calendar back. Edits made " +
          "in the calendar app will be overwritten on the next sync.",
      },
    ],
  },
  {
    id: "order-history-import",
    category: "Integrations",
    title: "Grocery order history import",
    body: [
      {
        type: "p",
        text:
          "A CSV or XLSX of an online grocery order can be imported straight into inventory, complete with " +
          "prices and purchase dates. Prices unlock cost-per-serving; purchase dates feed shelf-life estimation.",
      },
      {
        type: "p",
        text:
          "Retailers all export differently, so a **column-mapping profile** describes which column is which, " +
          "and profiles are editable in Settings. That makes this format-agnostic: it survives one retailer " +
          "changing their export, and it works for anyone who clones this repo and shops somewhere else.",
      },
      {
        type: "note",
        text:
          "There is no scraping and no stored retailer login anywhere in this app, by design. You bring the " +
          "file, from whatever export path your retailer offers.",
      },
    ],
  },

  // --- Data ---------------------------------------------------------------
  {
    id: "where-data-lives",
    category: "Data",
    title: "Where your data actually lives",
    body: [
      {
        type: "p",
        text:
          "Everything is in one SQLite database inside a Docker volume, plus a small number of files beside it " +
          "(uploaded knowledge documents, recipe images, timer sounds, TLS certificates, and the encryption key " +
          "for your secrets). Because it is a named volume and not the container filesystem, it survives " +
          "`docker compose up --build`.",
      },
      {
        type: "p",
        text:
          "Settings, prompts, API keys, household preferences and themes are all rows in that database rather " +
          "than files or environment variables. The `.env` file only carries bootstrap plumbing -- the things " +
          "that must exist before the database can be opened at all. That is why changing a setting takes " +
          "effect without a rebuild.",
      },
      {
        type: "note",
        text:
          "API keys are encrypted at rest. The key that decrypts them sits next to the database, so a backup " +
          "of the volume is what you need to restore them -- copying the database file alone is not enough.",
      },
    ],
  },
  {
    id: "recipe-export",
    category: "Data",
    title: "Exporting recipes in a portable format",
    body: [
      {
        type: "p",
        text:
          "Recipes export as schema.org JSON-LD -- the same structured format recipe sites publish and Google " +
          "consumes. It round-trips: an exported file can be imported straight back into Chef, or into anything " +
          "else that understands the format.",
      },
      {
        type: "p",
        text:
          "This is the answer to \"what if I stop using this app\". Your recipes are not trapped in it, and the " +
          "export needs no special tooling to read.",
      },
    ],
  },

  // --- Security -----------------------------------------------------------
  {
    id: "password-gate",
    category: "Security",
    title: "The optional password gate",
    body: [
      {
        type: "p",
        text:
          "Chef ships with **no authentication**, which is a reasonable default on a home LAN and completely " +
          "wrong the moment it is reachable from anywhere else. Settings > Security can turn on a single shared " +
          "password for the whole app.",
      },
      {
        type: "p",
        text:
          "It is one password for the household, not per-user accounts, and there are no permission levels -- " +
          "anyone who gets in sees everything. It is the right size for a household and the wrong size for " +
          "exposing this app to the internet.",
      },
      {
        type: "note",
        text:
          "Do not port-forward Chef to the internet. There is no rate limiting, no account lockout, and no " +
          "audit log. If you need remote access, put it behind a VPN.",
      },
    ],
  },

];
