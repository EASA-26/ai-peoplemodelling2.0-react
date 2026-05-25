import { useState, useEffect } from "react";
import axios from "axios";
import { Search, Loader2, Sparkles, CheckCircle, Users, Briefcase, FileDown, FileSpreadsheet, Bot, ClipboardCheck, Eye, X } from "lucide-react";
import { exportRowsToExcel, exportRowsToPdf } from "../lib/exportUtils";

const GENERIC_POSITION_TOKENS = new Set([
    "senior", "principal", "lead", "chief", "assistant", "associate", "engineer", "manager", "executive",
    "officer", "specialist", "project", "position", "job", "jd", "description", "hq", "site",
]);

const normalizeSearchText = (value: unknown) => String(value ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[-\u2010-\u2015_&@\/()+,.;:|\[\]{}]+/g, " ")
    .replace(/[^a-zA-Z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();

const tokenizeSearchText = (value: unknown) => normalizeSearchText(value).split(" ").filter(Boolean);

const getPositionBaseTitle = (position: any) => position?.["Position Title"] || position?.["position title"] || "";
const getPositionGrade = (position: any) => position?.["Position Grade"] || position?.Grade || "";

const POSITION_SPECIALTY_KEYS = [
    "Job Text", "job text", "Discipline", "discipline", "Department", "department", "Area", "area",
    "Specialization", "Specialisation", "Function", "Job Function", "Role", "Position Detail", "Position Details",
];

const getPositionSpecialtyText = (position: any) => {
    if (!position) return "";
    const parts = POSITION_SPECIALTY_KEYS
        .map(key => position?.[key])
        .filter(Boolean)
        .map(value => String(value).trim())
        .filter(Boolean);
    return Array.from(new Set(parts)).join(" ");
};

const getPositionMatchText = (position: any) => {
    const title = getPositionBaseTitle(position);
    const detail = getPositionSpecialtyText(position);
    if (!detail || normalizeSearchText(title).includes(normalizeSearchText(detail))) return title;
    return [title, detail].filter(Boolean).join(" - ");
};

const SPECIALTY_TOKEN_ALIASES: Record<string, string[]> = {
    scheduler: ["scheduler", "schedule", "scheduling", "planner", "planning"],
    schedule: ["schedule", "scheduler", "scheduling", "planner", "planning"],
    scheduling: ["scheduling", "scheduler", "schedule", "planner", "planning"],
    planner: ["planner", "planning", "schedule", "scheduler", "scheduling"],
    planning: ["planning", "planner", "schedule", "scheduler", "scheduling"],
};

const tokenMatches = (token: string, targetTokens: Set<string>) => {
    const aliases = SPECIALTY_TOKEN_ALIASES[token] || [token];
    return aliases.some(alias => targetTokens.has(alias));
};

const getProjectTokens = (projectName: unknown) => {
    const tokens = tokenizeSearchText(projectName).filter(token => !["projek", "project"].includes(token));
    const aliases: Record<string, string[]> = {
        nhep: ["nhep", "nenggiri"],
        nenggiri: ["nenggiri", "nhep"],
        hhfs: ["hhfs"],
        hess: ["hess"],
    };
    return Array.from(new Set(tokens.flatMap(token => aliases[token] || [token])));
};

const getGradeTokens = (value: unknown) => String(value ?? "").toUpperCase().match(/(?:CM|GM|M|E)\d{2}/g) || [];

const getJdSearchText = (jd: any) => [
    jd?.job_title,
    jd?.position,
    jd?.grade,
    jd?.original_filename,
    jd?.filepath,
].filter(Boolean).join(" ");

const getSpecialtyTokens = (value: unknown) => tokenizeSearchText(value)
    .filter(token => !GENERIC_POSITION_TOKENS.has(token) && !/^e\d{2}$/.test(token) && !/^m\d{2}$/.test(token));

const scoreJdMatch = (jd: any, position: any, projectName: string) => {
    const baseTitle = getPositionBaseTitle(position);
    const matchText = getPositionMatchText(position);
    const specialtyText = getPositionSpecialtyText(position);
    const grade = getPositionGrade(position);
    const normalizedBaseTitle = normalizeSearchText(baseTitle);
    const normalizedMatchText = normalizeSearchText(matchText);
    const baseTitleTokens = tokenizeSearchText(baseTitle);
    const matchTokens = tokenizeSearchText(matchText);
    const titleTokenSet = new Set(baseTitleTokens);
    const specialtyTokens = Array.from(new Set([
        ...getSpecialtyTokens(specialtyText),
        ...getSpecialtyTokens(matchText).filter(token => !baseTitleTokens.includes(token)),
    ]));

    const jdText = getJdSearchText(jd);
    const normalizedJdText = normalizeSearchText(jdText);
    const jdTokenSet = new Set(tokenizeSearchText(jdText));

    let score = 0;

    if (normalizedBaseTitle && normalizedJdText.includes(normalizedBaseTitle)) score += 60;
    if (normalizedMatchText && normalizedJdText.includes(normalizedMatchText)) score += 35;

    const titleOverlap = baseTitleTokens.filter(token => jdTokenSet.has(token)).length;
    if (baseTitleTokens.length > 0) score += (titleOverlap / baseTitleTokens.length) * 30;

    const matchOverlap = matchTokens.filter(token => tokenMatches(token, jdTokenSet)).length;
    if (matchTokens.length > 0) score += (matchOverlap / matchTokens.length) * 10;

    const specialtyOverlap = specialtyTokens.filter(token => tokenMatches(token, jdTokenSet)).length;
    if (specialtyTokens.length > 0) {
        if (specialtyOverlap > 0) {
            score += specialtyOverlap * 70;
            score += (specialtyOverlap / specialtyTokens.length) * 40;
        } else {
            score -= 90;
        }
    }

    const positionGradeTokens = getGradeTokens(grade);
    const jdGradeTokens = getGradeTokens([jd?.grade, jd?.job_title, jd?.position, jd?.original_filename].filter(Boolean).join(" "));
    if (positionGradeTokens.some(token => jdGradeTokens.includes(token))) score += 15;

    const projectTokens = getProjectTokens(projectName);
    if (projectTokens.length > 0) {
        const projectMatches = projectTokens.filter(token => jdTokenSet.has(token)).length;
        if (projectMatches > 0) score += 25 + (projectMatches * 5);
        const hasOtherProject = ["nhep", "nenggiri", "hhfs", "hess"].some(token => jdTokenSet.has(token) && !projectTokens.includes(token));
        if (hasOtherProject && projectMatches === 0) score -= 15;
    }

    if (titleTokenSet.size > 0 && specialtyTokens.length === 0 && titleOverlap === baseTitleTokens.length) score += 10;
    return score;
};

const findBestMatchingJd = (position: any, projectName: string, jdList: any[]) => {
    if (!position || !jdList.length) return null;
    const scored = jdList
        .map(jd => ({ jd, score: scoreJdMatch(jd, position, projectName) }))
        .filter(item => item.score > 0)
        .sort((a, b) => b.score - a.score || String(a.jd?.job_title || "").localeCompare(String(b.jd?.job_title || "")));
    return scored[0]?.jd || null;
};

const formatJdLabel = (jd: any) => jd ? `${jd.job_title || jd.position || jd.original_filename || "Untitled JD"} (${jd.grade || "No grade"})` : "Not selected";


export default function SuccessionPage() {
    const [positions, setPositions] = useState<any[]>([]);
    const [jds, setJds] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    const [selectedProject, setSelectedProject] = useState("");
    const [selectedPositionId, setSelectedPositionId] = useState("");
    const [selectedJdId, setSelectedJdId] = useState("");
    const [jdSelectionMode, setJdSelectionMode] = useState<"auto" | "manual">("auto");
    const [autoMatchedJdId, setAutoMatchedJdId] = useState("");

    const [recommendations, setRecommendations] = useState<any[]>([]);
    const [shortlistedCandidates, setShortlistedCandidates] = useState<any[]>([]);
    const [autoDetectedJd, setAutoDetectedJd] = useState<any | null>(null);
    const [isGenerating, setIsGenerating] = useState(false);
    const [isPreviewLoading, setIsPreviewLoading] = useState(false);
    const [isJdViewerOpen, setIsJdViewerOpen] = useState(false);
    const [isJdViewerLoading, setIsJdViewerLoading] = useState(false);
    const [jdViewerError, setJdViewerError] = useState("");
    const [jdViewerData, setJdViewerData] = useState<any | null>(null);

    useEffect(() => {
        const fetchData = async () => {
            try {
                const [posRes, jdRes] = await Promise.all([
                    axios.get("/api/uploads/position-profiles"),
                    axios.get("/api/uploads/job-descriptions")
                ]);
                setPositions(posRes.data);
                setJds(jdRes.data);

                const projects = Array.from(new Set(posRes.data.map((p: any) => p.Projek || p.Project))).filter(Boolean);
                if (projects.length > 0) setSelectedProject(projects[0] as string);
            } catch (err) {
                console.error(err);
            } finally {
                setLoading(false);
            }
        };
        fetchData();
    }, []);

    const availablePositions = positions.filter(p => (p.Projek || p.Project) === selectedProject);
    const selectedPosition = positions.find(p => String(p.DB_ID) === selectedPositionId);

    useEffect(() => {
        if (!selectedPositionId || jds.length === 0) {
            setAutoMatchedJdId("");
            if (jdSelectionMode === "auto") setSelectedJdId("");
            return;
        }

        const selectedPos = positions.find((p: any) => String(p.DB_ID) === selectedPositionId);
        const best = findBestMatchingJd(selectedPos, selectedProject, jds);
        const bestId = best?.id ? String(best.id) : "";
        setAutoMatchedJdId(bestId);

        if (jdSelectionMode === "auto") {
            setSelectedJdId(current => current === bestId ? current : bestId);
        }
    }, [selectedProject, selectedPositionId, positions, jds, jdSelectionMode]);

    useEffect(() => {
        const loadPreview = async () => {
            if (!selectedPositionId || !selectedJdId) {
                setShortlistedCandidates([]);
                setAutoDetectedJd(null);
                return;
            }
            const pos = positions.find((p: any) => String(p.DB_ID) === selectedPositionId);
            const jd = jds.find((j: any) => String(j.id) === selectedJdId);
            if (!pos || !jd) return;

            setIsPreviewLoading(true);
            try {
                const res = await axios.post("/api/recommendations/succession-preview", {
                    project_name: pos.Projek || pos.Project || selectedProject || "Unknown",
                    position_title: getPositionMatchText(pos),
                    position_text: getPositionSpecialtyText(pos),
                    position_grade: pos["Position Grade"] || pos["Grade"] || "",
                    jd_filepath: jd.filepath,
                });
                setShortlistedCandidates(res.data.shortlist || []);
                setAutoDetectedJd(res.data.auto_detected_jd || null);
            } catch (err) {
                console.error(err);
                setShortlistedCandidates([]);
                setAutoDetectedJd(null);
            } finally {
                setIsPreviewLoading(false);
            }
        };
        loadPreview();
    }, [selectedProject, selectedPositionId, selectedJdId, positions, jds]);

    const handleViewSelectedJd = async () => {
        if (!selectedJdId) return;

        setIsJdViewerOpen(true);
        setIsJdViewerLoading(true);
        setJdViewerError("");
        setJdViewerData(null);

        try {
            const res = await axios.get(`/api/uploads/job-descriptions/${selectedJdId}/content`);
            setJdViewerData(res.data);
        } catch (err: any) {
            console.error(err);
            setJdViewerError(err?.response?.data?.detail || "Unable to load the selected JD content.");
        } finally {
            setIsJdViewerLoading(false);
        }
    };

    const closeJdViewer = () => {
        setIsJdViewerOpen(false);
        setJdViewerError("");
    };

    const handleGenerate = async () => {
        if (!selectedPositionId || !selectedJdId) return;

        setIsGenerating(true);
        setRecommendations([]);

        try {
            const pos = positions.find(p => String(p.DB_ID) === selectedPositionId);
            const jd = jds.find(j => String(j.id) === selectedJdId);

            const payload = {
                project_name: pos.Projek || pos.Project || "Unknown",
                position_title: getPositionMatchText(pos),
                position_text: getPositionSpecialtyText(pos),
                position_grade: pos["Position Grade"] || pos["Grade"] || "",
                budget: pos["Budget"] || "Not specified",
                jd_filepath: jd.filepath
            };

            const res = await axios.post("/api/recommendations/succession", payload);
            setRecommendations(res.data.results || []);
            setShortlistedCandidates(res.data.shortlist || shortlistedCandidates);
            setAutoDetectedJd(res.data.auto_detected_jd || autoDetectedJd);

        } catch (err) {
            console.error(err);
            alert("Failed to generate recommendations. Please ensure backend Databricks config is active.");
        } finally {
            setIsGenerating(false);
        }
    };

    if (loading) {
        return <div className="p-8 text-center text-slate-500">Loading data sources...</div>;
    }

    const selectedJd = jds.find((j: any) => String(j.id) === selectedJdId);
    const autoMatchedJd = jds.find((j: any) => String(j.id) === autoMatchedJdId);
    const displayAutoDetectedJd = autoDetectedJd || autoMatchedJd;

    const formatPositionLabel = (p: any) => {
        const title = p["Position Title"] || p["position title"] || "Untitled Position";
        const detail = p["Job Text"] || p["Discipline"] || p["Department"] || p["Area"] || "";
        return detail ? `${title} - ${detail}` : title;
    };
    const recommendationRows = recommendations.map((rec: any, i: number) => ({
        Rank: rec.Rank || rec.rank || i + 1,
        Name: rec.Name || rec.name || "",
        "Job Title": rec["Job Title"] || rec.job_title || "",
        "Succession Score": rec["Succession Score"] || rec.succession_score || "",
        "Predicted Readiness": rec["Predicted Readiness"] || rec.predicted_readiness || "",
        "AI Reasoning": rec["AI Reasoning"] || rec.ai_reasoning || "",
    }));

    return (
        <div className="space-y-6 max-w-7xl mx-auto">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900 tracking-tight">AI Powered Succession Recommendation</h1>
                    <p className="text-slate-500 mt-1">Match existing talent to open position profiles using Databricks Llama-4.</p>
                </div>
                <div className="flex flex-wrap gap-3">
                    <button
                        onClick={() => exportRowsToPdf(`Succession Recommendation - ${selectedPosition?.["Position Title"] || "Position"}`, `succession_recommendation_${selectedPositionId || "position"}.pdf`, recommendationRows)}
                        disabled={recommendations.length === 0}
                        className="bg-white text-slate-700 hover:bg-slate-50 font-medium py-2 px-4 rounded-lg flex items-center gap-2 transition-colors border border-slate-200 text-sm disabled:opacity-50"
                    >
                        <FileDown className="w-4 h-4" /> Export PDF
                    </button>
                    <button
                        onClick={() => exportRowsToExcel(`succession_recommendation_${selectedPositionId || "position"}.xlsx`, "Recommendations", recommendationRows)}
                        disabled={recommendations.length === 0}
                        className="bg-white text-slate-700 hover:bg-slate-50 font-medium py-2 px-4 rounded-lg flex items-center gap-2 transition-colors border border-slate-200 text-sm disabled:opacity-50"
                    >
                        <FileSpreadsheet className="w-4 h-4" /> Export Excel
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm h-fit">
                    <h3 className="font-semibold text-slate-800 mb-4 flex items-center gap-2">
                        <Search className="w-4 h-4 text-blue-500" /> Model Configuration
                    </h3>

                    <div className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-slate-700 mb-1">Target Project</label>
                            <select
                                className="w-full border border-slate-300 rounded-lg p-2.5 text-sm bg-slate-50 focus:ring-2 focus:ring-blue-500 outline-none"
                                value={selectedProject}
                                onChange={(e) => {
                                    setSelectedProject(e.target.value);
                                    setSelectedPositionId("");
                                    setSelectedJdId("");
                                    setJdSelectionMode("auto");
                                    setAutoMatchedJdId("");
                                    setJdViewerData(null);
                                    setRecommendations([]);
                                    setShortlistedCandidates([]);
                                    setAutoDetectedJd(null);
                                }}
                            >
                                <option value="">Select a project...</option>
                                {Array.from(new Set(positions.map(p => p.Projek || p.Project))).map((proj: any) => (
                                    <option key={proj} value={proj}>{proj}</option>
                                ))}
                            </select>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-slate-700 mb-1">Target Position</label>
                            <select
                                className="w-full border border-slate-300 rounded-lg p-2.5 text-sm bg-slate-50 focus:ring-2 focus:ring-blue-500 outline-none"
                                value={selectedPositionId}
                                onChange={(e) => {
                                    setSelectedPositionId(e.target.value);
                                    setSelectedJdId("");
                                    setJdSelectionMode("auto");
                                    setJdViewerData(null);
                                    setRecommendations([]);
                                    setShortlistedCandidates([]);
                                    setAutoDetectedJd(null);
                                }}
                                disabled={!selectedProject}
                            >
                                <option value="">Select position inside project...</option>
                                {availablePositions.map(p => (
                                    <option key={p.DB_ID} value={p.DB_ID}>{formatPositionLabel(p)}</option>
                                ))}
                            </select>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-slate-700 mb-1">Reference Job Description (JD)</label>
                            <select
                                className="w-full border border-slate-300 rounded-lg p-2.5 text-sm bg-slate-50 focus:ring-2 focus:ring-blue-500 outline-none"
                                value={selectedJdId}
                                onChange={(e) => {
                                    setSelectedJdId(e.target.value);
                                    setJdSelectionMode(e.target.value ? "manual" : "auto");
                                    setJdViewerData(null);
                                    setRecommendations([]);
                                }}
                            >
                                <option value="">Choose a JD to score candidates against...</option>
                                {jds.map(j => (
                                    <option key={j.id} value={j.id}>{formatJdLabel(j)}</option>
                                ))}
                            </select>
                            <p className="text-xs text-slate-500 mt-2">JD is auto-matched from the selected project and position. You can still choose another JD manually.</p>
                        </div>

                        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2 text-sm">
                            <div className="flex items-start gap-2 text-slate-700">
                                <ClipboardCheck className="w-4 h-4 mt-0.5 text-blue-500" />
                                <div>
                                    <div className="font-medium">Selected JD</div>
                                    <div>{formatJdLabel(selectedJd)}</div>
                                </div>
                            </div>
                            <div className="flex items-start gap-2 text-slate-700">
                                <Bot className="w-4 h-4 mt-0.5 text-emerald-500" />
                                <div>
                                    <div className="font-medium">Auto-detected JD</div>
                                    <div>{displayAutoDetectedJd ? formatJdLabel(displayAutoDetectedJd) : (isPreviewLoading ? "Detecting..." : "Not available")}</div>
                                </div>
                            </div>
                        </div>

                        <button
                            onClick={handleViewSelectedJd}
                            disabled={!selectedJdId || isJdViewerLoading}
                            className="w-full bg-white hover:bg-slate-50 text-slate-700 font-medium py-2.5 px-4 rounded-lg flex items-center justify-center gap-2 transition-colors border border-slate-200 disabled:opacity-50"
                        >
                            {isJdViewerLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Eye className="w-4 h-4" />}
                            View Selected JD
                        </button>

                        <button
                            onClick={handleGenerate}
                            disabled={!selectedPositionId || !selectedJdId || isGenerating}
                            className="w-full mt-2 bg-blue-600 hover:bg-blue-700 text-white font-medium py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
                        >
                            {isGenerating ? <Loader2 className="w-5 h-5 animate-spin" /> : <Sparkles className="w-5 h-5" />}
                            {isGenerating ? "Analyzing Candidates..." : "Generate AI Recommendations"}
                        </button>
                    </div>
                </div>

                <div className="lg:col-span-2 space-y-6">
                    <div className="bg-white border border-slate-200 rounded-xl p-6 shadow-sm">
                        <div className="flex items-center justify-between gap-4 mb-4">
                            <div>
                                <h3 className="text-lg font-semibold text-slate-900">Shortlisted Candidates Before AI Run</h3>
                                <p className="text-sm text-slate-500 mt-1">This list is prepared from the grade and JD education matching rules before the LLM ranking step.</p>
                            </div>
                            {isPreviewLoading && <Loader2 className="w-5 h-5 animate-spin text-slate-400" />}
                        </div>

                        {shortlistedCandidates.length === 0 ? (
                            <div className="rounded-lg border border-dashed border-slate-200 p-8 text-center text-slate-400">
                                {selectedPositionId && selectedJdId ? "No shortlist candidates found for the current setup." : "Select target position and JD to preview shortlist."}
                            </div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm whitespace-nowrap">
                                    <thead>
                                        <tr className="border-b border-slate-200 text-left text-slate-500 uppercase tracking-wide text-xs">
                                            <th className="py-3 pr-4">Rank</th>
                                            <th className="py-3 pr-4">Name</th>
                                            <th className="py-3 pr-4">Grade</th>
                                            <th className="py-3 pr-4">Job Title</th>
                                            <th className="py-3 pr-4">KPI</th>
                                            <th className="py-3 pr-4 text-right">Succession Score</th>
                                            <th className="py-3 pr-4">Years of Experience</th>
                                            <th className="py-3 pr-4">Basic Salary</th>
                                            <th className="py-3 pr-4">Basic Salary x15%</th>
                                            <th className="py-3 pr-4">Planned Retirement</th>
                                            <th className="py-3 pr-4">Date Demob</th>
                                            <th className="py-3 pr-4">Project Name</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {shortlistedCandidates.map((candidate, idx) => (
                                            <tr key={`${candidate.name}-${idx}`} className="border-b border-slate-100 last:border-b-0 align-top">
                                                <td className="py-3 pr-4 text-slate-500">{candidate.rank || idx + 1}</td>
                                                <td className="py-3 pr-4 font-medium text-slate-800">{candidate.name}</td>
                                                <td className="py-3 pr-4 text-slate-700">{candidate.grade}</td>
                                                <td className="py-3 pr-4 text-slate-700">{candidate.job_title}</td>
                                                <td className="py-3 pr-4 text-slate-700">{candidate.kpi}</td>
                                                <td className="py-3 pr-4 text-right font-semibold text-slate-800">{candidate.succession_score}</td>
                                                <td className="py-3 pr-4 text-slate-700">{candidate.years_of_experience}</td>
                                                <td className="py-3 pr-4 text-slate-700">{candidate.basic_salary}</td>
                                                <td className="py-3 pr-4 text-slate-700">{candidate.basic_salary_x15}</td>
                                                <td className="py-3 pr-4 text-slate-700">{candidate.planned_retirement}</td>
                                                <td className="py-3 pr-4 text-slate-700">{candidate.date_demob}</td>
                                                <td className="py-3 pr-4 text-slate-700">{candidate.project_name}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>

                    {recommendations.length === 0 && !isGenerating ? (
                        <div className="bg-slate-50 border border-slate-200 rounded-xl flex flex-col items-center justify-center p-12 text-slate-500 h-[400px]">
                            <Users className="w-12 h-12 text-slate-300 mb-4" />
                            <p className="font-medium text-slate-600">No recommendations generated yet</p>
                            <p className="text-sm mt-1 max-w-md text-center">Configure the model targets on the left and click generate to invoke the Databricks Llama-4 recommendations.</p>
                        </div>
                    ) : (
                        <>
                            {recommendations.map((rec, i) => (
                                <div key={i} className="bg-white border text-left border-slate-200 rounded-xl p-6 shadow-sm relative overflow-hidden">
                                    <div className="absolute top-0 left-0 w-1.5 h-full bg-blue-500"></div>
                                    <div className="flex justify-between items-start mb-4">
                                        <div>
                                            <div className="flex items-center gap-2 mb-1">
                                                <span className="text-xs font-bold uppercase tracking-wider text-blue-600 bg-blue-50 px-2 py-1 rounded">
                                                    Rank {rec.Rank || rec.rank || i + 1}
                                                </span>
                                                <span className="text-sm font-medium text-emerald-600 bg-emerald-50 px-2 py-1 rounded flex items-center gap-1">
                                                    <CheckCircle className="w-3.5 h-3.5" />
                                                    {rec["Predicted Readiness"] || rec.predicted_readiness || "Ready Now"}
                                                </span>
                                            </div>
                                            <h3 className="text-xl font-bold text-slate-900 mt-2">{rec.Name || rec.name || "Unknown Candidate"}</h3>
                                            <p className="text-sm text-slate-500 flex items-center gap-2 mt-1">
                                                <Briefcase className="w-4 h-4" /> {rec["Job Title"] || rec.job_title}
                                            </p>
                                        </div>
                                        <div className="text-right">
                                            <div className="text-2xl font-black text-slate-800">{rec["Succession Score"] || rec.succession_score || "N/A"}</div>
                                            <div className="text-xs text-slate-500 font-medium tracking-wide uppercase">Score</div>
                                        </div>
                                    </div>

                                    <div className="bg-slate-50 rounded-lg p-4 border border-slate-100 text-sm leading-relaxed text-slate-700">
                                        <p>
                                            <strong>AI Reasoning:</strong>{" "}
                                            {rec["AI Reasoning"] || rec.ai_reasoning || "Strong technical background with proven capacity for complex project delivery."}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </>
                    )}
                </div>
            </div>

            {isJdViewerOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4">
                    <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
                        <div className="flex items-start justify-between gap-4 border-b border-slate-200 p-5">
                            <div>
                                <h3 className="text-lg font-semibold text-slate-900">Selected Job Description</h3>
                                <p className="text-sm text-slate-500 mt-1">{formatJdLabel(selectedJd)}</p>
                            </div>
                            <button
                                onClick={closeJdViewer}
                                className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors"
                                aria-label="Close JD viewer"
                            >
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <div className="p-5 overflow-y-auto">
                            {isJdViewerLoading ? (
                                <div className="flex items-center justify-center gap-2 text-slate-500 py-16">
                                    <Loader2 className="w-5 h-5 animate-spin" /> Loading selected JD...
                                </div>
                            ) : jdViewerError ? (
                                <div className="rounded-lg border border-red-100 bg-red-50 p-4 text-sm text-red-700">{jdViewerError}</div>
                            ) : jdViewerData ? (
                                <div className="space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
                                        <div className="rounded-lg bg-slate-50 border border-slate-200 p-3">
                                            <div className="font-medium text-slate-700">Job Title</div>
                                            <div className="text-slate-600 mt-1">{jdViewerData.job_title || jdViewerData.position || "Not provided"}</div>
                                        </div>
                                        <div className="rounded-lg bg-slate-50 border border-slate-200 p-3">
                                            <div className="font-medium text-slate-700">Grade</div>
                                            <div className="text-slate-600 mt-1">{jdViewerData.grade || "No grade"}</div>
                                        </div>
                                        <div className="rounded-lg bg-slate-50 border border-slate-200 p-3">
                                            <div className="font-medium text-slate-700">Filename</div>
                                            <div className="text-slate-600 mt-1 break-words">{jdViewerData.original_filename || "Not provided"}</div>
                                        </div>
                                    </div>

                                    <div className="rounded-lg border border-slate-200 bg-white">
                                        <div className="border-b border-slate-200 px-4 py-3 font-medium text-slate-700">JD Content</div>
                                        <pre className="max-h-[55vh] overflow-y-auto whitespace-pre-wrap break-words p-4 text-sm leading-relaxed text-slate-700 font-sans">
                                            {jdViewerData.content || "No extracted JD text is available for this file."}
                                        </pre>
                                    </div>
                                </div>
                            ) : null}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
