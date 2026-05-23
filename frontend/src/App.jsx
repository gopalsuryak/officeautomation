import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle, Archive, Bell, Building2, CalendarDays, Check,
  CheckCircle2, ChevronDown, Clock3, FileCheck2, FileText, Filter,
  FolderOpen, IndianRupee, LayoutDashboard, LockKeyhole, Mail,
  MessageCircle, MoreHorizontal, Phone, PieChart, Plus, Search,
  Settings, ShieldCheck, Stamp, UploadCloud, UserRound, UsersRound,
} from "lucide-react";
import {
  getMe, getTodayTasks, getClients, getGSTStatus,
  getDocuments, getApprovals, getReports,
  patchTaskStatus, postTaskEmail, postTaskWhatsApp,
  postApprove, postRequestChanges, postReject,
} from "./api.js";

// ── Constants ───────────────────────────────────────────────────────────────

const NAV_ITEMS = [
  { id: "today",     label: "Today",        icon: LayoutDashboard },
  { id: "clients",   label: "Clients",      icon: Building2 },
  { id: "gst",       label: "GST",          icon: FileCheck2 },
  { id: "tds",       label: "TDS",          icon: IndianRupee },
  { id: "itr",       label: "Income Tax",   icon: Stamp },
  { id: "mca",       label: "MCA",          icon: Archive },
  { id: "documents", label: "Documents",    icon: FolderOpen },
  { id: "approvals", label: "Approvals",    icon: ShieldCheck },
  { id: "reports",   label: "Reports",      icon: PieChart },
  { id: "settings",  label: "Settings",     icon: Settings },
];

const STATUS_STYLES = {
  "Pending from Client":    "border-amber-200 bg-amber-50 text-amber-800",
  "Pending with Staff":     "border-blue-200 bg-blue-50 text-blue-700",
  "Ready for Partner Review":"border-violet-200 bg-violet-50 text-violet-700",
  "Filed":                  "border-emerald-200 bg-emerald-50 text-emerald-700",
  "Overdue":                "border-rose-200 bg-rose-50 text-rose-700",
  "Draft Sent":             "border-slate-200 bg-slate-50 text-slate-700",
  "Data Received":          "border-cyan-200 bg-cyan-50 text-cyan-800",
};

// Roles that can approve filings
const PARTNER_ROLES = ["owner", "partner"];
// Roles that can update task status
const MANAGER_UP    = ["owner", "partner", "manager"];
const STAFF_UP      = ["owner", "partner", "manager", "senior", "assistant"];

// ── Helpers ─────────────────────────────────────────────────────────────────

function cx(...classes) { return classes.filter(Boolean).join(" "); }

function useAsync(fn, deps) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fn();
      setData(result);
    } catch (e) {
      setError(e.message || "Unknown error");
    } finally {
      setLoading(false);
    }
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { run(); }, [run]);
  return { data, loading, error, reload: run };
}

// ── Reusable atoms ───────────────────────────────────────────────────────────

function Pill({ children, status }) {
  return (
    <span className={cx(
      "inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold",
      STATUS_STYLES[status] || "border-slate-200 bg-slate-50 text-slate-700"
    )}>
      {children}
    </span>
  );
}

function Avatar({ name = "?" }) {
  const initials = String(name).split(" ").map(x => x[0]).join("").slice(0, 2).toUpperCase();
  return (
    <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-slate-100 text-xs font-bold text-slate-700">
      {initials}
    </div>
  );
}

function Skeleton({ className }) {
  return <div className={cx("animate-pulse rounded-xl bg-slate-100", className)} />;
}

function ErrorBanner({ message, onRetry }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-semibold text-rose-700">
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button onClick={onRetry} className="rounded-xl border border-rose-200 px-3 py-1 text-xs hover:bg-rose-100">
          Retry
        </button>
      )}
    </div>
  );
}

function EmptyState({ icon: Icon = FolderOpen, message = "Nothing here yet." }) {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-slate-400">
      <Icon className="h-10 w-10" />
      <p className="text-sm font-semibold">{message}</p>
    </div>
  );
}

// ── Top-level App ────────────────────────────────────────────────────────────

