/**
 * Header auth widget.
 *
 * Fetches /api/me on every page and renders either a "Sign in with Lichess"
 * link or the signed-in username with a log-out button.
 */

const widget = document.getElementById('auth-widget');

// Send the user back to the current page after the OAuth round-trip.
function loginUrl() {
    const next = window.location.pathname + window.location.search;
    return `/auth/login?next=${encodeURIComponent(next)}`;
}

function renderSignIn() {
    widget.replaceChildren();
    const link = document.createElement('a');
    link.href = loginUrl();
    link.className = 'auth-signin';
    link.textContent = 'Sign in with Lichess';
    widget.appendChild(link);
}

function renderUser(user) {
    widget.replaceChildren();
    const name = document.createElement('span');
    name.className = 'auth-username';
    name.textContent = user.username;
    const logout = document.createElement('button');
    logout.type = 'button';
    logout.className = 'auth-logout';
    logout.textContent = 'Log out';
    logout.addEventListener('click', async () => {
        try {
            await fetch('/auth/logout', { method: 'POST' });
        } catch {
            // Even if the request fails, go back to the menu as anonymous.
        }
        window.location.assign('/');
    });
    widget.append(name, logout);
}

async function renderAuth() {
    if (!widget) return;
    try {
        const response = await fetch('/api/me');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const { user } = await response.json();
        if (user) renderUser(user);
        else renderSignIn();
    } catch {
        renderSignIn();
    }
}

renderAuth();
