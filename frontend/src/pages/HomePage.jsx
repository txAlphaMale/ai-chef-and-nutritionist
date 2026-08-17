import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import InfoTip from "../components/InfoTip";
import { formatDate, formatRelativeDay } from "../utils/datetime";
import { kgToLbs } from "../utils/units";

/** The Home dashboard (capstone review 2026-08-16, backlog B24.3).
 *
 * This page had been the Phase 0 stub since Phase 0 -- a backend status
 * line, household size, and a sentence promising "dashboard widgets for
 * expiring items, this week's meal plan, and the persistent chat panel" as
 * phases landed. All three landed months ago, so the app's front door was
 * advertising features that had already shipped while every real surface
 * sat one click away behind the nav.
 *
 * What it deliberately does NOT do:
 *
 *  - **Repeat the app-wide banners.** The expiring digest, recall warnings,
 *    the job badge and the chat widget are all mounted in App.jsx outside
 *    <Routes> and are visible from every page including this one. The
 *    recall count here is a pointer, not a second copy of the warning --
 *    one safety warning shown twice in different words is worse than one
 *    shown once.
 *  - **Compute anything.** Every figure comes from GET /api/system/dashboard
 *    in one round trip. Working out in the browser which entry is "tonight"
 *    would use the browser's clock and disagree with the server by up to a
 *    day for anyone not in UTC -- the same class of bug the date utility
 *    was written to kill.
 *  - **Show the backend status line it used to.** That was scaffolding for
 *    a Phase 0 with nothing else to show; a dashboard that renders at all
 *    has already proved the backend is up.
 */