export default function App() {
  const [active, setActive]       = useState("today");
  const [selectedTask, setSelectedTask] = useState(null);

  const { data: me, loading: meLoading } = useAsync(getMe, []);
  const user = me?.user || {};
  const role = user.role || "viewer";

  const { data: approvalsData } = useAsync(getApprovals, []);
  const approvalCount = (approvalsData?.tasks || []).length;

  const pageTitle = NAV_ITEMS.find(n => n.id === active)?.label || "Today";
  const today = new Date().toLocaleDateString("en-IN", { weekday:"long", day:"numeric", month:"long", year:"numeric" });

  return (
    <div className="h-screen min-w-[1280px] overflow-hidden bg-[#f6f7f9] text-slate-950">
      <div className="flex h-full">

        {/* Sidebar */}
        <aside className="w-72 shrink-0 border-r border-slate-200 bg-white">
          <div className="flex h-20 items-center gap-3 border-b border-slate-200 px-5">
            <div className="grid h-11 w-11 place-items-center rounded-2xl bg-slate-950 text-white">
              <Stamp className="h-5 w-5" />
            </div>
            <div>
              <div className="text-base font-bold tracking-tight">CA Office Desk</div>
              <div className="text-xs text-slate-500">{user.firm_name || "Your Firm"}</div>
            </div>
          </div>

          <div className="px-3 py-4">
            {NAV_ITEMS.map(item => {
              const Icon = item.icon;
              const selected = active === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => setActive(item.id)}
                  className={cx(
                    "mb-1 flex h-11 w-full items-center gap-3 rounded-2xl px-3 text-left text-sm font-semibold transition",
                    selected ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
                  )}
                >
                  <Icon className="h-4 w-4" />
                  <span>{item.label}</span>
                  {item.id === "approvals" && approvalCount > 0 && (
                    <span className="ml-auto rounded-full bg-violet-100 px-2 py-0.5 text-xs text-violet-700">{approvalCount}</span>
                  )}
                </button>
              );
            })}
          </div>

          <AttentionBox />
        </aside>

        {/* Main */}
        <main className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-20 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6">
            <div>
              <h1 className="text-2xl font-bold tracking-tight">{pageTitle}</h1>
              <p className="mt-1 text-sm text-slate-500">{today}</p>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-[420px] items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 text-sm text-slate-500">
                <Search className="h-4 w-4" />
                <span>Search client, task, PAN, GSTIN, staff</span>
              </div>
              <button className="grid h-11 w-11 place-items-center rounded-2xl border border-slate-200 bg-white text-slate-600 hover:bg-slate-50">
                <Bell className="h-4 w-4" />
              </button>
              {STAFF_UP.includes(role) && (
                <button className="flex h-11 items-center gap-2 rounded-2xl bg-slate-950 px-5 text-sm font-bold text-white shadow-sm hover:bg-slate-800">
                  <Plus className="h-4 w-4" /> New Task
                </button>
              )}
              {meLoading ? (
                <Skeleton className="h-11 w-40 rounded-2xl" />
              ) : (
                <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2">
                  <Avatar name={user.name || "User"} />
                  <div>
                    <div className="text-sm font-bold leading-4">{user.name || "—"}</div>
                    <div className="text-xs text-slate-500 capitalize">{role}</div>
                  </div>
                  <ChevronDown className="h-4 w-4 text-slate-400" />
                </div>
              )}
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-hidden">
            {active === "today"     && <TodayScreen role={role} selectedTask={selectedTask} setSelectedTask={setSelectedTask} />}
            {active === "clients"   && <ClientsScreen />}
            {active === "gst"       && <GSTScreen />}
            {active === "tds"       && <ModuleScreen module="TDS" title="TDS Control" subtitle="Payments, returns, challan reconciliation, Form 16, and salary TDS follow-up." />}
            {active === "itr"       && <ModuleScreen module="Income Tax" title="Income Tax Work" subtitle="ITR data collection, computation, draft approval, filing, and e-verification." />}
            {active === "mca"       && <ModuleScreen module="MCA" title="MCA Work" subtitle="Board records, annual filing, resolutions, DIR forms, AOC-4, MGT-7, and sign-off." />}
            {active === "documents" && <DocumentsScreen />}
            {active === "approvals" && <ApprovalsScreen role={role} />}
            {active === "reports"   && <ReportsScreen />}
            {active === "settings"  && <SettingsScreen role={role} />}
          </div>
        </main>
      </div>
    </div>
  );
}

// ── Sidebar attention box ─────────────────────────────────────────────────

function AttentionBox() {
  const { data } = useAsync(getTodayTasks, []);
  const tasks = data?.tasks || [];
  const overdue   = tasks.filter(t => t.status === "Overdue").length;
  const clientPending = tasks.filter(t => t.status === "Pending from Client").length;
  const partnerReview = tasks.filter(t => t.status === "Ready for Partner Review").length;

  return (
    <div className="mx-4 mt-3 rounded-3xl border border-amber-200 bg-amber-50 p-4">
      <div className="flex items-center gap-2 text-sm font-bold text-amber-900">
        <AlertTriangle className="h-4 w-4" /> Due Attention
      </div>
      <div className="mt-3 space-y-2 text-sm text-amber-800">
        <div className="flex justify-between"><span>Overdue</span><b>{overdue}</b></div>
        <div className="flex justify-between"><span>Client pending</span><b>{clientPending}</b></div>
        <div className="flex justify-between"><span>Partner review</span><b>{partnerReview}</b></div>
      </div>
    </div>
  );
}

// ── Summary card ─────────────────────────────────────────────────────────

function SummaryCard({ label, value, icon: Icon, tone }) {
  const styles = {
    blue:   "bg-blue-50 text-blue-700",
    red:    "bg-rose-50 text-rose-700",
    amber:  "bg-amber-50 text-amber-800",
    violet: "bg-violet-50 text-violet-700",
  };
  return (
    <motion.div whileHover={{ y: -2 }} className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <div className={cx("grid h-12 w-12 place-items-center rounded-2xl", styles[tone])}>
          <Icon className="h-5 w-5" />
        </div>
        <MoreHorizontal className="h-4 w-4 text-slate-400" />
      </div>
      <div className="mt-5 text-3xl font-black tracking-tight text-slate-950">
        {value ?? <Skeleton className="h-8 w-12" />}
      </div>
      <div className="mt-1 text-sm font-semibold text-slate-500">{label}</div>
    </motion.div>
  );
}

