// Login and registration forms.

import { api } from "../api.js";
import { renderAccount } from "../main.js";
import { session, setUser } from "../state.js";
import { h, navigate } from "../util.js";

export function mount(container, { path }) {
  const registering = path === "/register";
  if (registering && !session.config.allow_registration) {
    container.append(h("div.card.narrow", {}, h("h2", {}, "Registration is disabled"), h("p.muted", {}, "Ask the server administrator for an account.")));
    return;
  }
  const error = h("p.form-error", { hidden: true });
  const username = h("input", { name: "username", autocomplete: "username", required: true, maxLength: 20, autocapitalize: "none", spellcheck: false });
  const password = h("input", {
    name: "password",
    type: "password",
    required: true,
    autocomplete: registering ? "new-password" : "current-password",
  });
  const submit = h("button.primary", { type: "submit" }, registering ? "Create account" : "Log in");

  const form = h(
    "form.card.narrow",
    {
      onsubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        error.hidden = true;
        try {
          const user = await api(registering ? "/auth/register" : "/auth/login", {
            method: "POST",
            body: { username: username.value.trim(), password: password.value },
          });
          setUser(user);
          renderAccount();
          navigate("#/");
        } catch (failure) {
          error.textContent = failure.message;
          error.hidden = false;
          submit.disabled = false;
        }
      },
    },
    h("h2", {}, registering ? "Create an account" : "Log in"),
    h("label", {}, "Username", username),
    registering && h("p.hint-text", {}, "3–20 characters: letters, digits, _ and -"),
    h("label", {}, "Password", password),
    registering && h("p.hint-text", {}, "At least 8 characters"),
    error,
    submit,
    registering
      ? h("p.muted", {}, "Already registered? ", h("a", { href: "#/login" }, "Log in"))
      : session.config.allow_registration && h("p.muted", {}, "New here? ", h("a", { href: "#/register" }, "Create an account")),
  );
  container.append(form);
  username.focus();
}
