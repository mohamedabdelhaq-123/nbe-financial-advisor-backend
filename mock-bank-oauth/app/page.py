"""Shared minimal styling for this service's server-rendered pages (the
mock bank's own hosted login/OTP UI) — a green/orange/white theme, purely
cosmetic, so the flow looks presentable in a demo. No templating engine:
this is a small mock service, so one shared CSS block + a thin page
wrapper is simpler than pulling in Jinja2 for two pages.
"""

import html

_STYLE = """
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f3f6f2;
    color: #1b1b1b;
    display: flex;
    justify-content: center;
    padding-top: 4rem;
    margin: 0;
  }
  .card {
    background: #ffffff;
    border-top: 6px solid #2e7d32;
    border-radius: 10px;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
    padding: 2rem 2.5rem;
    max-width: 380px;
    width: 100%;
  }
  .card.error {
    border-top-color: #f57c00;
  }
  h1 {
    color: #2e7d32;
    font-size: 1.4rem;
    margin-top: 0;
  }
  .card.error h1 {
    color: #f57c00;
  }
  p {
    color: #444;
    font-size: 0.95rem;
  }
  label {
    display: block;
    font-size: 0.85rem;
    font-weight: 600;
    margin-bottom: 0.35rem;
    color: #2e7d32;
  }
  input[type="text"] {
    width: 100%;
    padding: 0.6rem 0.7rem;
    border: 1px solid #cfd8cc;
    border-radius: 6px;
    font-size: 1rem;
    margin-bottom: 1.1rem;
    box-sizing: border-box;
  }
  input[type="text"]:focus {
    outline: none;
    border-color: #f57c00;
    box-shadow: 0 0 0 2px rgba(245, 124, 0, 0.2);
  }
  button {
    background: #2e7d32;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 0.65rem 1.4rem;
    font-size: 1rem;
    font-weight: 600;
    cursor: pointer;
  }
  button:hover {
    background: #f57c00;
  }
</style>
"""


def render_page(title: str, body: str, *, error: bool = False) -> str:
    """Wraps `body` (already-safe HTML) in the shared page shell/theme.
    `body` is trusted HTML built by the caller — any user-supplied value
    inside it must already be html.escape()'d by that caller."""
    card_class = "card error" if error else "card"
    return f"""
    <html>
    <head><title>{html.escape(title)}</title>{_STYLE}</head>
    <body>
        <div class="{card_class}">
        {body}
        </div>
    </body>
    </html>
    """


def render_error_page(message: str) -> str:
    """The shared shape every _error_page() in this service renders —
    kept here so both routes_authorize.py and routes_login.py show a
    themed error, not a bare unstyled one."""
    return render_page("Error", f"<h1>Error</h1><p>{html.escape(message)}</p>", error=True)
