/* DriftWarden control plane: live updates over Server-Sent Events, dark modals, chimes and operator actions. */
(function () {
    "use strict";

    const W = window.WARDEN || { snapshot: null, statusMeta: {}, overallMeta: {}, page: "" };
    const TONES = ["emerald", "amber", "sky", "rose", "violet", "slate"];
    const FLASH_KEY = "warden.flash";
    const CHIME_KEY = "warden.chimes";
    const AUTONOMOUS_PHRASE = "ENABLE AUTONOMOUS";

    function esc(text) {
        return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        })[c]);
    }

    /* ------------------------------------------------------------- toasts */

    function toastInto(hostId, message, tone, ttl) {
        const host = document.getElementById(hostId);
        if (!host) return;
        const el = document.createElement("div");
        el.className = "toast tone-" + (tone || "slate");
        el.textContent = message;
        host.appendChild(el);
        setTimeout(() => el.classList.add("show"), 10);
        setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 300); }, ttl || 6000);
    }

    function toast(message, tone) { toastInto("toasts", message, tone); }

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

    /* ------------------------------------------------------------- chimes */

    const chimes = { enabled: true, ctx: null };

    function chimeInit() {
        try { chimes.enabled = localStorage.getItem(CHIME_KEY) !== "off"; } catch (e) { /* ignore */ }
        const toggle = document.getElementById("chime-toggle");
        if (!toggle) return;
        const render = () => { toggle.textContent = chimes.enabled ? "🔔" : "🔕"; toggle.title = (chimes.enabled ? "Chimes on" : "Chimes muted") + " — click to toggle"; };
        render();
        toggle.addEventListener("click", () => {
            chimes.enabled = !chimes.enabled;
            try { localStorage.setItem(CHIME_KEY, chimes.enabled ? "on" : "off"); } catch (e) { /* ignore */ }
            render();
            if (chimes.enabled) playResolution();
        });
        /* Browsers only allow audio after a user gesture: unlock the context on the first interaction. */
        const unlock = () => { audioContext(); document.removeEventListener("pointerdown", unlock); document.removeEventListener("keydown", unlock); };
        document.addEventListener("pointerdown", unlock);
        document.addEventListener("keydown", unlock);
    }

    function audioContext() {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        if (!chimes.ctx) chimes.ctx = new Ctx();
        if (chimes.ctx.state === "suspended") chimes.ctx.resume().catch(() => {});
        return chimes.ctx;
    }

    /* Play a sequence of [frequencyHz, startOffsetSec, durationSec] notes with a soft envelope. */
    function playNotes(notes, type) {
        if (!chimes.enabled) return;
        const ctx = audioContext();
        if (!ctx || ctx.state !== "running") return;
        const master = ctx.createGain();
        master.gain.value = 0.18;
        master.connect(ctx.destination);
        const now = ctx.currentTime;
        notes.forEach(([freq, offset, duration]) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = type || "sine";
            osc.frequency.setValueAtTime(freq, now + offset);
            gain.gain.setValueAtTime(0.0001, now + offset);
            gain.gain.exponentialRampToValueAtTime(1, now + offset + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + duration);
            osc.connect(gain).connect(master);
            osc.start(now + offset);
            osc.stop(now + offset + duration + 0.05);
        });
    }

    /* Descending dual-tone warning: two notes falling a fifth, played twice. */
    function playDriftWarning() {
        playNotes([[880, 0, 0.22], [587.33, 0.22, 0.32], [830.61, 0.62, 0.22], [554.37, 0.84, 0.4]], "triangle");
    }

    /* Ascending bright chime: a major arpeggio up to the octave. */
    function playResolution() {
        playNotes([[523.25, 0, 0.16], [659.25, 0.14, 0.16], [783.99, 0.28, 0.16], [1046.5, 0.42, 0.5]], "sine");
    }

    const DRIFT_OVERALL = new Set(["drift_detected", "missing"]);

    function chimeForTransition(previousOverall, nextOverall) {
        if (!previousOverall || previousOverall === nextOverall) return;
        if (previousOverall === "in_sync" && DRIFT_OVERALL.has(nextOverall)) playDriftWarning();
        else if (nextOverall === "in_sync" && previousOverall !== "in_sync") playResolution();
    }

    /* -------------------------------------------------------------- modal */

    const modal = {
        root: null, title: null, message: null, body: null, form: null, confirm: null, cancel: null, resolve: null, validate: null,
    };

    function modalInit() {
        modal.root = document.getElementById("modal-root");
        if (!modal.root) return;
        modal.title = document.getElementById("modal-title");
        modal.message = document.getElementById("modal-message");
        modal.body = document.getElementById("modal-body");
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

    function modalValues() {
        const values = {};
        new FormData(modal.form).forEach((value, key) => { values[key] = String(value).trim(); });
        modal.body.querySelectorAll("input[type=radio]:checked").forEach((input) => { values[input.name] = input.value; });
        return values;
    }

    function modalSubmit() {
        for (const input of modal.form.querySelectorAll("[required]")) {
            if (!input.value.trim()) { input.focus(); input.classList.add("invalid"); return; }
        }
        const values = modalValues();
        if (modal.validate) {
            const problem = modal.validate(values);
            if (problem) {
                const field = modal.form.querySelector("[name=" + problem.field + "]");
                if (field) { field.classList.add("invalid"); field.focus(); }
                toast(problem.message, "rose");
                return;
            }
        }
        modalClose(values);
    }

    function modalClose(result) {
        if (!modal.root) return;
        modal.root.classList.add("hidden");
        modal.root.setAttribute("aria-hidden", "true");
        modal.validate = null;
        const resolve = modal.resolve;
        modal.resolve = null;
        if (resolve) resolve(result);
    }

    /**
     * Open the dark modal. Resolves with an object of input values (or {} when there are
     * no inputs) on Confirm, and with null on Cancel, Esc or a click outside.
     * options: { title, message, bodyHtml, inputs: [{name, label, placeholder, required, value}],
     *            confirmText, danger, validate(values) -> {field, message} | null, onRender(bodyEl) }
     */
    function wardenModal(options) {
        if (!modal.root) return Promise.resolve({});
        modal.title.textContent = options.title || "Confirm";
        modal.message.textContent = options.message || "";
        modal.body.innerHTML = options.bodyHtml || "";
        modal.form.innerHTML = (options.inputs || []).map((input) =>
            '<label class="block text-xs uppercase tracking-wider text-slate-400">' + esc(input.label || input.name) +
            '<input class="modal-input" name="' + esc(input.name) + '" placeholder="' + esc(input.placeholder || "") + '"' +
            (input.required ? " required" : "") + ' value="' + esc(input.value || "") + '" autocomplete="off"></label>'
        ).join("");
        modal.confirm.textContent = options.confirmText || "Confirm";
        modal.confirm.className = "btn-lg " + (options.danger ? "btn-rose" : "btn-emerald");
        modal.validate = options.validate || null;
        modal.root.classList.remove("hidden");
        modal.root.setAttribute("aria-hidden", "false");
        if (options.onRender) options.onRender(modal.body);
        const first = modal.form.querySelector("input");
        setTimeout(() => (first || modal.confirm).focus(), 20);
        return new Promise((resolve) => { modal.resolve = resolve; });
    }
    window.wardenModal = wardenModal;

    /* ------------------------------------------------------ impact preview */

    function findRecord(ref) {
        const records = (W.snapshot && W.snapshot.records) || [];
        return records.find((r) => r.ref === ref || r.container_name === ref || r.service_name === ref) || null;
    }

    function diffField(record, field) {
        return (record.diffs || []).find((d) => d.field === field) || null;
    }

    function shortSha() {
        const sha = W.snapshot && W.snapshot.baseline && W.snapshot.baseline.sha;
        return sha ? "@" + sha.slice(0, 7) : "";
    }

    /* Two-line impact summary: what is targeted, and which Docker operations will run. */
    function impactHtml(action, record) {
        if (!record) return "";
        const name = record.container_name || record.display_name || record.ref;
        const service = record.service_name ? " · service <code>" + esc(record.service_name) + "</code>" : "";
        const target = "<strong>Target:</strong> <code>" + esc(name) + "</code>" + service + " · state <code>" + esc(record.status) + "</code>";
        const ports = diffField(record, "ports");
        const image = diffField(record, "image");
        const livePorts = ports && ports.observed && ports.observed !== "(none)" ? " on port " + esc(ports.observed) : "";
        const gitPorts = ports && ports.expected && ports.expected !== "(none)" ? " with port " + esc(ports.expected) : "";
        const gitImage = image && image.expected ? " image <code>" + esc(image.expected) + "</code>" : (record.image ? " image <code>" + esc(record.image) + "</code>" : "");
        let mutation;
        if (action === "kill") {
            mutation = "Stop &amp; remove <code>" + esc(name) + "</code> (" + esc(record.image || "unknown image") + ")" + livePorts + ". Nothing is recreated.";
        } else if (record.drift_status === "awaiting_restart") {
            mutation = "Stop &amp; remove the unlabelled <code>" + esc(name) + "</code>" + livePorts + ", then recreate it with its Compose labels from Git <code>" + esc(shortSha()) + "</code>" + gitImage + gitPorts + ".";
        } else if (record.drift_status === "missing" && !record.container_id) {
            mutation = "Create and start <code>" + esc(record.display_name || record.ref) + "</code> from Git <code>" + esc(shortSha()) + "</code>" + gitImage + gitPorts + ".";
        } else {
            mutation = "Stop &amp; remove <code>" + esc(name) + "</code>" + livePorts + ", then recreate from Git <code>" + esc(shortSha()) + "</code>" + gitImage + gitPorts + ".";
        }
        return '<div class="impact"><p>' + target + "</p><p class=\"mt-1\"><strong>Docker:</strong> " + mutation + "</p></div>";
    }

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
        /* Every action is either Enforce (make the host match Git) or Adopt (make Git match the host). */
        if (r.actions.includes("revert")) actions += actionButton("revert", r.ref, "Enforce", "btn-rose");
        if (r.actions.includes("kill")) actions += actionButton("kill", r.ref, "Enforce", "btn-rose");
        if (r.actions.includes("adopt")) actions += actionButton("adopt", r.ref, "Adopt", "btn-emerald");
        if (r.actions.includes("adopt_deletion")) actions += actionButton("adopt_deletion", r.ref, "Adopt", "btn-emerald");
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

    const ORDER = ["rogue", "drifted", "missing", "awaiting_restart", "pending_adoption", "matched"];

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
        const modeBadge = document.getElementById("mode-badge");
        if (modeBadge && modeBadge.getAttribute("data-mode") !== snapshot.mode) {
            modeBadge.setAttribute("data-mode", snapshot.mode);
            setText("mode-text", snapshot.mode);
            modeBadge.className = "px-2.5 py-1 rounded-full border " + (snapshot.mode === "enforce" ? "border-rose-700 bg-rose-950 text-rose-200" : "border-slate-700 bg-slate-800 text-slate-300");
        }
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

    function apply(payload, announce) {
        const snapshot = payload.snapshot;
        const previousOverall = W.snapshot ? W.snapshot.overall : null;
        W.snapshot = snapshot;
        W.version = payload.version;
        renderChrome(snapshot);
        if (W.page === "dashboard") renderDashboard(snapshot);
        renderDiffPage(snapshot);
        /* Engine events are not toasted (visual clutter); the status pill and the chimes carry them. */
        if (announce) chimeForTransition(previousOverall, snapshot.overall);
    }

    /* --------------------------------------------------------------- SSE */

    function connect() {
        if (!window.EventSource) return;
        const source = new EventSource("/api/state/stream");
        const indicator = document.getElementById("live-indicator");
        let first = true;
        source.addEventListener("snapshot", (event) => {
            try { apply(JSON.parse(event.data), !first); } catch (err) { console.error("bad snapshot", err); }
            first = false;
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
            throw new Error(data.message || (Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail) || ("HTTP " + response.status));
        }
        return data;
    }

    const ACTIONS = {
        revert: {
            title: "Enforce",
            message: (ref) => "Make the host match Git: recreate '" + ref + "' exactly as declared in the baseline?",
            confirmText: "Enforce",
            danger: true,
            progress: (ref) => "Enforcing " + ref + "…",
            destructive: true,
            impact: true,
        },
        kill: {
            title: "Enforce",
            message: (ref) => "Make the host match Git: '" + ref + "' is not declared, so it will be stopped and removed. This cannot be undone from DriftWarden.",
            confirmText: "Enforce",
            danger: true,
            progress: (ref) => "Enforcing " + ref + "…",
            destructive: true,
            impact: true,
        },
        adopt: {
            title: "Adopt",
            message: (ref) => "Make Git match the host: synthesize YAML for '" + ref + "', cut a branch and open a pull request on the baseline repository. Environment values are written as ${KEY} placeholders.",
            inputs: [{ name: "note", label: "Note for the pull request (optional)", placeholder: "why this workload exists" }],
            confirmText: "Adopt",
            progress: (ref) => "Adopting " + ref + "…",
            destructive: false,
            endpoint: "adopt",
        },
        adopt_deletion: {
            title: "Adopt",
            message: (ref) => "Make Git match the host: open a pull request that removes service '" + ref + "' from the baseline, since it is no longer running here.",
            inputs: [{ name: "note", label: "Note for the pull request (optional)", placeholder: "why the service was retired" }],
            confirmText: "Adopt",
            progress: (ref) => "Adopting " + ref + "…",
            destructive: false,
            endpoint: "adopt-deletion",
        },
    };

    window.wardenAction = async function (action, ref) {
        const spec = ACTIONS[action];
        if (!spec) return;
        const record = findRecord(ref);
        const values = await wardenModal({
            title: spec.title,
            message: spec.message(ref),
            bodyHtml: spec.impact ? impactHtml(action, record) : "",
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
                navigatingAway = true;
                redirectHome(result.message, "emerald");
                return;
            }
            toast(result.message, "emerald");
        } catch (err) {
            toast(spec.title + " failed: " + err.message, "rose");
        }
    };

    /* ------------------------------------------------------ batch actions */

    const SCOPES = [
        { value: "all", label: "All workloads", statuses: ["rogue", "drifted", "missing", "awaiting_restart"], always: true },
        { value: "rogue", label: "Only rogue containers", statuses: ["rogue"] },
        { value: "drifted", label: "Only drifted containers", statuses: ["drifted"] },
        { value: "missing", label: "Only missing containers", statuses: ["missing"] },
    ];

    function affectedFor(scope) {
        const spec = SCOPES.find((s) => s.value === scope) || SCOPES[0];
        const records = (W.snapshot && W.snapshot.records) || [];
        return records.filter((r) => spec.statuses.includes(r.drift_status));
    }

    function batchBodyHtml(operation) {
        /* Only scopes that currently have matching workloads are offered; "All workloads" always is. */
        const visible = SCOPES.filter((s) => s.always || affectedFor(s.value).length > 0);
        const radios = visible.map((s, index) =>
            '<label class="modal-radio"><input type="radio" name="scope" value="' + s.value + '"' + (index === 0 ? " checked" : "") + "> " + esc(s.label) +
            ' <span class="ml-auto text-xs text-slate-400" data-scope-count="' + s.value + '">' + affectedFor(s.value).length + "</span></label>"
        ).join("");
        return '<div class="space-y-2">' + radios + "</div>" +
            '<p class="text-xs uppercase tracking-wider text-slate-400 mt-3">Affected workloads</p>' +
            '<div class="modal-list" id="batch-affected"></div>' +
            (operation === "adopt"
                ? '<p class="text-xs text-slate-500 mt-2">Rogue and drifted entries get adoption pull requests; missing services get deletion pull requests.</p>'
                : '<p class="text-xs text-slate-500 mt-2">Rogue containers are stopped and removed; drifted and missing services are recreated from Git.</p>');
    }

    function renderAffected(body, operation) {
        const scope = (body.querySelector("input[name=scope]:checked") || {}).value || "all";
        const list = body.querySelector("#batch-affected");
        const affected = affectedFor(scope);
        if (!affected.length) {
            list.innerHTML = '<span class="text-slate-500">Nothing in this scope.</span>';
        } else {
            list.innerHTML = affected.map((r) => {
                const verb = operation === "enforce"
                    ? (r.drift_status === "rogue" ? "enforce: remove" : "enforce: recreate from Git")
                    : (r.drift_status === "missing" ? "adopt: remove from Git" : "adopt: write to Git");
                return "<div>" + badge(r.drift_status) + ' <span class="ml-2">' + esc(r.display_name || r.ref) + '</span> <span class="text-slate-500">→ ' + verb + "</span></div>";
            }).join("");
        }
        modal.confirm.disabled = affected.length === 0;
        modal.confirm.style.opacity = affected.length ? "1" : "0.5";
    }

    window.wardenBatch = async function (operation) {
        const isEnforce = operation === "enforce";
        const values = await wardenModal({
            title: isEnforce ? "Enforce All" : "Adopt All",
            message: isEnforce
                ? "Bring every selected workload back to the Git baseline. Rogue containers are removed; drifted and missing services are recreated."
                : "Open pull requests for every selected workload so Git catches up with the host.",
            bodyHtml: batchBodyHtml(operation),
            inputs: isEnforce ? [] : [{ name: "note", label: "Note for the pull requests (optional)", placeholder: "batch adoption" }],
            confirmText: isEnforce ? "Enforce selected" : "Open pull requests",
            danger: isEnforce,
            onRender: (body) => {
                renderAffected(body, operation);
                body.querySelectorAll("input[name=scope]").forEach((input) => input.addEventListener("change", () => renderAffected(body, operation)));
            },
        });
        modal.confirm.disabled = false;
        modal.confirm.style.opacity = "1";
        if (values === null) return;
        const scope = values.scope || "all";
        toast((isEnforce ? "Enforcing " : "Adopting ") + scope + " workloads…", "sky");
        try {
            const result = await post("/api/actions/batch", { operation, scope, note: values.note || null });
            const failed = result.results.filter((r) => !r.ok);
            const prNote = result.pr_url ? " in one pull request" : "";
            toast((isEnforce ? "Enforce All" : "Adopt All") + ": " + result.succeeded + " succeeded, " + result.failed + " failed" + prNote, failed.length ? "amber" : "emerald");
            failed.slice(0, 3).forEach((r) => toast(r.target + ": " + r.message, "rose"));
        } catch (err) {
            toast("Batch failed: " + err.message, "rose");
        }
    };

    /* ----------------------------------------------------------- settings */

    window.wardenSaveSetting = async function (key) {
        const row = document.querySelector('[data-setting="' + key + '"]');
        if (!row) return;
        const input = row.querySelector(".setting-input");
        const value = String(input.value).trim();
        const current = row.getAttribute("data-value");
        if (value === current) { toast("No change for " + key, "slate"); return; }
        let confirmation = null;
        if (key === "warden_mode" && value === "enforce") {
            const values = await wardenModal({
                title: "Enable autonomous enforcement",
                message: "In enforce mode DriftWarden will, on its own, remove rogue containers and recreate drifted or missing services after the grace window. Type " + AUTONOMOUS_PHRASE + " to confirm.",
                inputs: [{ name: "confirmation", label: "Confirmation phrase", placeholder: AUTONOMOUS_PHRASE, required: true }],
                confirmText: "Enable autonomous mode",
                danger: true,
                validate: (v) => (v.confirmation === AUTONOMOUS_PHRASE ? null : { field: "confirmation", message: "Type exactly " + AUTONOMOUS_PHRASE }),
            });
            if (values === null) { input.value = current; return; }
            confirmation = values.confirmation;
        } else {
            const values = await wardenModal({
                title: "Change " + key,
                message: "Apply " + key + " = " + value + " now? The override is stored in SQLite and wins over .env until reset.",
                confirmText: "Apply",
            });
            if (values === null) { input.value = current; return; }
        }
        try {
            const view = await post("/api/settings", { key, value, confirmation }, "PUT");
            flash("Saved " + view.key + " = " + view.value, "emerald");
            window.location.reload();
        } catch (err) {
            toast("Save failed: " + err.message, "rose");
        }
    };

    window.wardenResetSetting = async function (key, defaultValue) {
        const values = await wardenModal({
            title: "Reset " + key,
            message: "Return " + key + " to its environment value (" + defaultValue + ")?",
            confirmText: "Reset",
        });
        if (values === null) return;
        try {
            await post("/api/settings", { key, value: defaultValue }, "PUT");
            flash("Reset " + key + " to " + defaultValue, "emerald");
            window.location.reload();
        } catch (err) {
            toast("Reset failed: " + err.message, "rose");
        }
    };

    /* ----------------------------------------------- whitelist and buffer */

    window.wardenWhitelist = async function (type, identifier) {
        const values = await wardenModal({
            title: "Whitelist " + type.toLowerCase(),
            message: "'" + identifier + "' will be exempt from drift alerts and autonomous enforcement until the exemption is revoked on Cluster Config.",
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
            title: "Revoke exemption",
            message: "'" + identifier + "' will be evaluated against the baseline again on the next cycle.",
            confirmText: "Revoke",
            danger: true,
        });
        if (values === null) return;
        try {
            const result = await post("/api/whitelist/" + id, undefined, "DELETE");
            flash(result.message, "emerald");
            window.location.reload();
        } catch (err) {
            toast("Revoke failed: " + err.message, "rose");
        }
    };

    window.wardenRevokeBuffer = async function (id, name, prNumber) {
        const values = await wardenModal({
            title: "Revoke buffer entry",
            message: "Close pull request #" + prNumber + " with a note that the adoption was revoked, evict '" + name + "' and every other buffered entry that shares that pull request, and re-evaluate them against the baseline?",
            confirmText: "Revoke and close PR",
            danger: true,
        });
        if (values === null) return;
        try {
            const result = await post("/api/buffer/" + id + "/revoke");
            flash(result.message, "emerald");
            window.location.reload();
        } catch (err) {
            toast("Revoke failed: " + err.message, "rose");
        }
    };

    /* Events Log: same Refresh button as the dashboard, then reload the server-rendered tables. */
    window.wardenRefreshPage = async function () {
        toast("Refreshing…", "sky");
        try {
            await post("/api/reconcile");
            window.location.reload();
        } catch (err) {
            toast("Refresh failed: " + err.message, "rose");
        }
    };

    window.wardenReconcile = async function () {
        toast("Reconciling…", "sky");
        try {
            const data = await post("/api/reconcile");
            apply(data, false);
            const summary = Object.entries(data.snapshot.counts).filter(([, v]) => v).map(([k, v]) => k + "=" + v).join(" ");
            toast("Reconciled: " + (summary || "nothing to show"), "emerald");
        } catch (err) {
            toast("Reconcile failed: " + err.message, "rose");
        }
    };

    modalInit();
    chimeInit();
    showFlash();
    if (W.snapshot) renderChrome(W.snapshot);
    connect();
})();
