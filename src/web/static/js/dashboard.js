/* DriftWarden control plane: live updates over Server-Sent Events and operator actions. */
(function () {
    "use strict";

    const W = window.WARDEN || { snapshot: null, statusMeta: {}, overallMeta: {}, page: "" };
    const TONES = ["emerald", "amber", "sky", "rose", "violet", "slate"];

    function esc(text) {
        return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        })[c]);
    }

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

    /* ---------------------------------------------------------- rendering */

    function badge(status) {
        const meta = W.statusMeta[status] || { label: status, tone: "slate" };
        return '<span class="badge tone-' + meta.tone + '">' + esc(meta.label) + "</span>";
    }

    function rowHtml(r) {
        const changed = r.diffs.filter((d) => d.changed).map((d) => d.field);
        const service = r.service_name || (r.project ? "project: " + r.project : "—");
        let actions = '<a href="/diff/' + encodeURIComponent(r.ref) + '" class="btn btn-ghost">Diff</a>';
        if (r.actions.includes("revert")) actions += ' <button onclick="wardenAction(\'revert\', \'' + esc(r.ref) + '\')" class="btn btn-rose">Revert</button>';
        if (r.actions.includes("kill")) actions += ' <button onclick="wardenAction(\'kill\', \'' + esc(r.ref) + '\')" class="btn btn-rose">Kill</button>';
        if (r.actions.includes("adopt")) actions += ' <button onclick="wardenAction(\'adopt\', \'' + esc(r.ref) + '\')" class="btn btn-emerald">Adopt</button>';
        if (r.actions.includes("whitelist")) actions += ' <button onclick="wardenWhitelist(\'CONTAINER\', \'' + esc(r.container_name) + '\')" class="btn btn-ghost">Whitelist</button>';
        if (r.pending_adoption && r.pending_adoption.pr_url) actions += ' <a href="' + esc(r.pending_adoption.pr_url) + '" target="_blank" class="btn btn-violet">PR #' + esc(r.pending_adoption.pr_number) + "</a>";
        const finding = changed.length
            ? '<span class="text-amber-300 font-mono text-xs">' + esc(changed.join(", ")) + "</span>"
            : '<span class="text-xs">' + esc(r.message) + "</span>";
        return (
            '<tr class="hover:bg-slate-800/40">' +
            '<td class="px-6 py-3">' + badge(r.drift_status) + "</td>" +
            '<td class="px-6 py-3 font-mono"><a class="text-indigo-300 hover:underline" href="/diff/' + encodeURIComponent(r.ref) + '">' + esc(r.container_name || "—") + "</a>" +
            (r.container_id ? '<span class="block text-xs text-slate-500">' + esc(r.container_id.slice(0, 12)) + "</span>" : "") + "</td>" +
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

    function renderDiffPage(snapshot) {
        if (!W.diffRef) return;
        const record = snapshot.records.find((r) => r.ref === W.diffRef || r.container_name === W.diffRef || r.service_name === W.diffRef);
        const root = document.querySelector("[data-diff-ref]");
        if (!root) return;
        const previous = root.getAttribute("data-diff-status");
        const current = record ? record.drift_status : "gone";
        if (previous !== current) {
            toast("State of " + W.diffRef + " changed to " + current + "; reloading…", "sky");
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

    const CONFIRM = {
        revert: (ref) => "Recreate '" + ref + "' from the Git baseline? The current container will be stopped and removed.",
        kill: (ref) => "Stop and remove rogue container '" + ref + "'?",
        adopt: null,
    };

    window.wardenAction = async function (action, ref) {
        if (CONFIRM[action] && !window.confirm(CONFIRM[action](ref))) return;
        let body;
        if (action === "adopt") {
            const note = window.prompt("Optional note for the pull request body:", "");
            if (note === null) return;
            body = { container_id: ref, note: note || null };
        }
        toast((action === "adopt" ? "Opening pull request for " : action === "kill" ? "Removing " : "Reverting ") + ref + "…", "sky");
        try {
            const result = await post("/api/actions/" + action + "/" + encodeURIComponent(ref), body);
            toast(result.message, "emerald");
            if (result.pr_url) window.open(result.pr_url, "_blank");
        } catch (err) {
            toast(action + " failed: " + err.message, "rose");
        }
    };

    window.wardenWhitelist = async function (type, identifier) {
        const reason = window.prompt("Reason for whitelisting " + type.toLowerCase() + " '" + identifier + "':", "");
        if (reason === null) return;
        try {
            const entry = await post("/api/whitelist", { target_type: type, target_identifier: identifier, reason: reason });
            toast("Whitelisted " + entry.target_type.toLowerCase() + " " + entry.target_identifier, "emerald");
        } catch (err) {
            toast("whitelist failed: " + err.message, "rose");
        }
    };

    window.wardenWhitelistForm = function (event) {
        event.preventDefault();
        const form = event.target;
        post("/api/whitelist", {
            target_type: form.target_type.value,
            target_identifier: form.target_identifier.value.trim(),
            reason: form.reason.value.trim(),
        }).then(() => window.location.reload()).catch((err) => toast("whitelist failed: " + err.message, "rose"));
        return false;
    };

    window.wardenUnwhitelist = async function (id, identifier) {
        if (!window.confirm("Remove exemption for '" + identifier + "'?")) return;
        try {
            await post("/api/whitelist/" + id, undefined, "DELETE");
            window.location.reload();
        } catch (err) {
            toast("remove failed: " + err.message, "rose");
        }
    };

    window.wardenReconcile = async function () {
        toast("Reconciling…", "sky");
        try {
            const data = await post("/api/reconcile");
            apply(data);
            toast("Reconciled: " + Object.entries(data.snapshot.counts).filter(([, v]) => v).map(([k, v]) => k + "=" + v).join(" ") || "nothing to show", "emerald");
        } catch (err) {
            toast("reconcile failed: " + err.message, "rose");
        }
    };

    if (W.snapshot) renderChrome(W.snapshot);
    connect();
})();
