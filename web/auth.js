// Sign-in with the Cognito managed login pages (authorization code flow).
// Loaded before the other page scripts. On every page load it:
//   1. if the URL has ?code=... (Cognito just sent the user back), swaps the
//      code for tokens at /oauth2/token and removes ?code from the address bar
//   2. otherwise uses the stored ID token, or the refresh token when it expired
//   3. with neither, sends the browser to the Cognito sign-in page
// The page stays hidden (body.auth-pending) until one of these succeeds.
// This check only decides what the user sees: anyone can edit this file.
// The API is what refuses requests without a valid token.
//
// Other scripts call:
//   const response = await apiFetch("/files");   // fetch() to our API, with the token
//   const token = await getIdToken();   // the ID token, refreshed if needed
//   signOut();

const AUTH_STORAGE = { idToken: "idToken", refreshToken: "refreshToken" };
// Must match a callback/logout URL of the app client (infra/terraform/aws/cognito.tf).
const AUTH_REDIRECT_URI = window.location.origin + "/index.html";
const AUTH_EXPIRY_MARGIN = 60; // seconds; refresh a little before the token expires

const authReady = startAuth();

async function startAuth() {
  if (!window.CONFIG?.cognitoDomain) {
    showAuthProblem("config.js has no Cognito settings. Deploy with Terraform, or add cognitoDomain and clientId to a local copy (see README).");
    return null;
  }
  try {
    const code = new URLSearchParams(window.location.search).get("code");
    if (code) {
      // A code works only once, so take it out of the URL before a reload can reuse it.
      window.history.replaceState(null, "", window.location.pathname);
      await requestTokens({ grant_type: "authorization_code", code, redirect_uri: AUTH_REDIRECT_URI });
    }
    const token = await validIdToken();
    showSignedIn(readClaims(token));
    return token;
  } catch (err) {
    // No automatic redirect here: if the token endpoint keeps failing, that would loop forever.
    showAuthProblem(`Sign-in failed: ${err.message}`, true);
    return null;
  }
}

// For the other scripts. Waits for startAuth first: right after sign-in the
// code is still being swapped for tokens, and there is no token to return yet.
async function getIdToken() {
  if (!(await authReady)) throw new Error("Not signed in");
  return validIdToken();
}

// fetch() for our API: `path` is appended to CONFIG.apiUrl (e.g. "/files") and
// the ID token goes in the Authorization header, where API Gateway's Cognito
// authorizer looks for it (the raw token, no "Bearer " prefix). Not for the
// presigned S3 URLs: those carry their own signature, and S3 rejects a second one.
async function apiFetch(path, options = {}) {
  const token = await getIdToken();
  return fetch(`${window.CONFIG.apiUrl}${path}`, {
    ...options,
    headers: { ...options.headers, Authorization: token },
  });
}

// Returns a valid ID token. Never returns without one: it redirects to the
// sign-in page instead (the returned promise then never resolves).
async function validIdToken() {
  const token = localStorage.getItem(AUTH_STORAGE.idToken);
  if (token && !isExpired(token)) return token;

  const refreshToken = localStorage.getItem(AUTH_STORAGE.refreshToken);
  if (refreshToken) {
    try {
      await requestTokens({ grant_type: "refresh_token", refresh_token: refreshToken });
      return localStorage.getItem(AUTH_STORAGE.idToken);
    } catch {
      // The refresh token expired or was revoked: sign in again below.
    }
  }
  redirectToSignIn();
  return new Promise(() => {}); // the page is navigating away
}

// POST to the token endpoint. The app client has no secret, so client_id is enough.
async function requestTokens(params) {
  const response = await fetch(`${window.CONFIG.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ ...params, client_id: window.CONFIG.clientId }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `token endpoint returned ${response.status}`);

  localStorage.setItem(AUTH_STORAGE.idToken, body.id_token);
  // A refresh request does not return a new refresh token; keep the old one.
  if (body.refresh_token) localStorage.setItem(AUTH_STORAGE.refreshToken, body.refresh_token);
}

function redirectToSignIn() {
  const params = new URLSearchParams({
    client_id: window.CONFIG.clientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: AUTH_REDIRECT_URI,
  });
  window.location.assign(`${window.CONFIG.cognitoDomain}/oauth2/authorize?${params}`);
}

// Clears the tokens here and the Cognito session cookie (via /logout), so the
// next visit asks for the password again.
function signOut() {
  localStorage.removeItem(AUTH_STORAGE.idToken);
  localStorage.removeItem(AUTH_STORAGE.refreshToken);
  const params = new URLSearchParams({ client_id: window.CONFIG.clientId, logout_uri: AUTH_REDIRECT_URI });
  window.location.assign(`${window.CONFIG.cognitoDomain}/logout?${params}`);
}

// The middle part of a JWT is base64url-encoded JSON. This only reads it;
// checking the signature is the API's job.
function readClaims(token) {
  const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes)); // TextDecoder keeps non-ASCII names intact
}

function isExpired(token) {
  try {
    return readClaims(token).exp - AUTH_EXPIRY_MARGIN < Date.now() / 1000;
  } catch {
    return true; // not a readable JWT
  }
}

function showSignedIn(claims) {
  document.getElementById("user-name").textContent = claims.given_name || claims.email;
  const button = document.getElementById("sign-out");
  button.hidden = false;
  button.addEventListener("click", signOut);
  document.body.classList.remove("auth-pending");
}

function showAuthProblem(message, offerSignIn = false) {
  const warning = document.getElementById("warning");
  warning.textContent = message + " ";
  if (offerSignIn) {
    const link = document.createElement("a");
    link.href = "#";
    link.textContent = "Sign in again";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      redirectToSignIn();
    });
    warning.append(link);
  }
  warning.hidden = false;
  document.body.classList.remove("auth-pending");
}
