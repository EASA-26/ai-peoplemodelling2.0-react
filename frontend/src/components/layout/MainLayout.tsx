import { Outlet, Navigate, Link, useLocation } from "react-router-dom";
import {
    BarChart,
    Users,
    UserPlus,
    Database,
    Building,
    LogOut,
    Bell,
    HelpCircle
} from "lucide-react";

export default function MainLayout() {
    const location = useLocation();

    if (localStorage.getItem("token") === null) {
        return <Navigate to="/login" replace />;
    }

    const handleLogout = () => {
        localStorage.removeItem("token");
        localStorage.removeItem("username");
        window.location.href = "/login";
    };

    const username = localStorage.getItem("username") || "admin";

    const navItems = [
        { name: "Analytics", path: "/analytics", icon: <BarChart className="w-5 h-5" /> },
        { name: "Succession Recommendation", path: "/succession", icon: <Users className="w-5 h-5" /> },
        { name: "Person ➜ Position", path: "/person-to-position", icon: <UserPlus className="w-5 h-5" /> },
        { name: "Data Management", path: "/data-management", icon: <Database className="w-5 h-5" /> },
    ];

    return (
        <div className="flex h-screen bg-[#F8FAFC]">
            {/* Sidebar */}
            <aside className="w-64 bg-white border-r border-slate-200 flex flex-col hidden md:flex">
                <div className="p-6 flex items-center gap-3">
                    <div className="bg-blue-600 p-2 rounded-lg">
                        <Building className="w-6 h-6 text-white" />
                    </div>
                    <div>
                        <h1 className="font-bold text-slate-800 text-lg leading-tight">People Modelling.AI</h1>
                        <p className="text-xs text-slate-500">Admin Dashboard</p>
                    </div>
                </div>

                <nav className="flex-1 px-4 py-6 space-y-2">
                    {navItems.map((item) => (
                        <Link
                            key={item.path}
                            to={item.path}
                            className={`flex items-center gap-3 px-4 py-3 rounded-lg font-medium transition-colors ${location.pathname.startsWith(item.path)
                                ? "bg-blue-50 text-blue-700"
                                : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                                }`}
                        >
                            {item.icon}
                            {item.name}
                        </Link>
                    ))}
                </nav>

                <div className="p-4 border-t border-slate-100">
                    <button
                        onClick={handleLogout}
                        className="flex items-center gap-3 px-4 py-3 w-full rounded-lg font-medium text-slate-600 hover:bg-red-50 hover:text-red-600 transition-colors"
                    >
                        <LogOut className="w-5 h-5" />
                        Sign Out
                    </button>
                </div>
            </aside>

            {/* Main Content */}
            <div className="flex-1 flex flex-col overflow-hidden">
                {/* Top Header */}
                <header className="h-16 bg-white border-b border-slate-200 px-8 flex items-center justify-between shadow-sm z-10">
                    <div className="flex-1 max-w-xl">
                        {/* Search placeholder */}
                        <div className="relative">
                            <input
                                type="text"
                                placeholder="Search resources..."
                                className="w-full bg-slate-100 border-none rounded-full pl-4 pr-10 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                            />
                        </div>
                    </div>

                    <div className="flex items-center gap-6">
                        <div className="flex items-center gap-4 text-slate-400">
                            <Bell className="w-5 h-5 hover:text-slate-600 cursor-pointer" />
                            <HelpCircle className="w-5 h-5 hover:text-slate-600 cursor-pointer" />
                        </div>
                        <div className="flex items-center gap-3 border-l border-slate-200 pl-6">
                            <div className="text-right hidden sm:block">
                                <p className="text-sm font-semibold text-slate-700">{username}</p>
                                <p className="text-xs text-slate-500">HR Manager</p>
                            </div>
                            <div className="w-10 h-10 rounded-full bg-blue-100 border border-blue-200 flex items-center justify-center text-blue-700 font-bold">
                                A
                            </div>
                        </div>
                    </div>
                </header>

                {/* Page Content */}
                <main className="flex-1 overflow-y-auto p-8">
                    <Outlet />
                </main>
            </div>
        </div>
    );
}
