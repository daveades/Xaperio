import { useState } from "react";

export default function Auth({ onSignedIn, initialMode = "login" }) {
  const [mode, setMode] = useState(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    fetch("/auth/" + mode, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    })
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "something went wrong");
        const me = await fetch("/auth/me").then((r) => r.json());
        onSignedIn(me.user);
      })
      .catch((err) => setError(err.message))
      .finally(() => setBusy(false));
  }

  return (
    <div className="auth">
      <p className="auth__tab">
        <button
          type="button"
          className={mode === "login" ? "mode mode--on" : "mode"}
          onClick={() => {
            setMode("login");
            setError(null);
          }}
        >
          Sign in
        </button>
        <button
          type="button"
          className={mode === "register" ? "mode mode--on" : "mode"}
          onClick={() => {
            setMode("register");
            setError(null);
          }}
        >
          Create account
        </button>
      </p>

      <form className="auth__form" onSubmit={submit}>
        <label className="field">
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
        </label>
        {error && <p className="auth__error">{error}</p>}
        <button type="submit" className="btn auth__submit" disabled={busy}>
          {busy ? "Please wait..." : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>
    </div>
  );
}
