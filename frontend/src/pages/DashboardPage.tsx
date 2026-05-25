import { useState, useEffect } from "react";
import { BarChart3, Users, Briefcase, TrendingUp } from "lucide-react";
import axios from "axios";

type ChartItem = { name: string; value: number };

type AnalyticsResponse = {
  positions?: { total?: number; distinct_projects?: number };
  employees?: { total?: number };
  charts?: {
    positions_by_project?: ChartItem[];
    employees_by_grade?: ChartItem[];
  };
};

function SimpleBarChart({ data, emptyText }: { data?: ChartItem[]; emptyText: string }) {
  const items = (data || []).filter((d) => Number(d.value) > 0);

  if (!items.length) {
    return (
      <div className="flex items-center justify-center h-64 bg-slate-50 border border-slate-100 rounded-lg">
        <p className="text-slate-400">{emptyText}</p>
      </div>
    );
  }

  const max = Math.max(...items.map((d) => d.value), 1);

  return (
    <div className="h-64 overflow-y-auto pr-1">
      <div className="space-y-4">
        {items.map((item, idx) => (
          <div key={`${item.name}-${idx}`}>
            <div className="flex items-center justify-between text-sm mb-1 gap-3">
              <span className="text-slate-700 font-medium truncate">{item.name}</span>
              <span className="text-slate-500 shrink-0">{item.value}</span>
            </div>
            <div className="w-full h-3 rounded-full bg-slate-100 overflow-hidden">
              <div
                className="h-3 rounded-full bg-blue-500 transition-all duration-500"
                style={{ width: `${Math.max((item.value / max) * 100, 8)}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats] = useState<AnalyticsResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await axios.get("/api/analytics/summary");
        setStats(res.data);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchStats();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
      </div>
    );
  }

  const kpis = [
    {
      title: "Active Positions",
      value: stats?.positions?.total || 0,
      icon: <Briefcase className="w-5 h-5 text-blue-600" />,
      change: "+2 from last month",
      trend: "up"
    },
    {
      title: "Total Employees",
      value: stats?.employees?.total || 0,
      icon: <Users className="w-5 h-5 text-emerald-600" />,
      change: "Stable",
      trend: "neutral"
    },
    {
      title: "Projects",
      value: stats?.positions?.distinct_projects || 0,
      icon: <BarChart3 className="w-5 h-5 text-indigo-600" />,
      change: "Active in 4 regions",
      trend: "neutral"
    },
    {
      title: "Talent Match Rate",
      value: "92%",
      icon: <TrendingUp className="w-5 h-5 text-purple-600" />,
      change: "+4% vs previous",
      trend: "up"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Analytics Overview</h1>
        <p className="text-slate-500 mt-1">Monitor key metrics for position profiles and candidate availability.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {kpis.map((kpi, idx) => (
          <div key={idx} className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm hover:shadow-md transition-shadow">
            <div className="flex items-center justify-between">
              <div className={`p-2 rounded-lg ${kpi.trend === 'up' ? "bg-blue-50" : "bg-slate-50"}`}>
                {kpi.icon}
              </div>
            </div>
            <div className="mt-4">
              <h3 className="text-3xl font-bold text-slate-800">{kpi.value}</h3>
              <p className="text-sm font-medium text-slate-500 mt-1">{kpi.title}</p>
            </div>
            <div className="mt-4 flex items-center text-sm">
              <span className={`font-medium ${kpi.trend === 'up' ? "text-emerald-600" : "text-slate-500"}`}>
                {kpi.change}
              </span>
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm min-h-[400px]">
          <h3 className="text-lg font-semibold text-slate-800 mb-4">Positions by Project</h3>
          <SimpleBarChart
            data={stats?.charts?.positions_by_project}
            emptyText="No position profile data available yet"
          />
        </div>

        <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm min-h-[400px]">
          <h3 className="text-lg font-semibold text-slate-800 mb-4">Employees by Grade</h3>
          <SimpleBarChart
            data={stats?.charts?.employees_by_grade}
            emptyText="No employee model data available yet"
          />
        </div>
      </div>
    </div>
  );
}