export default function HomePage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .get("/system/dashboard")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div className="card">
        <h3>Chef</h3>
        <p className="error-text">Could not load the dashboard: {error}</p>
        <p className="hint">
          The backend may still be starting up. <Link to="/settings">Settings</Link> shows connection status.
        </p>
      </div>
    );
  }

  if (!data) return <p>Loading...</p>;

  const { inventory, meal_plan: plan, recipes, health, recalls, setup } = data;
  const outstandingSetup = (setup || []).filter((s) => !s.done);

  return (
    <div className="dashboard">
      {/* First-run checklist. Disappears entirely once everything is done,
          rather than living on as a wall of ticks -- a checklist that never
          goes away stops being a checklist and becomes furniture. */}
      {outstandingSetup.length > 0 && (
        <div className="card dashboard-setup">
          <h3>
            Finish setting up
            <InfoTip label="Setup checklist" wikiEntry="first-run-checklist">
              Chef runs with none of this configured, but each item unlocks more of it. This card disappears once
              they are all done.
            </InfoTip>
          </h3>
          <p className="hint">
            {setup.length - outstandingSetup.length} of {setup.length} done.
          </p>
          <ul className="dashboard-setup-list">
            {outstandingSetup.map((item) => (
              <li key={item.key}>
                <a href={item.route}>
                  <strong>{item.label}</strong>
                </a>
                <div className="hint">{item.hint}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="dashboard-grid">
        {/* --- Tonight -------------------------------------------------- */}
        <div className="card dashboard-card">
          <h3>Tonight</h3>
          {!plan.plan_id && (
            <p className="hint">
              No meal plan yet. <Link to="/meal-plan">Generate a week</Link> and Chef will build it around what is
              already in your inventory.
            </p>
          )}
          {plan.plan_id && !plan.is_current_week && (
            <p className="hint">
              The most recent plan covers the week of {formatDate(plan.week_start_date)}, which is not this week.{" "}
              <Link to="/meal-plan">Plan this week</Link>.
            </p>
          )}
          {plan.plan_id && plan.is_current_week && plan.today_entries.length === 0 && (
            <p className="hint">
              Nothing planned for today in the week of {formatDate(plan.week_start_date)}.{" "}
              <Link to="/meal-plan">Open the plan</Link>.
            </p>
          )}
          {plan.today_entries.length > 0 && (
            <ul className="dashboard-meals">
              {plan.today_entries.map((entry) => (
                <li key={entry.entry_id}>
                  <span className="tag">{entry.meal_type}</span>{" "}
                  {entry.recipe_id ? (
                    <Link to={`/recipes/${entry.recipe_id}`}>
                      <strong>{entry.recipe_title}</strong>
                    </Link>
                  ) : (
                    <strong>{entry.recipe_title || (entry.is_eating_out ? "Eating out" : "Nothing assigned")}</strong>
                  )}{" "}
                  <span className="hint">{entry.servings} servings</span>{" "}
                  {entry.is_confirmed && <span className="tag">cooked</span>}
                  {entry.is_skipped && <span className="tag">skipped</span>}
                  {entry.is_eating_out && !entry.is_skipped && <span className="tag">out</span>}
                </li>
              ))}
            </ul>
          )}
          {plan.plan_id && (
            <p className="hint dashboard-week-line">
              This week: {plan.confirmed} cooked, {plan.planned} still planned, {plan.skipped} skipped
              {plan.grocery_outstanding > 0 && (
                <>
                  {" · "}
                  <Link to="/meal-plan">{plan.grocery_outstanding} still to buy</Link>
                </>
              )}
            </p>
          )}
        </div>

        {/* --- Use these first ------------------------------------------ */}
        <div className="card dashboard-card">
          <h3>
            Use these first
            <InfoTip label="Use these first" wikiEntry="expiration-urgency">
              Anything already expired, plus anything expiring within {inventory.within_days} days. This is also
              what meal-plan generation is told to build the week around.
            </InfoTip>
          </h3>
          {inventory.total_items === 0 ? (
            <p className="hint">
              The inventory is empty, so meal planning is working blind.{" "}
              <Link to="/inventory">Add something</Link> -- you can scan a barcode or photograph a receipt.
            </p>
          ) : inventory.soonest.length === 0 ? (
            <p className="hint">
              Nothing expiring in the next {inventory.within_days} days, across {inventory.total_items} items.
            </p>
          ) : (
            <>
              <ul className="dashboard-expiring">
                {inventory.soonest.map((item) => (
                  <li key={item.id}>
                    <strong>{item.name}</strong>{" "}
                    <span className={item.days_until != null && item.days_until < 0 ? "error-text" : "hint"}>
                      {item.expiration_date
                        ? `${formatDate(item.expiration_date)} (${formatRelativeDay(item.expiration_date)})`
                        : "no date"}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="hint">
                <Link to="/inventory">
                  {inventory.expired > 0 && `${inventory.expired} expired, `}
                  {inventory.expiring_soon} expiring soon, of {inventory.total_items} items
                </Link>
              </p>
            </>
          )}
        </div>

        {/* --- Kitchen at a glance -------------------------------------- */}
        <div className="card dashboard-card">
          <h3>Your kitchen</h3>
          <dl className="dashboard-stats">
            <div>
              <dt>Recipes</dt>
              <dd>
                <Link to="/recipes">{recipes.total}</Link>
              </dd>
            </div>
            <div>
              <dt>Staples</dt>
              <dd>{recipes.staples}</dd>
            </div>
            <div>
              <dt>Inventory</dt>
              <dd>
                <Link to="/inventory">{inventory.total_items}</Link>
              </dd>
            </div>
            <div>
              <dt>Household</dt>
              <dd>{data.household_size}</dd>
            </div>
          </dl>
          {recalls.active > 0 && (
            <p className="error-text">
              {recalls.active} active recall {recalls.active === 1 ? "match" : "matches"} against your inventory --
              the details are in the banner above.
            </p>
          )}
        </div>

        {/* --- Health --------------------------------------------------- */}
        <div className="card dashboard-card">
          <h3>
            Latest readings
            <InfoTip label="Latest readings" wikiEntry="household-and-targets">
              Each value carries its own date, because a lipid panel and a weigh-in almost never happen on the same
              day -- showing one "latest entry" would blank the cholesterol every time somebody stepped on a scale.
            </InfoTip>
          </h3>
          {health.entry_count === 0 ? (
            <p className="hint">
              Nothing logged yet. <Link to="/health">Log a weight</Link>, or import a lab panel from a PDF or a
              photo of the printed report.
            </p>
          ) : (
            <>
              <dl className="dashboard-stats">
                {health.latest.weight_kg && (
                  <div>
                    <dt>Weight</dt>
                    <dd>
                      {kgToLbs(health.latest.weight_kg.value)} lb
                      <span className="hint"> {formatDate(health.latest.weight_kg.entry_date)}</span>
                    </dd>
                  </div>
                )}
                {health.latest.bmi && (
                  <div>
                    <dt>BMI</dt>
                    <dd>
                      {health.latest.bmi.value}
                      <span className="hint"> {formatDate(health.latest.bmi.entry_date)}</span>
                    </dd>
                  </div>
                )}
                {health.latest.ldl_mg_dl && (
                  <div>
                    <dt>LDL</dt>
                    <dd>
                      {health.latest.ldl_mg_dl.value} mg/dL
                      <span className="hint"> {formatDate(health.latest.ldl_mg_dl.entry_date)}</span>
                    </dd>
                  </div>
                )}
                {health.latest.hdl_mg_dl && (
                  <div>
                    <dt>HDL</dt>
                    <dd>
                      {health.latest.hdl_mg_dl.value} mg/dL
                      <span className="hint"> {formatDate(health.latest.hdl_mg_dl.entry_date)}</span>
                    </dd>
                  </div>
                )}
              </dl>
              <p className="hint">
                <Link to="/health">See the trends</Link>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
