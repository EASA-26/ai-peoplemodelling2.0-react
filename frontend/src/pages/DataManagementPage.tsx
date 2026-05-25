import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { UploadCloud, Trash2, FileText, Users, Briefcase, FileBadge, RefreshCw, Database, ClipboardList } from "lucide-react";


export default function DataManagementPage() {
    const [activeTab, setActiveTab] = useState("jds");
    const [loading, setLoading] = useState(false);
    const [items, setItems] = useState<any[]>([]);
    const [message, setMessage] = useState<string>("");

    const tabs = useMemo(() => ([
        { id: "jds", label: "Job Descriptions", icon: <FileText className="w-4 h-4" /> },
        { id: "profiles", label: "Position Profiles", icon: <Briefcase className="w-4 h-4" /> },
        { id: "people", label: "People Model", icon: <Users className="w-4 h-4" /> },
        { id: "talent", label: "Talent Cards", icon: <FileBadge className="w-4 h-4" /> },
        { id: "audit", label: "Audit Log", icon: <ClipboardList className="w-4 h-4" /> }
    ]), []);

    const endpointMap: Record<string, string> = {
        jds: "job-descriptions",
        profiles: "position-profiles",
        people: "people-model",
        talent: "talent-cards",
        audit: "audit-logs",
    };

    const fetchItems = async (tab: string) => {
        setLoading(true);
        try {
            const url = tab === "audit" ? `/api/${endpointMap[tab]}` : `/api/uploads/${endpointMap[tab]}`;
            const res = await axios.get(url);
            setItems(Array.isArray(res.data) ? res.data : []);
        } catch (e: any) {
            console.error(e);
            setItems([]);
            setMessage(e?.response?.data?.detail || "Unable to load records.");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchItems(activeTab);
    }, [activeTab]);

    const uploadSingleFileInChunks = async (category: string, file: File) => {
        const uploadId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const totalChunks = Math.ceil(file.size / 1048576) || 1;

        for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
            const start = chunkIndex * 1048576;
            const end = Math.min(start + 1048576, file.size);
            const blob = file.slice(start, end);
            const fd = new FormData();
            fd.append("upload_id", uploadId);
            fd.append("filename", file.name);
            fd.append("chunk_index", String(chunkIndex));
            fd.append("total_chunks", String(totalChunks));
            fd.append("chunk", blob, file.name);

            const res = await fetch(`/api/uploads/chunk/${category}`, {
                method: "POST",
                body: fd,
                credentials: "same-origin",
            });

            if (!res.ok) {
                let detail = `Chunk upload failed for ${file.name}`;
                try {
                    const data = await res.json();
                    detail = data?.detail || data?.message || detail;
                } catch {
                    // ignore json parse failure
                }
                throw new Error(detail);
            }
        }

        return { upload_id: uploadId, filename: file.name, total_chunks: totalChunks };
    };

    const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const files = e.target.files ? Array.from(e.target.files) : [];
        if (files.length === 0) return;

        setLoading(true);
        setMessage("Uploading files...");
        const category = endpointMap[activeTab];
        if (activeTab === "audit") return;

        try {
            const uploadedMeta = [];
            for (const file of files) {
                uploadedMeta.push(await uploadSingleFileInChunks(category, file));
            }

            const finalizeRes = await fetch(`/api/uploads/finalize/${category}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ uploads: uploadedMeta }),
                credentials: "same-origin",
            });

            let finalizeData: any = {};
            try {
                finalizeData = await finalizeRes.json();
            } catch {
                finalizeData = {};
            }

            if (!finalizeRes.ok) {
                throw new Error(finalizeData?.detail || "Finalize upload failed.");
            }

            const details = Array.isArray(finalizeData?.details) && finalizeData.details.length
                ? ` ${finalizeData.details.join(" | ")}`
                : "";
            const errors = Array.isArray(finalizeData?.errors) && finalizeData.errors.length
                ? ` Warnings: ${finalizeData.errors.join(" | ")}`
                : "";
            setMessage(`${finalizeData?.message || "Upload successful."}${details}${errors}`);
            await fetchItems(activeTab);
            if (activeTab === "jds" || activeTab === "talent") {
                setTimeout(() => fetchItems(activeTab), 4000);
            }
        } catch (err: any) {
            setMessage(`Upload failed: ${err?.message || "Network error"}`);
        } finally {
            e.target.value = "";
            setLoading(false);
        }
    };

    const handleDelete = async (id: number) => {
        if (activeTab === "audit") return;
        if (!window.confirm("Delete this item?")) return;
        try {
            await axios.delete(`/api/uploads/${endpointMap[activeTab]}/${id}`);
            setMessage("Record deleted.");
            fetchItems(activeTab);
        } catch (e: any) {
            setMessage(e?.response?.data?.detail || "Delete failed.");
        }
    };

    const handleDeleteAll = async () => {
        if (activeTab === "audit") return;
        if (!window.confirm("Are you sure you want to delete ALL items in this category?")) return;
        try {
            await axios.delete(`/api/uploads/${endpointMap[activeTab]}`);
            setMessage("All records deleted.");
            fetchItems(activeTab);
        } catch (e: any) {
            setMessage(e?.response?.data?.detail || "Delete all failed.");
        }
    };

    const accept = activeTab === "jds" || activeTab === "talent" ? ".pdf" : ".xlsx,.xls";
    const multi = activeTab === "jds" || activeTab === "talent";
    const isAuditTab = activeTab === "audit";

    return (
        <div className="space-y-6 max-w-7xl mx-auto">
            <div>
                <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Data Management</h1>
                <p className="text-slate-500 mt-1">Manage uploaded source data for matching, analytics, and AI recommendations.</p>
            </div>

            <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                <div className="flex border-b border-slate-200 bg-slate-50 overflow-x-auto">
                    {tabs.map(t => (
                        <button
                            key={t.id}
                            onClick={() => setActiveTab(t.id)}
                            className={`flex items-center gap-2 px-6 py-4 font-medium text-sm transition-colors border-b-2 whitespace-nowrap ${activeTab === t.id
                                    ? "border-blue-600 text-blue-700 bg-white"
                                    : "border-transparent text-slate-500 hover:text-slate-700 hover:bg-slate-100"
                                }`}
                        >
                            {t.icon} {t.label}
                        </button>
                    ))}
                </div>

                <div className="p-6 border-b border-slate-100 bg-white flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                    <div className="flex-1">
                        <h3 className="font-semibold text-slate-800">
                            {tabs.find(t => t.id === activeTab)?.label}
                        </h3>
                        <p className="text-xs text-slate-500 mt-1">
                            Upload and manage source data required for AI pipeline matching.
                        </p>
                        {message && (
                            <div className="mt-3 text-sm rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-slate-700">
                                {message}
                            </div>
                        )}
                    </div>
                    <div className="flex items-center gap-3 flex-wrap">
                        {!isAuditTab && <label className="cursor-pointer bg-blue-50 text-blue-700 hover:bg-blue-100 font-medium py-2 px-4 rounded-lg flex items-center gap-2 transition-colors border border-blue-200 text-sm">
                            <UploadCloud className="w-4 h-4" />
                            Upload New
                            <input
                                type="file"
                                multiple={multi}
                                accept={accept}
                                className="hidden"
                                onChange={handleUpload}
                            />
                        </label>}
                        <button onClick={() => fetchItems(activeTab)} className="bg-white text-slate-700 hover:bg-slate-50 font-medium py-2 px-4 rounded-lg flex items-center gap-2 transition-colors border border-slate-200 text-sm">
                            <RefreshCw className="w-4 h-4" /> Refresh
                        </button>
                        {!isAuditTab && <button onClick={handleDeleteAll} className="bg-slate-50 text-red-600 hover:bg-red-50 hover:border-red-200 font-medium py-2 px-4 rounded-lg flex items-center gap-2 transition-colors border border-slate-200 text-sm">
                            <Trash2 className="w-4 h-4" /> Drop All
                        </button>}
                    </div>
                </div>

                <div className="p-0">
                    {loading ? (
                        <div className="p-12 text-center text-slate-400">Loading records...</div>
                    ) : items.length === 0 ? (
                        <div className="p-16 text-center text-slate-400 flex flex-col items-center">
                            <Database className="w-12 h-12 mb-4 text-slate-200" />
                            <p>No records found in current storage.</p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="w-full text-left border-collapse">
                                <thead>
                                    {isAuditTab ? (
                                        <tr className="bg-slate-50 border-b border-slate-200 text-xs uppercase tracking-wider text-slate-500">
                                            <th className="p-4 font-semibold">Time</th>
                                            <th className="p-4 font-semibold">User</th>
                                            <th className="p-4 font-semibold">Module</th>
                                            <th className="p-4 font-semibold">Action</th>
                                            <th className="p-4 font-semibold">Status</th>
                                            <th className="p-4 font-semibold">Details</th>
                                        </tr>
                                    ) : (
                                        <tr className="bg-slate-50 border-b border-slate-200 text-xs uppercase tracking-wider text-slate-500">
                                            <th className="p-4 font-semibold">ID</th>
                                            <th className="p-4 font-semibold">Identifier</th>
                                            <th className="p-4 font-semibold text-right">Actions</th>
                                        </tr>
                                    )}
                                </thead>
                                <tbody className="divide-y divide-slate-100 text-sm">
                                    {items.map((item, idx) => isAuditTab ? (
                                        <tr key={idx} className="hover:bg-slate-50 transition-colors align-top">
                                            <td className="p-4 text-slate-500 whitespace-nowrap">{item.created_at || "-"}</td>
                                            <td className="p-4 font-medium text-slate-700">{item.username || "admin"}</td>
                                            <td className="p-4 text-slate-700">{item.module || "-"}</td>
                                            <td className="p-4 text-slate-700">{item.action || "-"}</td>
                                            <td className="p-4">
                                                <span className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${item.status === "failed" ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700"}`}>
                                                    {item.status || "success"}
                                                </span>
                                            </td>
                                            <td className="p-4 text-slate-600 max-w-xl break-words">{item.details || item.entity_type || "-"}</td>
                                        </tr>
                                    ) : (
                                        <tr key={idx} className="hover:bg-slate-50 transition-colors">
                                            <td className="p-4 text-slate-500 w-16">#{item.id || item.DB_ID}</td>
                                            <td className="p-4 font-medium text-slate-700">
                                                {activeTab === "jds" && (item.original_filename || item.job_title || item.position)}
                                                {activeTab === "profiles" && (item["Position Title"] || item["position title"] || `Row ${idx + 1}`)}
                                                {activeTab === "people" && (item.Name || item.Nama || `Row ${idx + 1}`)}
                                                {activeTab === "talent" && (item.Name || `Card ${idx + 1}`)}
                                            </td>
                                            <td className="p-4 text-right">
                                                <button
                                                    onClick={() => handleDelete(item.id || item.DB_ID)}
                                                    className="text-slate-400 hover:text-red-500 p-1 rounded hover:bg-red-50 transition-colors"
                                                    title="Delete"
                                                >
                                                    <Trash2 className="w-4 h-4" />
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
