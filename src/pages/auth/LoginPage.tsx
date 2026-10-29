/**
 * 登录/注册页面
 *
 * 布局：桌面端左右分栏，左侧品牌展示，右侧表单
 * 互斥逻辑：无用户时强制注册，有用户时默认登录
 */

import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  User,
  Lock,
  Loader2,
  Eye,
  EyeOff,
  Server,
  Shield,
  Zap,
  ArrowRight,
  CheckCircle2,
} from "lucide-react";
import * as authApi from "@/api/auth";
import axios from "axios";
import { useAuth } from "@/hooks/useAuth";
import { useToast } from "@/components/ui/toast";
import { LogoIcon } from "@/components/LogoIcon";

/* ------------------------------------------------------------------ */
const features = [
  { icon: Server, text: "海量模板，开箱即用" },
  { icon: Shield, text: "服务编排，丝滑运转" },
  { icon: Zap, text: "订阅自动化，省心省力" },
];

/* ------------------------------------------------------------------ */
export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isCheckingUsers, setIsCheckingUsers] = useState(true);
  const [isRegisterMode, setIsRegisterMode] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [focusedField, setFocusedField] = useState<string | null>(null);

  const navigate = useNavigate();
  const { isAuthenticated, login } = useAuth();
  const toast = useToast();
  const usernameRef = useRef<HTMLInputElement>(null);

  /* 已登录则跳转 */
  useEffect(() => {
    if (isAuthenticated) {
      navigate("/", { replace: true });
    }
  }, [isAuthenticated, navigate]);

  /* 检查系统中是否已有用户 */
  useEffect(() => {
    authApi
      .hasUsers()
      .then((hasUsers) => {
        setIsRegisterMode(!hasUsers);
      })
      .catch(() => {
        // 请求失败时默认展示登录，并提示用户检查后端
        setIsRegisterMode(false);
        toast.error("无法连接到后端服务，请确认后端已启动", { duration: 5000 });
      })
      .finally(() => {
        setIsCheckingUsers(false);
        setTimeout(() => usernameRef.current?.focus(), 300);
      });
  }, [toast]);

  /* 登录提交 */
  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) {
      toast.error("请输入用户名和密码");
      return;
    }

    setIsLoading(true);
    try {
      const resp = await authApi.login(username, password);
      await login(resp.access_token, rememberMe);
      toast.success("登录成功，正在跳转...", { duration: 2000 });
      setTimeout(() => navigate("/"), 600);
    } catch {
      toast.error("用户名或密码错误", { duration: 3000 });
    } finally {
      setIsLoading(false);
    }
  };

  /* 注册提交 */
  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) {
      toast.error("请输入用户名和密码");
      return;
    }
    if (password !== confirmPassword) {
      toast.error("两次输入的密码不一致");
      return;
    }
    if (password.length < 6) {
      toast.error("密码长度至少 6 位");
      return;
    }

    setIsLoading(true);
    try {
      // 注册
      await authApi.register(username, password);
      // 自动登录
      const loginResp = await authApi.login(username, password);
      await login(loginResp.access_token);
      toast.success("注册成功，正在进入系统...", { duration: 2000 });
      setTimeout(() => navigate("/"), 600);
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? err.response?.data?.message || "注册失败"
        : "注册失败";
      toast.error(msg, { duration: 3000 });
    } finally {
      setIsLoading(false);
    }
  };

  /* 切换模式时清空表单（安全考虑） */
  const toggleMode = () => {
    setIsRegisterMode(!isRegisterMode);
    setPassword("");
    setConfirmPassword("");
    setTimeout(() => usernameRef.current?.focus(), 100);
  };

  if (isCheckingUsers) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex bg-background">
      {/* ===================== 左侧品牌区 ===================== */}
      <div className="hidden lg:flex lg:w-[55%] xl:w-1/2 relative bg-primary flex-col justify-between p-12 xl:p-16 overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-primary via-[#6d28d9] to-[#5b21b6]" />
        <div className="absolute inset-0 overflow-hidden pointer-events-none">
          {[
            { size: 300, x: "10%", y: "20%", delay: 0, duration: 8 },
            { size: 200, x: "70%", y: "60%", delay: 2, duration: 10 },
            { size: 160, x: "40%", y: "80%", delay: 4, duration: 12 },
            { size: 240, x: "80%", y: "15%", delay: 1, duration: 9 },
            { size: 120, x: "25%", y: "45%", delay: 3, duration: 11 },
          ].map((orb, i) => (
            <div
              key={i}
              className="absolute rounded-full opacity-20"
              style={{
                width: orb.size,
                height: orb.size,
                left: orb.x,
                top: orb.y,
                background:
                  "radial-gradient(circle, rgba(255,255,255,0.4) 0%, transparent 70%)",
                animationDelay: `${orb.delay}s`,
                animationDuration: `${orb.duration}s`,
              }}
            />
          ))}
        </div>

        <div className="relative z-10 flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-white/15 backdrop-blur-sm flex items-center justify-center">
            <LogoIcon size={24} inverse />
          </div>
          <span className="text-xl font-semibold text-white tracking-tight">
            OneTap
          </span>
        </div>

        <div className="relative z-10 max-w-md">
          <h2 className="text-4xl xl:text-5xl font-bold text-white leading-tight tracking-tight">
            掌控您的
            <br />
            <span className="text-white/80">数字生活</span>
          </h2>
          <p className="mt-6 text-lg text-white/70 leading-relaxed">
            告别繁琐配置，OneTap 让您轻松拥有专属的家庭智能娱乐中心！
            作为一站式自动化编排工具，它提供海量模板，只需一键即可唤醒
            家庭娱乐体验，让一切尽可能在你的掌握之中。
          </p>

          <div className="mt-10 space-y-4">
            {features.map((f) => (
              <div
                key={f.text}
                className="flex items-center gap-3 text-white/80"
              >
                <div className="h-8 w-8 rounded-lg bg-white/10 flex items-center justify-center">
                  <f.icon className="h-4 w-4 text-white" />
                </div>
                <span className="text-sm font-medium">{f.text}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="relative z-10 text-sm text-white/50">
          © {new Date().getFullYear()} OneTap. All rights reserved.
        </div>
      </div>

      {/* ===================== 右侧表单区 ===================== */}
      <div className="flex-1 flex items-center justify-center p-4 sm:p-8 relative">
        <div className="lg:hidden absolute top-6 left-6 flex items-center gap-2">
          <LogoIcon size={28} />
          <span className="text-lg font-semibold text-foreground">OneTap</span>
        </div>

        <div className="w-full max-w-[400px] animate-fade-in">
          {/* 标题 */}
          <div className="mb-8">
            <h1 className="text-2xl font-bold text-foreground tracking-tight">
              {isRegisterMode ? "创建管理员账户" : "欢迎回来"}
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {isRegisterMode
                ? "首次使用，请创建管理员账户以继续"
                : "请输入您的账户信息以继续"}
            </p>
          </div>

          {/* 表单 */}
          <form
            onSubmit={isRegisterMode ? handleRegister : handleLogin}
            className="space-y-5"
          >
            {/* 用户名 */}
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-foreground">
                用户名
              </label>
              <div
                className={`relative group transition-all duration-200 ${
                  focusedField === "username"
                    ? "ring-2 ring-primary/20 rounded-xl"
                    : ""
                }`}
              >
                <div className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 flex items-center justify-center">
                  <User
                    className={`h-4 w-4 transition-colors duration-200 ${
                      focusedField === "username"
                        ? "text-primary"
                        : "text-muted-foreground"
                    }`}
                  />
                </div>
                <input
                  ref={usernameRef}
                  type="text"
                  placeholder="请输入用户名"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  onFocus={() => setFocusedField("username")}
                  onBlur={() => setFocusedField(null)}
                  disabled={isLoading}
                  autoComplete="username"
                  className="w-full h-12 pl-10 pr-4 rounded-xl bg-card border border-border text-sm text-foreground placeholder:text-muted-foreground outline-none transition-all duration-200 focus:border-primary hover:border-border/80 disabled:opacity-50"
                />
              </div>
            </div>

            {/* 密码 */}
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-foreground">
                密码
              </label>
              <div
                className={`relative group transition-all duration-200 ${
                  focusedField === "password"
                    ? "ring-2 ring-primary/20 rounded-xl"
                    : ""
                }`}
              >
                <div className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 flex items-center justify-center">
                  <Lock
                    className={`h-4 w-4 transition-colors duration-200 ${
                      focusedField === "password"
                        ? "text-primary"
                        : "text-muted-foreground"
                    }`}
                  />
                </div>
                <input
                  type={showPassword ? "text" : "password"}
                  placeholder="请输入密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onFocus={() => setFocusedField("password")}
                  onBlur={() => setFocusedField(null)}
                  disabled={isLoading}
                  autoComplete={isRegisterMode ? "new-password" : "current-password"}
                  className="w-full h-12 pl-10 pr-10 rounded-xl bg-card border border-border text-sm text-foreground placeholder:text-muted-foreground outline-none transition-all duration-200 focus:border-primary hover:border-border/80 disabled:opacity-50"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  tabIndex={-1}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors p-1"
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>

            {/* 确认密码 — 仅注册模式 */}
            {isRegisterMode && (
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-foreground">
                  确认密码
                </label>
                <div
                  className={`relative group transition-all duration-200 ${
                    focusedField === "confirm"
                      ? "ring-2 ring-primary/20 rounded-xl"
                      : ""
                  }`}
                >
                  <div className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 flex items-center justify-center">
                    <Lock
                      className={`h-4 w-4 transition-colors duration-200 ${
                        focusedField === "confirm"
                          ? "text-primary"
                          : "text-muted-foreground"
                      }`}
                    />
                  </div>
                  <input
                    type={showConfirm ? "text" : "password"}
                    placeholder="请再次输入密码"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    onFocus={() => setFocusedField("confirm")}
                    onBlur={() => setFocusedField(null)}
                    disabled={isLoading}
                    autoComplete="new-password"
                    className="w-full h-12 pl-10 pr-10 rounded-xl bg-card border border-border text-sm text-foreground placeholder:text-muted-foreground outline-none transition-all duration-200 focus:border-primary hover:border-border/80 disabled:opacity-50"
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirm(!showConfirm)}
                    tabIndex={-1}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors p-1"
                  >
                    {showConfirm ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                  </button>
                </div>
              </div>
            )}

            {/* 记住我 & 忘记密码 — 仅登录模式 */}
            {!isRegisterMode && (
              <div className="flex items-center justify-between">
                <label className="flex items-center gap-2 cursor-pointer group">
                  <div className="relative">
                    <input
                      type="checkbox"
                      checked={rememberMe}
                      onChange={(e) => setRememberMe(e.target.checked)}
                      className="peer sr-only"
                    />
                    <div className="h-4.5 w-4.5 rounded border border-border bg-card transition-all duration-200 peer-checked:bg-primary peer-checked:border-primary flex items-center justify-center">
                      <CheckCircle2 className="h-3 w-3 text-primary-foreground opacity-0 peer-checked:opacity-100 transition-opacity" />
                    </div>
                  </div>
                  <span className="text-sm text-muted-foreground group-hover:text-foreground transition-colors">
                    记住我
                  </span>
                </label>
                <button
                  type="button"
                  onClick={() =>
                    toast.success("请联系管理员重置密码", { duration: 3000 })
                  }
                  className="text-sm text-primary hover:text-primary/80 transition-colors font-medium"
                >
                  忘记密码？
                </button>
              </div>
            )}

            {/* 提交按钮 */}
            <button
              type="submit"
              disabled={isLoading}
              className="w-full h-12 rounded-xl bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-semibold transition-all duration-200 disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2 group shadow-lg shadow-primary/20 hover:shadow-primary/30 hover:-translate-y-0.5 active:translate-y-0"
            >
              {isLoading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>{isRegisterMode ? "注册中..." : "登录中..."}</span>
                </>
              ) : (
                <>
                  <span>{isRegisterMode ? "创建账户" : "登录"}</span>
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
                </>
              )}
            </button>
          </form>

          {/* 模式切换链接 */}
          <div className="mt-6 text-center">
            {isRegisterMode ? (
              <button
                type="button"
                onClick={toggleMode}
                className="text-sm text-primary hover:text-primary/80 transition-colors font-medium"
              >
                已有账户？去登录
              </button>
            ) : (
              <button
                type="button"
                onClick={toggleMode}
                className="text-sm text-primary hover:text-primary/80 transition-colors font-medium"
              >
                还没有账户？去注册
              </button>
            )}
          </div>

        </div>
      </div>

      <style>{`
        @keyframes fade-in {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes float {
          0%, 100% { transform: translateY(0) scale(1); }
          33%      { transform: translateY(-20px) scale(1.05); }
          66%      { transform: translateY(10px) scale(0.95); }
        }
        .animate-fade-in { animation: fade-in 0.5s ease-out; }
      `}</style>
    </div>
  );
}
