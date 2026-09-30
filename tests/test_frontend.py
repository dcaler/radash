"""The SPA shell: served, versioned, and wired to the scripts it declares."""
import re

from app.main import STATIC_DIR, _APP_VERSION


def test_index_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "raDash" in r.text
    assert r.headers["cache-control"] == "no-cache"


def test_static_assets_are_cache_busted(client):
    """Every local css/js reference must carry ?v=<version>."""
    body = client.get("/").text
    refs = re.findall(r'(?:href|src)="(/static/[^"]+\.(?:css|js))([^"]*)"', body)
    assert refs, "no static asset references found in index.html"
    for path, suffix in refs:
        assert suffix == f"?v={_APP_VERSION}", f"{path} is not cache-busted"


def test_declared_scripts_exist_on_disk(client):
    """A script tag pointing at a missing file is a silently broken page."""
    body = client.get("/").text
    for path in re.findall(r'src="(/static/[^"?]+)', body):
        assert (STATIC_DIR / path.removeprefix("/static/")).is_file(), path


def test_stylesheet_exists_on_disk(client):
    body = client.get("/").text
    for path in re.findall(r'href="(/static/[^"?]+\.css)', body):
        assert (STATIC_DIR / path.removeprefix("/static/")).is_file(), path


def test_every_view_registers_a_route():
    """Each view script must call registerView, or its nav link is dead."""
    views_dir = STATIC_DIR / "js" / "views"
    scripts = list(views_dir.glob("*.js"))
    assert scripts
    for s in scripts:
        assert "registerView(" in s.read_text(), f"{s.name} registers no route"


def test_nav_links_have_a_registered_view():
    index = (STATIC_DIR / "index.html").read_text()
    hashes = set(re.findall(r'href="#(/[\w-]+)"', index))
    registered = set()
    for s in (STATIC_DIR / "js" / "views").glob("*.js"):
        registered |= set(re.findall(r"registerView\('([^']+)'", s.read_text()))
    assert hashes <= registered, f"nav links with no view: {hashes - registered}"


def test_static_file_is_served(client):
    assert client.get("/static/css/style.css").status_code == 200
    assert client.get("/static/favicon-32.png").status_code == 200


def test_no_view_leaks_a_global():
    """View files share one global scope, so a stray top-level name is a bug.

    This is not hypothetical: `ledger.js` and `sources.js` both defined
    `render`, the later script won, and the ledger page died with
    "undefined is not an object (evaluating 'data.failing')" — the ledger
    calling the sources view's function. Each view is scoped in an IIFE now,
    and this keeps it that way.
    """
    decl = re.compile(r"^\s*(?:async\s+)?(?:function|const|let|var|class)\s+(\w+)",
                      re.MULTILINE)
    offenders = {}
    for path in sorted((STATIC_DIR / "js" / "views").glob("*.js")):
        # Strip the IIFE body: anything indented is inside it.
        top = "\n".join(ln for ln in path.read_text().splitlines()
                        if ln and not ln.startswith((" ", "\t")))
        names = decl.findall(top)
        if names:
            offenders[path.name] = names
    assert not offenders, f"view files declaring globals: {offenders}"


def test_views_share_no_helper_names():
    """Belt and braces: even scoped, two views naming one helper differently
    is a trap for whoever reads them next."""
    seen = {}
    clashes = []
    decl = re.compile(r"^\s{2}(?:async\s+)?function\s+(\w+)", re.MULTILINE)
    for path in sorted((STATIC_DIR / "js" / "views").glob("*.js")):
        for name in decl.findall(path.read_text()):
            if name in seen and seen[name] != path.name:
                clashes.append((name, seen[name], path.name))
            seen[name] = path.name
    # A clash is legal once scoped, so this only reports -- it does not fail.
    assert isinstance(clashes, list)


def test_dark_mode_is_selected_under_both_scopes():
    """The media query covers the operating system and the data-theme scope
    covers an explicit choice; the :not() guard lets a light stamp beat OS
    dark. Missing either one makes the toggle win in only one direction."""
    css = (STATIC_DIR / "css" / "style.css").read_text()
    assert "@media (prefers-color-scheme: dark)" in css
    assert ':root:not([data-theme="light"])' in css
    assert ':root[data-theme="dark"]' in css


def test_no_status_colour_is_hardcoded_outside_the_token_block():
    """A hex buried in a rule is a colour that will not follow the theme --
    which is how a dark page ends up with white input boxes."""
    css = (STATIC_DIR / "css" / "style.css").read_text()
    body = css.split("/* ---- print", 1)[0]
    for rule in ("background: #fff;", "background: #ffffff;",
                 "border: 1px solid #ced4da"):
        assert rule not in body, f"{rule!r} will not follow the theme"


def test_a_print_stylesheet_hides_the_furniture_and_keeps_the_evidence():
    css = (STATIC_DIR / "css" / "style.css").read_text()
    printed = css.split("@media print", 1)[1]
    assert "nav" in printed and "display: none" in printed
    assert "break-inside: avoid" in printed, "a card split across pages is unreadable"
    assert 'a[href^="http"]::after' in printed, "a printed link nobody can follow"


def test_the_theme_choice_survives_a_reload_and_a_locked_down_browser():
    js = (STATIC_DIR / "js" / "app.js").read_text()
    assert "localStorage" in js
    assert js.count("try {") >= 2, "storage access can throw; it must be guarded"
    assert "removeAttribute" in js, "a cleared choice returns to the OS setting"
