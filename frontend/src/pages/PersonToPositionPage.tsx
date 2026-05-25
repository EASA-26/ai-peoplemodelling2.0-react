import { useState, useEffect } from "react";
import axios from "axios";
import { Search, Loader2, Compass, AlertTriangle, ArrowRight, User, FileDown, FileSpreadsheet } from "lucide-react";
import { exportRowsToExcel, exportRowsToPdf } from "../lib/exportUtils";

export default function PersonToPositionPage() {
    const [people, setPeople] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    const [selectedName, setSelectedName] = useState("");
    const [searchQuery, setSearchQuery] = useState("");
    const [topK, setTopK] = useState(5);

    const [recommendations, setRecommendations] = useState<any[]>([]);
    const [isGenerating, setIsGenerating] = useState(false);

    useEffect(() => {
        const fetchTools = async () => {
            try {
                const res = await axios.get("/api/uploads/people-model");
                setPeople(res.data);
            } catch (err) {
                console.error(err);
            } finally {
                setLoading(false);
            }
        };
        fetchTools();
    }, []);

    if (loading) {
        return <div className="p-8 text-center text-slate-500">Loading directory...</div>;
    }

    const uniqueNames = Array.from(new Set(people.map(p => p.Name || p.Nama || p["Employee Name"]).filter(Boolean))).sort();
    const filteredNames = uniqueNames.filter(n => String(n).toLowerCase().includes(searchQuery.toLowerCase()));

    const handleGenerate = async () => {
        if (!selectedName) return;
        setIsGenerating(true);
        setRecommendations([]);
        try {
            const payload = { employee_name: selectedName, top_k: topK };
            const res = await axios.post("/api/recommendations/person-to-position", payload);
            setRecommendations(res.data.recommendations || []);
        } catch (err) {
            console.error(err);
            alert("Failed to contact Databricks Backend.");
        } finally {
            setIsGenerating(false);
        }
    };

    const topKLabel = topK <= 0 ? "All matched JDs" : `Top ${topK}`;

    const exportRows = recommendations.map((rec, i) => ({
        Rank: rec.rank || i + 1,
        Position: rec.position_title || "",
        "Fit Reason": rec.fit_reason || "",
        "Gap / Risks": rec.risks || "",
        "Development Plan": rec.development_plan || "",
        "AI Detailed Reasoning": rec.ai_reasoning || "",
    }));

    return (
        <div className="max-w-7xl mx-auto space-y-6">
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Person ➜ Position Recommendation</h1>
                    <p className="text-slate-500 mt-1">Discover ideal future positions for a specific employee based on their talent profile and JD matching.</p>
                </div>
                <div className="flex flex-wrap gap-3">
                    <button
                        onClick={() => exportRowsToPdf(`Person to Position Recommendation - ${selectedName || "Employee"}`, `person_to_position_${selectedName || "employee"}.pdf`, exportRows)}
                        disabled={recommendations.length === 0}
                        className="bg-white text-slate-700 hover:bg-slate-50 font-medium py-2 px-4 rounded-lg flex items-center gap-2 transition-colors border border-slate-200 text-sm disabled:opacity-50"
                    >
                        <FileDown className="w-4 h-4" /> Export PDF
                    </button>
                    <button
                        onClick={() => exportRowsToExcel(`person_to_position_${selectedName || "employee"}.xlsx`, "Recommendations", exportRows)}
                        disabled={recommendations.length === 0}
                        className="bg-white text-slate-700 hover:bg-slate-50 font-medium py-2 px-4 rounded-lg flex items-center gap-2 transition-colors border border-slate-200 text-sm disabled:opacity-50"
                    >
                        <FileSpreadsheet className="w-4 h-4" /> Export Excel
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm h-fit">
                    <div className="mb-4">
                        <h3 className="font-semibold text-slate-800 flex items-center gap-2">
                            <User className="w-5 h-5 text-indigo-500" /> Employee Search
                        </h3>
                        <p className="text-xs text-slate-500 mt-1">Select an employee to run multi-JD mapping and choose how many matched JDs to rank.</p>
                    </div>

                    <div className="space-y-4">
                        <div className="relative">
                            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
                            <input
                                type="text"
                                placeholder="Filter names..."
                                className="w-full pl-9 pr-3 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                            />
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-slate-700 mb-1">JD Ranking Scope</label>
                            <select
                                className="w-full border border-slate-300 rounded-lg p-2.5 text-sm bg-slate-50 focus:ring-2 focus:ring-blue-500 outline-none"
                                value={String(topK)}
                                onChange={(e) => setTopK(Number(e.target.value))}
                            >
                                <option value="5">Top 5 matched JDs</option>
                                <option value="10">Top 10 matched JDs</option>
                                <option value="20">Top 20 matched JDs</option>
                                <option value="0">All matched JDs</option>
                            </select>
                        </div>

                        <div className="h-64 overflow-y-auto border border-slate-200 rounded-lg bg-slate-50 p-2 space-y-1">
                            {filteredNames.length === 0 ? (
                                <div className="p-3 text-center text-sm text-slate-400">No names match.</div>
                            ) : (
                                filteredNames.map(name => (
                                    <button
                                        key={name as string}
                                        onClick={() => setSelectedName(name as string)}
                                        className={`w-full text-left px-3 py-2 text-sm rounded-md transition-colors ${selectedName === name
                                            ? "bg-indigo-600 text-white font-medium shadow-sm"
                                            : "text-slate-700 hover:bg-slate-200"
                                            }`}
                                    >
                                        {name as string}
                                    </button>
                                ))
                            )}
                        </div>

                        <button
                            onClick={handleGenerate}
                            disabled={!selectedName || isGenerating}
                            className="w-full mt-2 bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
                        >
                            {isGenerating ? <Loader2 className="w-5 h-5 animate-spin" /> : <Compass className="w-5 h-5" />}
                            {isGenerating ? `Ranking ${topKLabel}...` : `Map Future Roles (${topKLabel})`}
                        </button>
                    </div>
                </div>

                <div className="col-span-1 lg:col-span-2 space-y-4">
                    {recommendations.length === 0 && !isGenerating ? (
                        <div className="bg-white border text-center border-slate-200 rounded-xl p-12 shadow-sm text-slate-500 h-full flex flex-col items-center justify-center">
                            <Compass className="w-12 h-12 text-slate-300 mb-4" />
                            <p className="font-medium text-slate-600">Select an employee and map their future.</p>
                        </div>
                    ) : (
                        recommendations.map((rec, i) => (
                            <div key={i} className="bg-white border-l-4 border-l-indigo-500 border-t border-r border-b border-slate-200 rounded-r-xl p-6 shadow-sm">
                                <div className="flex justify-between items-start">
                                    <div>
                                        <span className="text-xs font-bold uppercase tracking-wider text-indigo-600 bg-indigo-50 px-2 py-1 rounded mb-2 inline-block">
                                            Rank {rec.rank || i + 1}
                                        </span>
                                        <h3 className="text-lg font-bold text-slate-900">{rec.position_title}</h3>
                                    </div>
                                </div>

                                <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div className="bg-slate-50 rounded-lg p-3 text-sm border border-slate-100">
                                        <span className="text-emerald-700 font-semibold flex items-center gap-1 mb-1">
                                            <ArrowRight className="w-4 h-4" /> Fit Reason
                                        </span>
                                        <p className="text-slate-600">{rec.fit_reason || "Strong overlap with core competencies."}</p>
                                    </div>
                                    <div className="bg-slate-50 rounded-lg p-3 text-sm border border-slate-100">
                                        <span className="text-amber-700 font-semibold flex items-center gap-1 mb-1">
                                            <AlertTriangle className="w-4 h-4" /> Gap / Risks
                                        </span>
                                        <p className="text-slate-600">{rec.risks || "Minor differences in specific project scopes."}</p>
                                    </div>
                                </div>

                                <div className="mt-4 text-sm text-slate-700 border-t border-slate-100 pt-4">
                                    <strong>AI Detailed Reasoning:</strong> {rec.ai_reasoning}
                                </div>
                                <div className="mt-2 text-sm text-slate-700">
                                    <strong>Development Plan:</strong> {rec.development_plan || "On the job training recommended."}
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
}
