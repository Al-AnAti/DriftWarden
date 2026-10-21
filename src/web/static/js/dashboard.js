/* DriftWarden control plane: live updates over Server-Sent Events, dark modals and operator actions. */
(function () {
    "use strict";

    const W = window.WARDEN || { snapshot: null, statusMeta: {}, overallMeta: {}, page: "" };
    const TONES = ["emerald", "amber", "sky", "rose", "violet", "slate"];
    const FLASH_KEY = "warden.flash";

    function esc(text) {
        return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        })[c]);
    }

    /* ------------------------------------------------------------- toasts */

    function toast(message, tone) {
        const host = document.getElementById("toasts");
        if (!host) return;
        const el = document.createElement("div");
        el.className = "toast tone-" + (tone || "slate");
        el.textContent = message;
        host.appendChild(el);
        setTimeout(() => el.classList.add("show"), 10);
        setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 300); }, 6000);
    }

    /* Queue a toast to be shown on the next page (used around redirects). */
    function flash(message, tone) {
        try { sessionStorage.setItem(FLASH_KEY, JSON.stringify({ message, tone })); } catch (e) { /* storage blocked */ }
    }

    function showFlash() {
        try {
            const raw = sessionStorage.getItem(FLASH_KEY);
            if (!raw) return;
            sessionStorage.removeItem(FLASH_KEY);
            const { message, tone } = JSON.parse(raw);
            toast(message, tone);
        } catch (e) { /* ignore */ }
    }

    function redirectHome(message, tone) {
        flash(message, tone || "emerald");
        window.location.assign("/");
    }

    /* -------------------------------------------------------------- modal */

    const modal = {
        root: null, title: null, message: null, form: null, confirm: null, cancel: null, resolve: null,
    };

    function modalInit() {
        modal.root = document.getElementById("modal-root");
        if (!modal.root) return;
        modal.title = document.getElementById("modal-title");
        modal.message = document.getElementById("modal-message");
        modal.form = document.getElementById("modal-form");
        modal.confirm = document.getElementById("modal-confirm");
        modal.cancel = document.getElementById("modal-cancel");
        modal.cancel.addEventListener("click", () => modalClose(null));
        modal.confirm.addEventListener("click", modalSubmit);
        modal.form.addEventListener("submit", (event) => { event.preventDefault(); modalSubmit(); });
        modal.root.addEventListener("click", (event) => { if (event.target === modal.root) modalClose(null); });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !modal.root.classList.contains("hidden")) modalClose(null);
        });
    }

    function modalSubmit() {
        const values = {};
        new FormData(modal.form).forEach((value, key) => { values[key] = String(value).trim(); });
        for (const input of modal.form.querySelectorAll("[required]")) {
            if (!input.value.trim()) { input.focus(); input.classList.add("invalid"); return; }
        }
        modalClose(values);
    }

    function modalClose(result) {
        if (!modal.root) return;
        modal.root.classList.add("hidden");
        modal.root.setAttribute("aria-hidden", "true");
        const resolve = modal.resolve;
        modal.resolve = null;
        if (resolve) resolve(result);
    }

    /**
     * Open the dark modal. Resolves with an object of input values (or {} when there are
     * no inputs) on Confirm, and with null on Cancel, Esc or a click outside.
     * options: { title, message, inputs: [{name, label, placeholder, required, value}], confirmText, danger }
     */
    function wardenModal(options) {
        if (!modal.root) return Promise.resolve({});
        modal.title.textContent = options.title || "Confirm";
        modal.message.textContent = options.message || "";
        modal.form.innerHTML = (options.inputs || []).map((input) =>
            '<label class="block text-xs uppercase tracking-wider text-slate-400">' + esc(input.label || input.name) +
            '<input class="modal-input" name="' + esc(input.name) + '" placeholder="' + esc(input.placeholder || "") + '"' +
            (input.required ? " required" : "") + ' value="' + esc(input.value || "") + '" autocomplete="off"></label>'
        ).join("");
        modal.confirm.textContent = options.confirmText || "Confirm";
        modal.confirm.className = "btn-lg " + (options.danger ? "btn-rose" : "btn-emerald");
        modal.root.classList.remove("hidden");
        modal.root.setAttribute("aria-hidden", "false");
        const first = modal.form.querySelector("input");
        setTimeout(() => (first || modal.confirm).focus(), 20);
        return new Promise((resolve) => { modal.resolve = resolve; });
    }
    window.wardenModal = wardenModal;

    /* ---------------------------------------------------------- rendering */

    function setTone(el, tone) {
        if (!el) return;
        TONES.forEach((t) => el.classList.remove("tone-" + t));
        el.classList.add("tone-" + tone);
    }

    function setHidden(id, hidden) {
        const el = document.getElementById(id);
        if (el) el.classList.toggle("hidden", hidden);
    }

    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    function badge(status) {
        const meta = W.statusMeta[status] || { label: status, tone: "slate" };
        return '<span class="badge tone-' + meta.tone + '">' + esc(meta.label) + "</span>";
    }

    function actionButton(action, ref, label, cls) {
        return ' <button onclick="wardenAction(\'' + action + '\', \'' + esc(ref) + '\')" class="btn ' + cls + '">' + label + "</button>";
    }

    function rowHtml(r) {
        const changed = r.diffs.filter((d) => d.changed && d.field !== "state").map((d) => d.field);
        const service = r.service_name || (r.project ? "project: " + r.project : "—");
        let actions = '<a href="/diff/' + encodeURIComponent(r.ref) + '" class="btn btn-ghost">Diff</a>';
        if (r.actions.includes("revert")) actions += actionButton("revert", r.ref, r.drift_status === "missing" ? "Spin up" : "Revert", "btn-rose");
        if (r.actions.includes("kill")) actions += actionButton("kill", r.ref, "Kill", "btn-rose");
        if (r.actions.includes("adopt")) actions += actionButton("adopt", r.ref, "Adopt", "btn-emerald");
        if (r.actions.includes("adopt_deletion")) actions += actionButton("adopt_deletion", r.ref, "Adopt deletion", "btn-emerald");
        if (r.actions.includes("whitelist")) actions += ' <button onclick="wardenWhitelist(\'CONTAINER\', \'' + esc(r.container_name) + '\')" class="btn btn-ghost">Whitelist</button>';
        if (r.pending_adoption && r.pending_adoption.pr_url) actions += ' <a href="' + esc(r.pending_adoption.pr_url) + '" target="_blank" class="btn btn-violet">PR #' + esc(r.pending_adoption.pr_number) + "</a>";
        const finding = changed.length
            ? '<span class="text-amber-300 font-mono text-xs">' + esc(changed.join(", ")) + "</span>"
            : '<span class="text-xs">' + esc(r.message) + "</span>";
        return (
            '<tr class="hover:bg-slate-800/40">' +
            '<td class="px-6 py-3">' + badge(r.drift_status) + "</td>" +
            '<td class="px-6 py-3 font-mono"><a class="text-indigo-300 hover:underline" href="/diff/' + encodeURIComponent(r.ref) + '">' + esc(r.display_name || r.container_name || r.service_name) + "</a>" +
            (r.container_id ? '<span class="block text-xs text-slate-500">' + esc(r.container_id.slice(0, 12)) + "</span>" : '<span class="block text-xs text-slate-500">declared in Git, not running</span>') + "</td>" +
            '<td class="px-6 py-3 font-mono text-slate-300">' + esc(service) + "</td>" +
            '<td class="px-6 py-3 font-mono text-slate-300">' + esc(r.image || "—") + "</td>" +
            '<td class="px-6 py-3 text-slate-300">' + esc(r.status) + "</td>" +
            '<td class="px-6 py-3 text-slate-400 max-w-md">' + finding + "</td>" +
            '<td class="px-6 py-3 text-right whitespace-nowrap space-x-1">' + actions + "</td>" +
            "</tr>"
        );
    }

    const ORDER = ["rogue", "drifted", "missing", "pending_adoption", "matched"];

    function renderDashboard(snapshot) {
        const tbody = document.getElementById("state-rows");
        if (!tbody) return;
        const records = snapshot.records.slice().sort((a, b) => {
            const d = ORDER.indexOf(a.drift_status) - ORDER.indexOf(b.drift_status);
            return d !== 0 ? d : a.ref.localeCompare(b.ref);
        });
        if (!records.length) {
            const why = !snapshot.baseline.available
                ? "No baseline loaded, so containers cannot be classified."
                : !snapshot.docker_connected ? "Docker is unreachable." : "No containers or services to show.";
            tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-10 text-center text-slate-500">' + esc(why) + "</td></tr>";
        } else {
            tbody.innerHTML = records.map(rowHtml).join("");
        }
        Object.entries(snapshot.counts).forEach(([key, value]) => {
            const el = document.querySelector('[data-count="' + key + '"]');
            if (el) el.textContent = value;
        });
        const enf = document.getElementById("enforcement-state");
        if (enf) {
            enf.textContent = snapshot.mode === "enforce" ? (snapshot.enforcement_active ? "Armed" : "Grace window") : "Manual only";
            enf.className = "text-xl font-semibold mt-1 " + (snapshot.enforcement_active ? "text-rose-400" : "text-slate-300");
        }
        setText("exempt-note", snapshot.exempt.length + " exempt");
        const exempt = document.getElementById("exempt-list");
        if (exempt) exempt.innerHTML = snapshot.exempt.map((e) => "<li>" + esc(e) + "</li>").join("");
    }

    function renderChrome(snapshot) {
        const overall = W.overallMeta[snapshot.overall] || { label: snapshot.overall, tone: "slate" };
        setText("status-text", overall.label);
        setTone(document.getElementById("status-pill"), overall.tone);
        setHidden("banner-baseline", snapshot.baseline.available);
        setText("banner-baseline-error", snapshot.baseline.error || "");
        setHidden("banner-docker", snapshot.docker_connected);
        setText("banner-docker-error", snapshot.docker_error || "");
        setHidden("banner-degraded", snapshot.baseline.available === false || !snapshot.baseline.error);
        setText("banner-degraded-error", snapshot.baseline.error || "");
        const when = snapshot.taken_at ? new Date(snapshot.taken_at).toISOString().slice(11, 19) : "";
        setText("footer-updated", "snapshot " + when + " UTC via " + snapshot.trigger);
    }

    let navigatingAway = false;

    function renderDiffPage(snapshot) {
        if (!W.diffRef || navigatingAway) return;
        const root = document.querySelector("[data-diff-ref]");
        if (!root) return;
        const record = snapshot.records.find((r) => r.ref === W.diffRef || r.container_name === W.diffRef || r.service_name === W.diffRef);
        const previous = root.getAttribute("data-diff-status");
        if (!record) {
            navigatingAway = true;
            redirectHome(W.diffRef + " is no longer tracked on this host.", "sky");
            return;
        }
        if (record.drift_status !== previous) {
            if (record.ref !== W.diffRef) {
                navigatingAway = true;
                window.location.replace("/diff/" + encodeURIComponent(record.ref));
                return;
            }
            toast("State of " + W.diffRef + " changed to " + record.drift_status + "; refreshing…", "sky");
            navigatingAway = true;
            setTimeout(() => window.location.reload(), 800);
        }
    }

    function apply(payload) {
        const snapshot = payload.snapshot;
        W.snapshot = snapshot;
        W.version = payload.version;
        renderChrome(snapshot);
        if (W.page === "dashboard") renderDashboard(snapshot);
        renderDiffPage(snapshot);
    }

    /* --------------------------------------------------------------- SSE */

    function connect() {
        if (!window.EventSource) return;
        const source = new EventSource("/api/state/stream");
        const indicator = document.getElementById("live-indicator");
        source.addEventListener("snapshot", (event) => {
            try { apply(JSON.parse(event.data)); } catch (err) { console.error("bad snapshot", err); }
            if (indicator) { indicator.textContent = "live"; indicator.className = "text-emerald-400"; }
        });
        source.onopen = () => { if (indicator) { indicator.textContent = "live"; indicator.className = "text-emerald-400"; } };
        source.onerror = () => { if (indicator) { indicator.textContent = "reconnecting…"; indicator.className = "text-amber-400"; } };
    }

    /* ----------------------------------------------------------- actions */

    async function post(url, body, method) {
        const response = await fetch(url, {
            method: method || "POST",
            headers: { "Content-Type": "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        let data = {};
        try { data = await response.json(); } catch (e) { /* no body */ }
        if (!response.ok) {
            throw new Error(data.message || data.detail || ("HTTP " + response.status));
        }
        return data;
    }

    const ACTIONS = {
        revert: {
            title: "Enforce baseline",
            message: (ref) => "Recreate '" + ref + "' exactly as declared in Git? Any existing container for this service is stopped and removed first.",
            confirmText: "Enforce baseline",
            danger: true,
            progress: (ref) => "Reverting " + ref + "…",
            destructive: true,
        },
        kill: {
            title: "Enforce kill",
            message: (ref) => "Stop and remove rogue container '" + ref + "'? This cannot be undone from DriftWarden.",
            confirmText: "Kill container",
            danger: true,
            progress: (ref) => "Removing " + ref + "…",
            destructive: true,
        },
        adopt: {
            title: "Adopt into Git",
            message: (ref) => "Synthesize YAML for '" + ref + "', cut a branch and open a pull request on the baseline repository. Environment values are written as ${KEY} placeholders.",
            inputs: [{ name: "note", label: "Note for the pull request (optional)", placeholder: "why this workload exists" }],
            confirmText: "Open pull request",
            progress: (ref) => "Opening pull request for " + ref + "…",
            destructive: false,
            endpoint: "adopt",
        },
        adopt_deletion: {
            title: "Adopt deletion",
            message: (ref) => "Open a pull request that removes service '" + ref + "' from the baseline, so Git matches the host where it is no longer running.",
            inputs: [{ name: "note", label: "Note for the pull request (optional)", placeholder: "why the service was retired" }],
            confirmText: "Open deletion PR",
            progress: (ref) => "Opening deletion pull request for " + ref + "…",
            destructive: false,
            endpoint: "adopt-deletion",
        },
    };

    window.wardenAction = async function (action, ref) {
        const spec = ACTIONS[action];
        if (!spec) return;
        const values = await wardenModal({
            title: spec.title,
            message: spec.message(ref),
            inputs: spec.inputs || [],
            confirmText: spec.confirmText,
            danger: spec.danger,
        });
        if (values === null) return;
        const body = spec.inputs ? { container_id: ref, note: values.note || null } : undefined;
        toast(spec.progress(ref), "sky");
        try {
            const result = await post("/api/actions/" + (spec.endpoint || action) + "/" + encodeURIComponent(ref), body);
            /* No automatic jump to GitHub: the PR link appears on the entry once it is buffered. */
            if (spec.destructive && W.page === "dashboard" && W.diffRef) {
                /* On /diff/{name} the target no longer exists under this URL: go home with a toast. */
                navigatingAway = true;
                redirectHome(result.message, "emerald");
                return;
            }
            toast(result.message, "emerald");
        } catch (err) {
            toast(spec.title + " failed: " + err.message, "rose");
        }
    };

    window.wardenWhitelist = async function (type, identifier) {
        const values = await wardenModal({
            title: "Whitelist " + type.toLowerCase(),
            message: "'" + identifier + "' will be exempt from drift alerts and autonomous enforcement until the exemption is removed on Cluster Config.",
            inputs: [{ name: "reason", label: "Reason", placeholder: "e.g. managed by another team", required: true }],
            confirmText: "Whitelist",
        });
        if (values === null) return;
        try {
            const entry = await post("/api/whitelist", { target_type: type, target_identifier: identifier, reason: values.reason });
            const message = "Whitelisted " + entry.target_type.toLowerCase() + " " + entry.target_identifier;
            if (W.diffRef) { navigatingAway = true; redirectHome(message, "emerald"); return; }
            toast(message, "emerald");
        } catch (err) {
            toast("Whitelist failed: " + err.message, "rose");
        }
    };

    window.wardenWhitelistForm = function (event) {
        event.preventDefault();
        const form = event.target;
        post("/api/whitelist", {
            target_type: form.target_type.value,
            target_identifier: form.target_identifier.value.trim(),
            reason: form.reason.value.trim(),
        }).then((entry) => {
            flash("Whitelisted " + entry.target_type.toLowerCase() + " " + entry.target_identifier, "emerald");
            window.location.reload();
        }).catch((err) => toast("Whitelist failed: " + err.message, "rose"));
        return false;
    };

    window.wardenUnwhitelist = async function (id, identifier) {
        const values = await wardenModal({
            title: "Remove exemption",
            message: "'" + identifier + "' will be evaluated against the baseline again on the next cycle.",
            confirmText: "Remove",
            danger: true,
        });
        if (values === null) return;
        try {
            const result = await post("/api/whitelist/" + id, undefined, "DELETE");
            flash(result.message, "emerald");
            window.location.reload();
        } catch (err) {
            toast("Remove failed: " + err.message, "rose");
        }
    };

    window.wardenReconcile = async function () {
        toast("Reconciling…", "sky");
        try {
            const data = await post("/api/reconcile");
            apply(data);
            const summary = Object.entries(data.snapshot.counts).filter(([, v]) => v).map(([k, v]) => k + "=" + v).join(" ");
            toast("Reconciled: " + (summary || "nothing to show"), "emerald");
        } catch (err) {
            toast("Reconcile failed: " + err.message, "rose");
        }
    };

    modalInit();
    showFlash();
    if (W.snapshot) renderChrome(W.snapshot);
    connect();
})();
