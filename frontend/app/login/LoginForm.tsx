"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { IconProducts } from "@/components/ui/icons";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/lib/auth";
import { APP_NAME } from "@/lib/config";
import { ApiError, getErrorMessage } from "@/services/http";

const REDIRECT_BASE = "http://panel.invalid";

/**
 * Only allow same-origin relative redirects. Parsing with `URL` also rejects tricks that a
 * prefix check misses: `//evil.com`, `/\evil.com`, `/<TAB>/evil.com` (tabs/newlines are stripped
 * by the URL parser and the result becomes protocol-relative).
 */
function safeNextPath(value: string | null): string {
  if (!value || !value.startsWith("/")) return "/";
  let url: URL;
  try {
    url = new URL(value, REDIRECT_BASE);
  } catch {
    return "/";
  }
  if (url.origin !== REDIRECT_BASE) return "/";
  if (url.pathname === "/login" || url.pathname.startsWith("/login/")) return "/";
  return `${url.pathname}${url.search}${url.hash}`;
}

function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return error.detail || "Неверный логин или пароль";
    if (error.status === 429) return error.detail || "Слишком много попыток входа. Попробуйте через минуту.";
  }
  return getErrorMessage(error);
}

export function LoginForm() {
  const { login, status } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = safeNextPath(searchParams.get("next"));

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Already signed in (restored session or successful login) → go to the panel.
  useEffect(() => {
    if (status === "authenticated") router.replace(nextPath);
  }, [status, nextPath, router]);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    if (!username.trim() || !password) {
      setError("Введите логин и пароль");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(loginErrorMessage(err));
      setSubmitting(false);
    }
  };

  return (
    <div className="w-full max-w-sm">
      <div className="mb-6 flex flex-col items-center text-center">
        <span aria-hidden className="mb-3 flex size-12 items-center justify-center rounded-xl bg-brand-600 text-white shadow-sm">
          <IconProducts className="size-7" />
        </span>
        <h1 className="text-xl font-semibold text-slate-900">{APP_NAME}</h1>
        <p className="mt-1 text-sm text-slate-500">Вход в админ-панель</p>
      </div>

      <form
        onSubmit={onSubmit}
        noValidate
        className="flex flex-col gap-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
        aria-describedby={error ? "login-error" : undefined}
      >
        <Input
          label="Логин"
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          required
          disabled={submitting}
          autoFocus
        />
        <div className="flex flex-col gap-1.5">
          <Input
            label="Пароль"
            name="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            disabled={submitting}
          />
          <button
            type="button"
            onClick={() => setShowPassword((value) => !value)}
            className="self-end rounded text-xs text-slate-500 hover:text-slate-700 focus-visible:outline-2 focus-visible:outline-brand-600"
            aria-pressed={showPassword}
          >
            {showPassword ? "Скрыть пароль" : "Показать пароль"}
          </button>
        </div>

        {error ? (
          <p id="login-error" role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        ) : null}

        <Button type="submit" fullWidth loading={submitting}>
          Войти
        </Button>
      </form>
    </div>
  );
}