// ── Today Screen ─────────────────────────────────────────────────────────

function TodayScreen({ role, selectedTask, setSelectedTask }) {
  const { data, loading, error, reload } = useAsync(getTodayTasks, []);
  const tasks = data?.tasks || [];

  useEffect(() => {
    if (tasks.length && !selectedTask) setSelectedTask(tasks[0]);
  }, [tasks]); // eslint-disable-line

  const buckets = [
    { label: "Due Today",           value: tasks.filter(t => t.due === "Today").length,             icon: CalendarDays, tone: "blue"   },
    { label: "Overdue",             value: tasks.filter(t => t.status === "Overdue").length,         icon: AlertTriangle, tone: "red"  },
    { label: "Pending from Client", value: tasks.filter(t => t.status === "Pending from Client").length, icon: MessageCircle, tone: "amber" },
    { label: "Partner Review",      value: tasks.filter(t => t.status === "Ready for Partner Review").length, icon: ShieldCheck, tone: "violet" },
  ];

  return (
    <div className="grid h-full grid-cols-[1fr_420px] overflow-hidden">
      <section className="min-w-0 overflow-y-auto p-6">
        <div className="grid grid-cols-4 gap-4">
          {buckets.map(b => <SummaryCard key={b.label} {...b} />)}
        </div>

        <div className="mt-6 rounded-[28px] border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
            <div>
              <h2 className="text-lg font-bold tracking-tight">Today's Work</h2>
              <p className="mt-1 text-sm text-slate-500">Clear status, owner, pending point, and next action</p>
            </div>
            <button className="flex h-10 items-center gap-2 rounded-2xl border border-slate-200 px-4 text-sm font-bold text-slate-700 hover:bg-slate-50">
              <Filter className="h-4 w-4" /> Filter
            </button>
          </div>

          {loading && (
            <div className="divide-y divide-slate-100">
              {[1,2,3].map(i => (
                <div key={i} className="grid grid-cols-[1fr_180px_170px_130px] items-center gap-4 px-5 py-4">
                  <div className="space-y-2"><Skeleton className="h-4 w-64" /><Skeleton className="h-3 w-40" /></div>
                  <Skeleton className="h-6 w-36 rounded-full" />
                  <Skeleton className="h-9 w-32 rounded-full" />
                  <Skeleton className="h-4 w-20 ml-auto" />
                </div>
              ))}
            </div>
          )}
          {error && <div className="p-5"><ErrorBanner message={error} onRetry={reload} /></div>}
          {!loading && !error && tasks.length === 0 && <EmptyState message="No tasks due today." />}
          {!loading && !error && tasks.length > 0 && (
            <div className="divide-y divide-slate-100">
              {tasks.map(task => (
                <button
                  key={task.id}
                  onClick={() => setSelectedTask(task)}
                  className={cx(
                    "grid w-full grid-cols-[1fr_180px_170px_130px] items-center gap-4 px-5 py-4 text-left transition hover:bg-slate-50",
                    selectedTask?.id === task.id && "bg-blue-50/70"
                  )}
                >
                  <div className="min-w-0">
                    <div className="mb-2 flex items-center gap-2">
                      <span className="rounded-lg bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600">{task.module}</span>
                      <span className="text-xs font-semibold text-slate-400">{task.id}</span>
                    </div>
                    <div className="truncate text-base font-bold text-slate-950">{task.work}</div>
                    <div className="mt-1 truncate text-sm text-slate-500">{task.client}</div>
                    <div className="mt-2 flex items-center gap-2 text-sm text-slate-600">
                      <AlertTriangle className="h-4 w-4 text-amber-600" />
                      <span className="truncate">{task.pending}</span>
                    </div>
                  </div>
                  <div><Pill status={task.status}>{task.status}</Pill></div>
                  <div className="flex items-center gap-2">
                    <Avatar name={task.staff} />
                    <div>
                      <div className="text-sm font-bold text-slate-800">{task.staff}</div>
                      <div className="text-xs text-slate-500">Staff</div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={cx("text-sm font-bold", task.due === "Overdue" ? "text-rose-700" : "text-slate-900")}>{task.due}</div>
                    <div className="mt-1 text-xs text-slate-500">{task.amount}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      {selectedTask
        ? <TaskPanel task={selectedTask} role={role} onStatusChange={task => setSelectedTask(task)} reload={reload} />
        : <div className="flex items-center justify-center border-l border-slate-200 bg-white text-sm text-slate-400">Select a task</div>
      }
    </div>
  );
}

// ── Task Panel ────────────────────────────────────────────────────────────

function TaskPanel({ task, role, onStatusChange, reload }) {
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const fileInput = useRef(null);

  function showToast(msg, type = "success") {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }

  async function updateStatus(newStatus, remarks = "") {
    setBusy(true);
    try {
      const res = await patchTaskStatus(task._internal_id, newStatus, remarks);
      onStatusChange?.({ ...task, status: newStatus });
      reload?.();
      showToast(`Status updated to "${newStatus}"`);
    } catch (e) {
      showToast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  function handleCall() {
    if (task.phone) { window.open(`tel:${task.phone}`); }
    else showToast("No phone number on file for this client.", "error");
  }

  function handleWhatsApp() {
    const num = task.phone ? task.phone.replace(/\D/g, "") : "";
    if (num) { window.open(`https://wa.me/91${num}`); }
    else showToast("No phone number on file for this client.", "error");
  }

  async function handleSendMail() {
    setBusy(true);
    try {
      await postTaskEmail(task._internal_id, { subject: `Update: ${task.work}`, to_client: true });
      showToast("Email queued for delivery.");
    } catch (e) { showToast(e.message, "error"); }
    finally { setBusy(false); }
  }

  const canUpdateStatus = STAFF_UP.includes(role);
  const canPartnerReview = MANAGER_UP.includes(role);

  return (
    <aside className="relative overflow-y-auto border-l border-slate-200 bg-white p-5">
      {toast && (
        <div className={cx(
          "mb-4 rounded-2xl px-4 py-3 text-sm font-semibold",
          toast.type === "error" ? "bg-rose-50 text-rose-700" : "bg-emerald-50 text-emerald-700"
        )}>{toast.msg}</div>
      )}

      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <span className="rounded-lg bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600">{task.module}</span>
            <span className="text-xs font-semibold text-slate-400">{task.id}</span>
          </div>
          <h2 className="text-xl font-black leading-7 tracking-tight text-slate-950">{task.work}</h2>
          <p className="mt-2 text-sm text-slate-500">{task.client}</p>
        </div>
        <button className="grid h-10 w-10 place-items-center rounded-2xl border border-slate-200 text-slate-500 hover:bg-slate-50">
          <MoreHorizontal className="h-4 w-4" />
        </button>
      </div>

      <div className="rounded-3xl border border-slate-200 bg-slate-50 p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm font-bold text-slate-700">Current Status</span>
          <Pill status={task.status}>{task.status}</Pill>
        </div>
        <div className="rounded-2xl bg-white p-4 text-sm font-semibold leading-6 text-slate-700">{task.pending}</div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3">
        <ActionButton icon={Phone}        label="Call Client" onClick={handleCall} />
        <ActionButton icon={MessageCircle} label="WhatsApp"   onClick={handleWhatsApp} />
        <ActionButton icon={UploadCloud}  label="Upload File" onClick={() => fileInput.current?.click()} />
        <ActionButton icon={Mail}         label="Send Mail"   onClick={handleSendMail} disabled={busy} />
      </div>
      {/* Hidden file input */}
      <input ref={fileInput} type="file" className="hidden" multiple
        onChange={e => { if (e.target.files.length) showToast(`${e.target.files.length} file(s) ready to upload — full upload coming soon.`); }} />

      <div className="mt-5 rounded-3xl border border-slate-200 p-4">
        <h3 className="mb-4 text-sm font-black text-slate-950">Next Steps</h3>
        <Step done label="Task created" />
        <Step done={!["Pending from Client","Overdue"].includes(task.status)} label="Documents received" />
        <Step done={["Ready for Partner Review","Filed"].includes(task.status)} label="Prepared by staff" />
        <Step done={task.status === "Filed"} label="Partner approved / filed" />
      </div>

      <div className="mt-5 rounded-3xl border border-slate-200 p-4">
        <h3 className="mb-4 text-sm font-black text-slate-950">Responsibility</h3>
        <PersonLine name={task.staff}   label="Staff handling" />
        <PersonLine name={task.partner} label="Partner review" />
      </div>

      {canUpdateStatus && (
        <div className="mt-5 rounded-3xl border border-slate-200 p-4">
          <h3 className="mb-4 text-sm font-black text-slate-950">Quick Update</h3>
          <div className="grid gap-2">
            <button
              disabled={busy}
              onClick={() => updateStatus("Data Received", "Documents received from client")}
              className="h-11 rounded-2xl bg-slate-950 px-4 text-sm font-bold text-white hover:bg-slate-800 disabled:opacity-50"
            >Mark as Data Received</button>
            {canPartnerReview && (
              <button
                disabled={busy}
                onClick={() => updateStatus("Ready for Partner Review", "Work complete, sent for partner review")}
                className="h-11 rounded-2xl border border-slate-200 px-4 text-sm font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >Send to Partner Review</button>
            )}
            {PARTNER_ROLES.includes(role) && (
              <button
                disabled={busy}
                onClick={() => updateStatus("Filed", "Approved and filed")}
                className="h-11 rounded-2xl border border-emerald-300 bg-emerald-50 px-4 text-sm font-bold text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
              >Mark as Filed</button>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}

function ActionButton({ icon: Icon, label, onClick, disabled }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="flex h-12 items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white text-sm font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
    >
      <Icon className="h-4 w-4" /> {label}
    </button>
  );
}

function Step({ done, label }) {
  return (
    <div className="mb-3 flex items-center gap-3 last:mb-0">
      <div className={cx("grid h-7 w-7 place-items-center rounded-full", done ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-400")}>
        <Check className="h-4 w-4" />
      </div>
      <span className={cx("text-sm font-semibold", done ? "text-slate-800" : "text-slate-400")}>{label}</span>
    </div>
  );
}

function PersonLine({ name = "—", label }) {
  return (
    <div className="mb-3 flex items-center gap-3 last:mb-0">
      <Avatar name={name} />
      <div>
        <div className="text-sm font-bold text-slate-900">{name}</div>
        <div className="text-xs text-slate-500">{label}</div>
      </div>
    </div>
  );
}

// ── Clients Screen ────────────────────────────────────────────────────────

function ClientsScreen() {
  const { data, loading, error, reload } = useAsync(getClients, []);
  const clients = data?.clients || [];

  const total   = clients.length;
  const gst     = clients.filter(c => c.gst && c.gst !== "No GST").length;
  const overdue = clients.filter(c => c.overdue > 0).length;
  const pending = clients.reduce((sum, c) => sum + (c.pending || 0), 0);

  return (
    <PageWrap title="Clients" subtitle="One row per client with pending work, overdue work, GST status, and owner.">
      <div className="grid grid-cols-5 gap-4">
        <SummaryCard label="Total Clients"  value={loading ? null : total}   icon={Building2}    tone="blue"   />
        <SummaryCard label="Active GST"     value={loading ? null : gst}     icon={FileCheck2}   tone="violet" />
        <SummaryCard label="ITR Clients"    value={loading ? null : "—"}     icon={Stamp}        tone="blue"   />
        <SummaryCard label="Pending Docs"   value={loading ? null : pending} icon={FolderOpen}   tone="amber"  />
        <SummaryCard label="Overdue"        value={loading ? null : overdue} icon={AlertTriangle} tone="red"   />
      </div>
      <div className="mt-6 rounded-[28px] border border-slate-200 bg-white shadow-sm">
        {loading && <LoadingRows cols={7} />}
        {error && <div className="p-5"><ErrorBanner message={error} onRetry={reload} /></div>}
        {!loading && !error && clients.length === 0 && <EmptyState message="No clients found." />}
        {!loading && !error && clients.length > 0 && (
          <SimpleTable
            columns={["Client", "Type", "GST", "Owner", "Pending", "Overdue", "Status"]}
            rows={clients.map(c => [c.name, c.pan, c.gst, c.owner, c.pending, c.overdue, c.status])}
            statusColIndex={6}
          />
        )}
      </div>
    </PageWrap>
  );
}

// ── GST Screen ────────────────────────────────────────────────────────────

function GSTScreen() {
  const { data, loading, error, reload } = useAsync(getGSTStatus, []);
  const rows = data?.rows || [];

  const pending   = rows.filter(r => !["Filed"].includes(r.gstr3b)).length;
  const review    = rows.filter(r => r.gstr3b === "Ready for Partner Review").length;
  const filed     = rows.filter(r => r.gstr3b === "Filed").length;
  const overdue   = rows.filter(r => r.gstr3b === "Overdue").length;

  return (
    <PageWrap title="GST Control" subtitle="GSTR-1, GSTR-3B, reconciliation, client pending list, and filing readiness.">
      <div className="grid grid-cols-4 gap-4">
        <SummaryCard label="GSTR-1 Pending" value={loading ? null : rows.filter(r => r.gstr1 !== "Filed").length} icon={FileCheck2}   tone="amber"  />
        <SummaryCard label="3B Review"       value={loading ? null : review}  icon={ShieldCheck}  tone="violet" />
        <SummaryCard label="Filed"           value={loading ? null : filed}   icon={CheckCircle2} tone="blue"   />
        <SummaryCard label="Overdue"         value={loading ? null : overdue} icon={AlertTriangle} tone="red"   />
      </div>
      <div className="mt-6 rounded-[28px] border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-200 px-5 py-4">
          <h2 className="text-lg font-black">GST Filing Status</h2>
        </div>
        {loading && <LoadingRows cols={7} />}
        {error && <div className="p-5"><ErrorBanner message={error} onRetry={reload} /></div>}
        {!loading && !error && rows.length === 0 && <EmptyState message="No GST tasks found." />}
        {!loading && !error && rows.length > 0 && (
          <table className="w-full border-collapse text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-[0.08em] text-slate-500">
              <tr>
                {["Client","GSTR-1","GSTR-3B","Books / 2B","Due","Staff","Action"].map(h => (
                  <th key={h} className={cx("px-5 py-3 font-bold", h === "Action" ? "text-right" : "text-left")}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.client} className="border-t border-slate-100 hover:bg-slate-50/70">
                  <td className="px-5 py-4 font-bold text-slate-950">{row.client}</td>
                  <td className="px-5 py-4"><Pill status={row.gstr1}>{row.gstr1}</Pill></td>
                  <td className="px-5 py-4"><Pill status={row.gstr3b}>{row.gstr3b}</Pill></td>
                  <td className="px-5 py-4 text-slate-600">{row.books}</td>
                  <td className={cx("px-5 py-4 font-bold", row.due === "Overdue" ? "text-rose-700" : "text-slate-700")}>{row.due}</td>
                  <td className="px-5 py-4 text-slate-600">{row.staff}</td>
                  <td className="px-5 py-4 text-right">
                    <button className="rounded-xl border border-slate-200 px-3 py-2 text-xs font-bold hover:bg-slate-50">Open</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </PageWrap>
  );
}

// ── Module Screen (TDS / ITR / MCA) ──────────────────────────────────────

function ModuleScreen({ module: moduleName, title, subtitle }) {
  const { data, loading, error, reload } = useAsync(getTodayTasks, []);
  const tasks = (data?.tasks || []).filter(t => t.module === moduleName || t.module?.startsWith(moduleName));

  return (
    <PageWrap title={title} subtitle={subtitle}>
      <div className="grid grid-cols-4 gap-4">
        <SummaryCard label="Due Today"       value={loading ? null : tasks.filter(t => t.due === "Today").length}           icon={CalendarDays}  tone="blue"   />
        <SummaryCard label="Pending Client"  value={loading ? null : tasks.filter(t => t.status === "Pending from Client").length} icon={MessageCircle} tone="amber"  />
        <SummaryCard label="Staff Working"   value={loading ? null : tasks.filter(t => t.status === "Pending with Staff").length}  icon={UsersRound}    tone="violet" />
        <SummaryCard label="Completed"       value={loading ? null : tasks.filter(t => t.status === "Filed").length}        icon={CheckCircle2}  tone="blue"   />
      </div>
      <div className="mt-6 rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
        {error && <ErrorBanner message={error} onRetry={reload} />}
        {!loading && tasks.length === 0 && <EmptyState message={`No ${moduleName} tasks found.`} />}
        {!loading && tasks.length > 0 && (
          <div className="grid grid-cols-3 gap-4">
            {tasks.slice(0, 9).map(task => (
              <div key={task.id} className="rounded-3xl border border-slate-200 bg-slate-50 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <span className="rounded-lg bg-white px-2 py-1 text-xs font-bold text-slate-500">{task.id}</span>
                  <Pill status={task.status}>{task.status}</Pill>
                </div>
                <h3 className="text-base font-black text-slate-950">{task.work}</h3>
                <p className="mt-1 text-sm text-slate-500">{task.client}</p>
                <p className="mt-4 text-sm font-semibold text-slate-700">{task.pending}</p>
                <div className="mt-4 flex items-center justify-between border-t border-slate-200 pt-3">
                  <span className="text-sm text-slate-500">{task.staff}</span>
                  <button className="rounded-xl bg-slate-950 px-3 py-2 text-xs font-bold text-white">Open</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </PageWrap>
  );
}

// ── Documents Screen ──────────────────────────────────────────────────────

function DocumentsScreen() {
  const { data, loading, error, reload } = useAsync(getDocuments, []);
  const docs = data?.documents || [];

  return (
    <PageWrap title="Documents" subtitle="Client-wise document requests, received files, missing files, and follow-up status.">
      <div className="grid grid-cols-4 gap-4">
        <SummaryCard label="Pending from Client" value={loading ? null : docs.filter(d => d.status === "Pending from Client").length} icon={FolderOpen}    tone="amber"  />
        <SummaryCard label="Received Today"      value={loading ? null : docs.filter(d => d.status === "Data Received").length}       icon={UploadCloud}   tone="blue"   />
        <SummaryCard label="Overdue Requests"    value={loading ? null : docs.filter(d => d.status === "Overdue").length}             icon={AlertTriangle} tone="red"    />
        <SummaryCard label="Ready for Work"      value={loading ? null : docs.filter(d => d.status === "Data Received").length}       icon={CheckCircle2}  tone="violet" />
      </div>
      <div className="mt-6 rounded-[28px] border border-slate-200 bg-white shadow-sm">
        {loading && <LoadingRows cols={6} />}
        {error && <div className="p-5"><ErrorBanner message={error} onRetry={reload} /></div>}
        {!loading && !error && docs.length === 0 && <EmptyState message="No pending document requests." />}
        {!loading && !error && docs.length > 0 && (
          <SimpleTable
            columns={["Client","Document","Asked","Owner","Status","Action"]}
            rows={docs.map(d => [d.client, d.item, d.asked, d.owner, d.status, "Follow-up"])}
            statusColIndex={4}
          />
        )}
      </div>
    </PageWrap>
  );
}

// ── Approvals Screen ──────────────────────────────────────────────────────

function ApprovalsScreen({ role }) {
  const { data, loading, error, reload } = useAsync(getApprovals, []);
  const tasks = data?.tasks || [];
  const [busy, setBusy] = useState({});
  const [toast, setToast] = useState(null);

  function showToast(msg, type = "success") {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3000);
  }

  async function doApprove(id) {
    setBusy(b => ({ ...b, [id]: true }));
    try {
      await postApprove(id);
      showToast("Approved and moved to Filed.");
      reload();
    } catch (e) { showToast(e.message, "error"); }
    finally { setBusy(b => ({ ...b, [id]: false })); }
  }

  async function doChanges(id) {
    const remarks = window.prompt("Describe the changes needed:");
    if (!remarks) return;
    setBusy(b => ({ ...b, [id]: true }));
    try {
      await postRequestChanges(id, remarks);
      showToast("Changes requested.");
      reload();
    } catch (e) { showToast(e.message, "error"); }
    finally { setBusy(b => ({ ...b, [id]: false })); }
  }

  async function doReject(id) {
    const remarks = window.prompt("Reason for rejection:");
    if (!remarks) return;
    setBusy(b => ({ ...b, [id]: true }));
    try {
      await postReject(id, remarks);
      showToast("Rejected.");
      reload();
    } catch (e) { showToast(e.message, "error"); }
    finally { setBusy(b => ({ ...b, [id]: false })); }
  }

  const canApprove = PARTNER_ROLES.includes(role);
  const canReview  = MANAGER_UP.includes(role);

  return (
    <PageWrap title="Approvals" subtitle="Work ready for partner or manager review before filing, sending, or final submission.">
      {toast && (
        <div className={cx(
          "mb-4 rounded-2xl px-4 py-3 text-sm font-semibold",
          toast.type === "error" ? "bg-rose-50 text-rose-700" : "bg-emerald-50 text-emerald-700"
        )}>{toast.msg}</div>
      )}
      <div className="grid grid-cols-3 gap-4">
        <SummaryCard label="Ready for Review" value={loading ? null : tasks.length}                        icon={ShieldCheck}   tone="violet" />
        <SummaryCard label="High Priority"    value={loading ? null : tasks.filter(t => t.priority === "High" || t.priority === "Urgent").length} icon={AlertTriangle} tone="red"    />
        <SummaryCard label="Approved Today"   value={loading ? null : "—"}                                icon={CheckCircle2}  tone="blue"   />
      </div>

      {loading && <div className="mt-6 grid grid-cols-2 gap-5"><LoadingCard /><LoadingCard /></div>}
      {error && <div className="mt-6"><ErrorBanner message={error} onRetry={reload} /></div>}
      {!loading && !error && tasks.length === 0 && (
        <div className="mt-6 rounded-[28px] border border-slate-200 bg-white p-10">
          <EmptyState icon={ShieldCheck} message="No items pending approval." />
        </div>
      )}
      {!loading && !error && tasks.length > 0 && (
        <div className="mt-6 grid grid-cols-2 gap-5">
          {tasks.map(task => (
            <div key={task.id} className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-start justify-between gap-4">
                <div>
                  <span className="rounded-lg bg-slate-100 px-2 py-1 text-xs font-bold text-slate-600">{task.module}</span>
                  <h3 className="mt-3 text-lg font-black tracking-tight text-slate-950">{task.work}</h3>
                  <p className="mt-1 text-sm text-slate-500">{task.client}</p>
                </div>
                <Pill status={task.status}>{task.status}</Pill>
              </div>
              <div className="rounded-2xl bg-slate-50 p-4 text-sm font-semibold text-slate-700">{task.pending}</div>

              {canApprove || canReview ? (
                <div className="mt-4 grid grid-cols-3 gap-3">
                  {canApprove && (
                    <button
                      disabled={busy[task._internal_id]}
                      onClick={() => doApprove(task._internal_id)}
                      className="h-11 rounded-2xl bg-slate-950 text-sm font-bold text-white disabled:opacity-50"
                    >Approve</button>
                  )}
                  {canReview && (
                    <button
                      disabled={busy[task._internal_id]}
                      onClick={() => doChanges(task._internal_id)}
                      className="h-11 rounded-2xl border border-slate-200 text-sm font-bold text-slate-700 disabled:opacity-50"
                    >Changes</button>
                  )}
                  {canReview && (
                    <button
                      disabled={busy[task._internal_id]}
                      onClick={() => doReject(task._internal_id)}
                      className="h-11 rounded-2xl border border-rose-200 text-sm font-bold text-rose-600 disabled:opacity-50"
                    >Reject</button>
                  )}
                </div>
              ) : (
                <p className="mt-4 text-sm text-slate-400">You don't have permission to approve this item.</p>
              )}
            </div>
          ))}
        </div>
      )}
    </PageWrap>
  );
}

// ── Reports Screen ────────────────────────────────────────────────────────

function ReportsScreen() {
  const { data, loading, error, reload } = useAsync(getReports, []);
  const r = data?.summary || {};

  return (
    <PageWrap title="Reports" subtitle="Partner view of office workload, collections, pending documents, overdue statutory work, and staff performance.">
      <div className="grid grid-cols-4 gap-4">
        <SummaryCard label="Monthly Completion" value={loading ? null : r.completion_pct ? `${r.completion_pct}%` : "—"} icon={PieChart}      tone="blue"   />
        <SummaryCard label="Staff Pending"       value={loading ? null : r.staff_pending  ?? "—"}  icon={UsersRound}    tone="violet" />
        <SummaryCard label="Client Pending"      value={loading ? null : r.client_pending ?? "—"}  icon={MessageCircle} tone="amber"  />
        <SummaryCard label="Overdue"             value={loading ? null : r.overdue        ?? "—"}  icon={AlertTriangle} tone="red"    />
      </div>
      <div className="mt-6 grid grid-cols-2 gap-6">
        <ReportCard title="Staff workload"    rows={r.staff_rows    || ["Loading…"]} />
        <ReportCard title="Statutory summary" rows={r.statutory_rows || ["Loading…"]} />
      </div>
    </PageWrap>
  );
}

// ── Settings Screen ───────────────────────────────────────────────────────

function SettingsScreen({ role }) {
  const isPartner = PARTNER_ROLES.includes(role);
  return (
    <PageWrap title="Settings" subtitle="Firm, users, roles, email, WhatsApp, document storage, templates, billing, and security.">
      {!isPartner && (
        <div className="mb-4 flex items-center gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-800">
          <AlertTriangle className="h-4 w-4" />
          Some settings are restricted to Partner role only.
        </div>
      )}
      <div className="grid grid-cols-3 gap-5">
        <SettingsCard title="Firm Profile"        icon={Building2}    text="Name, address, letterhead, logo, GSTIN, PAN, and branch details." />
        <SettingsCard title="Users & Roles"       icon={UsersRound}   text="Partner, manager, senior, assistant, viewer, and permission controls." locked={!isPartner} />
        <SettingsCard title="Email & WhatsApp"    icon={MessageCircle} text="Client communication accounts, templates, logs, and delivery checks." />
        <SettingsCard title="Task Templates"      icon={FileText}     text="GST, TDS, ITR, MCA, audit, payroll, and custom recurring templates." />
        <SettingsCard title="Document Storage"    icon={FolderOpen}   text="Client folders, naming rules, upload limits, and document categories." />
        <SettingsCard title="Security"            icon={LockKeyhole}  text="Two-factor login, password policy, audit log, and user access review." locked={!isPartner} />
      </div>
    </PageWrap>
  );
}

// ── Layout helpers ────────────────────────────────────────────────────────

function PageWrap({ title, subtitle, children }) {
  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-3xl font-black tracking-tight text-slate-950">{title}</h2>
          <p className="mt-2 text-sm text-slate-500">{subtitle}</p>
        </div>
        <div className="flex items-center gap-3">
          <button className="flex h-11 items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 text-sm font-bold text-slate-700 hover:bg-slate-50">
            <Filter className="h-4 w-4" /> Filter
          </button>
          <button className="flex h-11 items-center gap-2 rounded-2xl bg-slate-950 px-5 text-sm font-bold text-white hover:bg-slate-800">
            <Plus className="h-4 w-4" /> Add New
          </button>
        </div>
      </div>
      {children}
    </div>
  );
}

function SimpleTable({ columns, rows, statusColIndex }) {
  return (
    <table className="w-full border-collapse text-sm">
      <thead className="bg-slate-50 text-xs uppercase tracking-[0.08em] text-slate-500">
        <tr>
          {columns.map(col => <th key={col} className="px-5 py-3 text-left font-bold">{col}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} className="border-t border-slate-100 hover:bg-slate-50/70">
            {row.map((cell, j) => (
              <td key={j} className="px-5 py-4 font-semibold text-slate-700">
                {j === statusColIndex && STATUS_STYLES[cell]
                  ? <Pill status={cell}>{cell}</Pill>
                  : String(cell ?? "—")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ReportCard({ title, rows }) {
  return (
    <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="mb-4 text-lg font-black tracking-tight text-slate-950">{title}</h3>
      <div className="space-y-3">
        {rows.map(row => (
          <div key={row} className="flex items-center justify-between rounded-2xl bg-slate-50 px-4 py-3 text-sm font-bold text-slate-700">
            <span>{row}</span>
            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
          </div>
        ))}
      </div>
    </div>
  );
}

function SettingsCard({ title, icon: Icon, text, locked }) {
  return (
    <motion.div whileHover={{ y: -2 }} className={cx("rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm", locked && "opacity-60 cursor-not-allowed")}>
      <div className="mb-5 grid h-12 w-12 place-items-center rounded-2xl bg-slate-50 text-slate-700">
        <Icon className="h-5 w-5" />
      </div>
      <h3 className="text-lg font-black tracking-tight text-slate-950">{title}</h3>
      <p className="mt-2 text-sm font-medium leading-6 text-slate-500">{text}</p>
      {locked && <span className="mt-3 inline-block text-xs font-semibold text-amber-600">Partner only</span>}
    </motion.div>
  );
}

function LoadingRows({ cols = 5 }) {
  return (
    <div className="divide-y divide-slate-100">
      {[1,2,3].map(i => (
        <div key={i} className="flex items-center gap-4 px-5 py-4">
          {Array.from({ length: cols }).map((_, j) => (
            <Skeleton key={j} className="h-4 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

function LoadingCard() {
  return (
    <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm space-y-3">
      <Skeleton className="h-5 w-24 rounded-lg" />
      <Skeleton className="h-6 w-3/4" />
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-16 w-full rounded-2xl" />
      <div className="grid grid-cols-3 gap-3">
        <Skeleton className="h-11 rounded-2xl" /><Skeleton className="h-11 rounded-2xl" /><Skeleton className="h-11 rounded-2xl" />
      </div>
    </div>
  );
}
