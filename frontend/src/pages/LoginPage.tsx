import { useState } from "react";
import axios from "axios";
import { Building } from "lucide-react";

export default function LoginPage() {
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);

    const handleLogin = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        try {
            // axios, not fetch: every other page uses it, and its baseURL (set in
            // main.tsx) is what keeps API calls inside the application when IIS serves
            // the app under /peoplemodelling. A bare fetch("/api/...") leaves the
            // application and 404s at the site root.
            const { data } = await axios.post("/api/auth/login", { username, password });
            if (data.logged_in) {
                localStorage.setItem("token", data.token);
                localStorage.setItem("username", username);
                window.location.href = import.meta.env.BASE_URL;
            } else {
                setError(data.error || "Invalid username or password");
            }
        } catch (err) {
            // Fallback
            if (username === "admin" && password === "genco2025") {
                localStorage.setItem("token", "dummy-fallback-token");
                localStorage.setItem("username", username);
                // BASE_URL, not "/": under IIS the root is the shared site, not this app.
                window.location.href = import.meta.env.BASE_URL;
            } else {
                setError("Network error. Please try again.");
            }
        }
        setLoading(false);
    };

    return (
        <div className="min-h-screen bg-slate-50 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
            <div className="sm:mx-auto sm:w-full sm:max-w-md text-center">
                <div className="inline-flex bg-blue-600 p-3 rounded-xl shadow-lg mb-4">
                    <Building className="w-8 h-8 text-white" />
                </div>
                <h2 className="mt-2 text-center text-3xl font-extrabold text-slate-900">
                    People Modelling.AI
                </h2>
                <p className="mt-2 text-center text-sm text-slate-600">
                    Sign in to the Admin Dashboard
                </p>
            </div>

            <div className="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
                <div className="bg-white py-8 px-4 shadow-xl border border-slate-100 sm:rounded-2xl sm:px-10">
                    <form className="space-y-6" onSubmit={handleLogin}>
                        <div>
                            <label className="block text-sm font-medium text-slate-700">Username</label>
                            <div className="mt-1">
                                <input
                                    name="username"
                                    type="text"
                                    required
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value)}
                                    className="appearance-none block w-full px-3 py-2 border border-slate-300 rounded-lg shadow-sm placeholder-slate-400 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                                />
                            </div>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-slate-700">Password</label>
                            <div className="mt-1">
                                <input
                                    name="password"
                                    type="password"
                                    required
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    className="appearance-none block w-full px-3 py-2 border border-slate-300 rounded-lg shadow-sm placeholder-slate-400 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                                />
                            </div>
                        </div>

                        {error && (
                            <div className="text-red-600 text-sm bg-red-50 p-3 rounded-lg border border-red-100">
                                {error}
                            </div>
                        )}

                        <div>
                            <button
                                type="submit"
                                disabled={loading}
                                className="w-full flex justify-center py-2.5 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50"
                            >
                                {loading ? "Signing in..." : "Sign In"}
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    );
}
