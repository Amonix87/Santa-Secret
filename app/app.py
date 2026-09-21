"""Secret Santa : petit site auto-hébergé, sans compte, sans e-mail."""
import hmac
import os
import re
import secrets
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, abort, flash, g, jsonify, make_response, redirect,
                   render_template, request, session, url_for)
from markupsafe import Markup
from werkzeug.middleware.proxy_fix import ProxyFix

from draw import draw as run_draw, simulate

DATA_DIR = os.environ.get("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "santa.db")
TZ = ZoneInfo(os.environ.get("APP_TZ", "Europe/Paris"))

# Barème : chaque case « donneur -> receveur » reçoit une note.
# 0 = interdit, 5 = neutre, 1 à 9 = de moins en moins / de plus en plus probable,
# 10 = certain (100 %) : le donneur offre forcément à ce receveur.
NOTE_MIN, NOTE_MAX, NOTE_DEFAULT = 0, 10, 5
NOTE_FORCED = NOTE_MAX
NOTE_STEP = 1.5  # chaque point de plus = 1,5 fois plus de chances (de 1 à 9)


def _couple_note():
    """Note appliquée entre membres d'un couple/groupe (COUPLE_NOTE, 0 à 9, défaut 1)."""
    try:
        return max(NOTE_MIN, min(NOTE_MAX - 1, int(os.environ.get("COUPLE_NOTE", "1"))))
    except ValueError:
        return 1


COUPLE_NOTE = _couple_note()


def note_weight(note):
    if note == NOTE_FORCED:
        return 1.0  # certain : géré par weight_map
    if note <= NOTE_MIN:
        return 0.0
    return NOTE_STEP ** (note - NOTE_DEFAULT)


def _load_secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(DATA_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key


ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = secrets.token_urlsafe(9)
    print(f"[secret-santa] ADMIN_PASSWORD absent. Mot de passe temporaire : "
          f"{ADMIN_PASSWORD}", flush=True)

app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=256 * 1024,
)
if os.environ.get("BEHIND_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# --------------------------------------------------------------------------
# Base de données
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS participants(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  token TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS weights(
  giver INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  receiver INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  level INTEGER NOT NULL,
  PRIMARY KEY(giver, receiver)
);
CREATE TABLE IF NOT EXISTS draw(
  giver INTEGER PRIMARY KEY REFERENCES participants(id) ON DELETE CASCADE,
  receiver INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  viewed_at TEXT
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tokens(key TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS history(
  year INTEGER NOT NULL,
  giver TEXT NOT NULL,
  receiver TEXT NOT NULL,
  PRIMARY KEY(year, giver)
);
"""


def connect():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def new_token():
    return secrets.token_urlsafe(9)


def token_for(con, name):
    """Lien personnel d'un nom : réservé pour toujours, même si la personne est retirée puis
    rajoutée (l'an prochain, elle retrouve le même lien)."""
    key = name.strip().casefold()
    row = con.execute("SELECT token FROM tokens WHERE key=?", (key,)).fetchone()
    if row:
        return row[0]
    token = new_token()
    con.execute("INSERT INTO tokens(key, token) VALUES (?, ?)", (key, token))
    return token


def seed_from_env(con):
    """Au premier démarrage : PARTICIPANTS="A, B, C" et COUPLES="A+B, C+D"."""
    if con.execute("SELECT 1 FROM participants LIMIT 1").fetchone():
        return
    names = [n.strip() for n in re.split(r"[\n,;]+", os.environ.get("PARTICIPANTS", "")) if n.strip()]
    ids = {}
    for name in names:
        if name.lower() in ids:
            continue
        cur = con.execute("INSERT INTO participants(name, token) VALUES (?, ?)", (name, token_for(con, name)))
        ids[name.lower()] = cur.lastrowid
    for group in re.split(r"[,;\n]+", os.environ.get("COUPLES", "")):
        members = [ids[x.strip().lower()] for x in group.split("+") if x.strip().lower() in ids]
        for a in members:
            for b in members:
                if a != b:
                    con.execute("INSERT OR REPLACE INTO weights VALUES (?, ?, ?)", (a, b, COUPLE_NOTE))
    con.commit()


def current_year():
    return datetime.now(TZ).year


def name_key(name):
    return name.strip().casefold()


def record_history(con, year, result):
    """Garde les paires du tirage de l'année (un nouveau tirage la même année remplace le précédent)."""
    keys = {r["id"]: name_key(r["name"]) for r in con.execute("SELECT id, name FROM participants")}
    con.execute("DELETE FROM history WHERE year=?", (year,))
    con.executemany("INSERT INTO history(year, giver, receiver) VALUES (?, ?, ?)",
                    [(year, keys[g], keys[r]) for g, r in result.items()])


def archive_current_draw(con):
    """Tirage fait avant l'arrivée de l'historique : on le range dans l'historique."""
    rows = con.execute("SELECT giver, receiver FROM draw").fetchall()
    if not rows:
        return
    at = con.execute("SELECT value FROM settings WHERE key='drawn_at'").fetchone()
    year = int(at["value"][:4]) if at else current_year()
    if con.execute("SELECT 1 FROM history WHERE year=?", (year,)).fetchone():
        return
    record_history(con, year, {r["giver"]: r["receiver"] for r in rows})


def migrate_scale(con):
    """Met à jour les anciens barèmes (une seule fois chacun)."""
    def scale():
        row = con.execute("SELECT value FROM settings WHERE key='scale'").fetchone()
        return row["value"] if row else None

    if scale() is None:      # v0 : niveaux 0 à 5 -> notes v1 (0 à 10, imposé = 11)
        con.execute("""UPDATE weights SET level = CASE level
            WHEN 0 THEN 0 WHEN 1 THEN 2 WHEN 2 THEN 5 WHEN 3 THEN 7 WHEN 4 THEN 9 WHEN 5 THEN 11
            ELSE level END""")
        con.execute("INSERT OR REPLACE INTO settings VALUES ('scale', 'notes-v1')")
    if scale() == "notes-v1":  # v1 -> v2 : 10 devient « certain », l'ancien 10 devient 9
        con.execute("UPDATE weights SET level = CASE level WHEN 10 THEN 9 WHEN 11 THEN 10 ELSE level END")
        con.execute("INSERT OR REPLACE INTO settings VALUES ('scale', 'notes-v2')")
    con.commit()


def init_db():
    con = connect()
    con.executescript(SCHEMA)
    migrate_scale(con)
    archive_current_draw(con)
    for row in con.execute("SELECT name, token FROM participants").fetchall():  # liens déjà distribués
        con.execute("INSERT OR IGNORE INTO tokens(key, token) VALUES (?, ?)",
                    (row["name"].strip().casefold(), row["token"]))
    con.commit()
    seed_from_env(con)
    con.close()


init_db()

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def now_iso():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


def fmt_dt(value):
    if not value:
        return ""
    d = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return d.strftime("%d/%m/%Y à %H:%M")


def get_participants():
    return db().execute(
        "SELECT id, name, token FROM participants ORDER BY name COLLATE NOCASE"
    ).fetchall()


def is_drawn():
    return db().execute("SELECT 1 FROM draw LIMIT 1").fetchone() is not None


def get_setting(key, default=None):
    row = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    db().execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))


def load_levels():
    return {(r["giver"], r["receiver"]): r["level"]
            for r in db().execute("SELECT giver, receiver, level FROM weights")}


def forced_pairs(levels):
    return [pair for pair, note in levels.items() if note == NOTE_FORCED]


def check_forced(levels):
    """Message d'erreur si les tirages imposés se contredisent, sinon None."""
    forced = forced_pairs(levels)
    givers = [a for a, _ in forced]
    receivers = [b for _, b in forced]
    if len(set(givers)) < len(givers):
        return "Une même personne ne peut pas être imposée à deux personnes."
    if len(set(receivers)) < len(receivers):
        return "Deux personnes ne peuvent pas être imposées à la même personne."
    return None


def weight_map(ids, levels):
    w = {(a, b): note_weight(levels.get((a, b), NOTE_DEFAULT))
         for a in ids for b in ids if a != b}
    forced = forced_pairs(levels)
    for a, b in forced:
        for x in ids:
            if x != a and x != b:
                w[(a, x)] = 0.0   # a n'offre qu'à b
                w[(x, b)] = 0.0   # personne d'autre n'offre à b
    for a, b in forced:
        w[(a, b)] = 1.0
    return w


def parse_years(value):
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def repeat_years():
    try:
        return max(0, min(5, int(get_setting("repeat_years", "1"))))
    except ValueError:
        return 1


def repeat_pairs(ids, years):
    """Paires (donneur, receveur) déjà tirées lors des `years` dernières années enregistrées."""
    if years <= 0:
        return set()
    con = db()
    seen = [r["year"] for r in con.execute(
        "SELECT DISTINCT year FROM history WHERE year < ? ORDER BY year DESC LIMIT ?",
        (current_year(), years))]
    if not seen:
        return set()
    by_key = {name_key(p["name"]): p["id"] for p in get_participants() if p["id"] in ids}
    marks = ",".join("?" * len(seen))
    pairs = set()
    for r in con.execute(f"SELECT giver, receiver FROM history WHERE year IN ({marks})", seen):
        g, x = by_key.get(r["giver"]), by_key.get(r["receiver"])
        if g and x:
            pairs.add((g, x))
    return pairs


def weight_plans(ids, levels, years):
    """Poids à essayer, du plus strict (paires des années passées interdites) au plus souple
    (très improbables). Une paire imposée (note 10) n'est jamais écartée."""
    base = weight_map(ids, levels)
    pairs = repeat_pairs(ids, years)
    if not pairs:
        return [(base, False)]
    forced = set(forced_pairs(levels))

    def scaled(factor):
        return {k: (v * factor if k in pairs and k not in forced else v) for k, v in base.items()}

    return [(scaled(0.0), False), (scaled(0.05), True)]


def parse_levels(payload, ids):
    out = {}
    for key, val in (payload.get("levels") or {}).items():
        try:
            giver, receiver = (int(x) for x in key.split("-"))
            val = int(val)
        except (ValueError, TypeError):
            continue
        if (giver in ids and receiver in ids and giver != receiver
                and NOTE_MIN <= val <= NOTE_MAX and val != NOTE_DEFAULT):
            out[(giver, receiver)] = val
    return out


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.context_processor
def inject():
    return {
        "csrf_token": csrf_token,
        "csrf_input": lambda: Markup(
            f'<input type="hidden" name="csrf" value="{csrf_token()}">'),
    }


@app.before_request
def csrf_protect():
    if request.method == "POST" and request.path.startswith("/admin"):
        expected = session.get("csrf")
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400)


@app.after_request
def security_headers(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
    if request.path.startswith(("/p/", "/admin")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            if request.is_json:
                return jsonify(ok=False, message="Session expirée : recharge la page."), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------
# Pages publiques
# --------------------------------------------------------------------------


@app.get("/")
def home():
    return render_template(
        "message.html", title="Secret Santa",
        text="Ouvre le lien personnel que l'organisateur t'a envoyé.")


@app.get("/healthz")
def healthz():
    return "ok"


@app.errorhandler(404)
def not_found(_e):
    return render_template(
        "message.html", title="Lien introuvable",
        text="Ce lien ne correspond à personne. Vérifie qu'il est complet, "
             "ou demande-le à l'organisateur."), 404


@app.route("/p/<token>", methods=["GET", "POST"])
def participant(token):
    me = db().execute("SELECT id, name FROM participants WHERE token=?", (token,)).fetchone()
    if not me:
        abort(404)
    row = db().execute(
        "SELECT p.name FROM draw d JOIN participants p ON p.id = d.receiver WHERE d.giver=?",
        (me["id"],)).fetchone()
    revealed, target = False, None
    if row and request.method == "POST":
        revealed, target = True, row["name"]
        db().execute("UPDATE draw SET viewed_at = COALESCE(viewed_at, ?) WHERE giver=?",
                     (now_iso(), me["id"]))
        db().commit()
    return render_template("reveal.html", me=me["name"], token=token, budget=get_setting("budget", ""),
                           drawn=row is not None, revealed=revealed, target=target)


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        given = request.form.get("password", "").encode()
        if hmac.compare_digest(given, ADMIN_PASSWORD.encode()):
            session.clear()
            session["admin"] = True
            session.permanent = True
            return redirect(url_for("admin"))
        time.sleep(1)  # freine les essais répétés
        flash("Mot de passe incorrect.", "error")
    return render_template("login.html")


@app.post("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/admin")
@admin_required
def admin():
    parts = get_participants()
    drawn = is_drawn()
    viewed = {r["giver"]: r["viewed_at"] for r in db().execute("SELECT giver, viewed_at FROM draw")}
    levels = load_levels()
    base = os.environ.get("PUBLIC_URL", request.host_url).rstrip("/")
    people = [{"id": p["id"], "name": p["name"],
               "url": f"{base}/p/{p['token']}", "viewed": viewed.get(p["id"])} for p in parts]
    data = {
        "participants": [{"id": p["id"], "name": p["name"]} for p in people],
        "notes": {"min": NOTE_MIN, "max": NOTE_MAX, "default": NOTE_DEFAULT, "forced": NOTE_FORCED},
        "state": {f"{a}-{b}": lvl for (a, b), lvl in levels.items()},
        "locked": drawn,
    }
    return render_template(
        "admin.html", people=people, drawn=drawn, data=data,
        drawn_at=fmt_dt(get_setting("drawn_at")),
        opened=sum(1 for v in viewed.values() if v),
        avoid_swaps=get_setting("avoid_swaps", "1") == "1", couple_note=COUPLE_NOTE,
        budget=get_setting("budget", ""), repeat_years=repeat_years(),
        history=[(r["year"], r["n"]) for r in db().execute(
            "SELECT year, COUNT(*) AS n FROM history GROUP BY year ORDER BY year DESC")])


@app.post("/admin/participants")
@admin_required
def add_participants():
    if is_drawn():
        flash("Le tirage est fait : réinitialise-le pour modifier la liste.", "error")
        return redirect(url_for("admin"))
    names = [n.strip()[:60] for n in re.split(r"[\n,;]+", request.form.get("names", ""))]
    added = skipped = 0
    for name in names:
        if not name:
            continue
        try:
            db().execute("INSERT INTO participants(name, token) VALUES (?, ?)",
                         (name, token_for(db(), name)))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1
    db().commit()
    msg = f"{added} participant(s) ajouté(s)."
    if skipped:
        msg += f" {skipped} déjà dans la liste."
    flash(msg, "ok" if added else "error")
    return redirect(url_for("admin"))


@app.post("/admin/participants/<int:pid>/delete")
@admin_required
def delete_participant(pid):
    if is_drawn():
        flash("Le tirage est fait : réinitialise-le pour modifier la liste.", "error")
    else:
        db().execute("DELETE FROM participants WHERE id=?", (pid,))
        db().commit()
    return redirect(url_for("admin"))


@app.get("/admin/backup")
@admin_required
def backup():
    """Copie de la base SANS les résultats du tirage ni l'historique (l'organisateur ne doit pas les voir)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dst = sqlite3.connect(path)
        db().backup(dst)
        dst.execute("DELETE FROM draw")
        dst.execute("DELETE FROM history")
        dst.execute("DELETE FROM settings WHERE key='drawn_at'")
        dst.commit()
        dst.execute("VACUUM")  # efface réellement les résultats du fichier
        dst.close()
        with open(path, "rb") as f:
            data = f.read()
    finally:
        os.unlink(path)
    resp = make_response(data)
    resp.headers["Content-Type"] = "application/octet-stream"
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="secret-santa-{datetime.now(TZ):%Y-%m-%d}.db"')
    return resp


@app.post("/admin/budget")
@admin_required
def save_budget():
    text = " ".join(request.form.get("budget", "").split())[:40]
    if re.fullmatch(r"\d+([.,]\d+)?", text):
        text += " €"   # « 30 » devient « 30 € »
    if text:
        set_setting("budget", text)
        flash(f"Budget enregistré : {text}. Il s'affiche sur la page de chaque participant.", "ok")
    else:
        db().execute("DELETE FROM settings WHERE key='budget'")
        flash("Budget supprimé : il n'est plus affiché aux participants.", "ok")
    db().commit()
    return redirect(url_for("admin"))


@app.post("/admin/couples")
@admin_required
def add_couples():
    if is_drawn():
        flash("Le tirage est fait : réinitialise-le pour modifier les réglages.", "error")
        return redirect(url_for("admin"))
    try:
        note = int(request.form.get("note", COUPLE_NOTE))
    except ValueError:
        note = COUPLE_NOTE
    note = max(NOTE_MIN, min(NOTE_MAX - 1, note))
    by_name = {p["name"].lower(): p["id"] for p in get_participants()}
    unknown, count = [], 0
    for group in re.split(r"[\n,;]+", request.form.get("groups", "")):
        names = [x.strip() for x in group.split("+") if x.strip()]
        members = []
        for name in names:
            if name.lower() in by_name:
                members.append(by_name[name.lower()])
            else:
                unknown.append(name)
        for a in members:
            for b in members:
                if a != b:
                    db().execute("INSERT OR REPLACE INTO weights VALUES (?, ?, ?)", (a, b, note))
        if len(members) > 1:
            count += 1
    db().commit()
    if unknown:
        flash("Noms inconnus, ignorés : " + ", ".join(unknown) + ".", "error")
    if count:
        flash(f"{count} groupe(s) enregistré(s) avec la note {note} entre leurs membres.", "ok")
    return redirect(url_for("admin"))


@app.post("/admin/weights")
@admin_required
def save_weights():
    if is_drawn():
        return jsonify(ok=False, message="Le tirage est fait : réinitialise-le pour modifier les réglages."), 409
    payload = request.get_json(silent=True) or {}
    ids = {p["id"] for p in get_participants()}
    levels = parse_levels(payload, ids)
    con = db()
    con.execute("DELETE FROM weights")
    con.executemany("INSERT INTO weights VALUES (?, ?, ?)",
                    [(a, b, lvl) for (a, b), lvl in levels.items()])
    set_setting("avoid_swaps", "1" if payload.get("avoid_swaps") else "0")
    set_setting("repeat_years", str(parse_years(payload.get("repeat_years"))))
    con.commit()
    return jsonify(ok=True)


@app.post("/admin/simulate")
@admin_required
def simulate_route():
    payload = request.get_json(silent=True) or {}
    ids = [p["id"] for p in get_participants()]
    if len(ids) < 3:
        return jsonify(ok=False, message="Il faut au moins 3 participants.")
    levels = parse_levels(payload, set(ids))
    problem = check_forced(levels)
    if problem:
        return jsonify(ok=False, message=problem)
    out, softened = None, False
    for w, softened in weight_plans(ids, levels, parse_years(payload.get("repeat_years", repeat_years()))):
        out = simulate(ids, w, avoid_swaps=bool(payload.get("avoid_swaps")),
                       swap_ok=forced_pairs(levels))
        if out is not None:
            break
    if out is None:
        return jsonify(ok=False, message="Aucun tirage n'est possible avec ces réglages. "
                                         "Assouplis une interdiction.")
    counts, runs = out
    pct = {f"{a}-{b}": round(100 * c / runs) for (a, b), c in counts.items()}
    return jsonify(ok=True, pct=pct, runs=runs, softened=softened)


@app.post("/admin/draw")
@admin_required
def do_draw():
    if is_drawn():
        flash("Le tirage a déjà été fait.", "error")
        return redirect(url_for("admin"))
    ids = [p["id"] for p in get_participants()]
    if len(ids) < 3:
        flash("Il faut au moins 3 participants.", "error")
        return redirect(url_for("admin"))
    levels = load_levels()
    problem = check_forced(levels)
    if problem:
        flash(problem, "error")
        return redirect(url_for("admin"))
    result, softened = None, False
    for w, softened in weight_plans(ids, levels, repeat_years()):
        result = run_draw(ids, w, avoid_swaps=get_setting("avoid_swaps", "1") == "1",
                          swap_ok=forced_pairs(levels))
        if result is not None:
            break
    if result is None:
        flash("Aucun tirage n'est possible avec ces réglages. "
              "Assouplis une interdiction ou autorise les échanges réciproques.", "error")
        return redirect(url_for("admin"))
    con = db()
    con.executemany("INSERT INTO draw(giver, receiver) VALUES (?, ?)", list(result.items()))
    set_setting("drawn_at", now_iso())
    record_history(con, current_year(), result)
    con.commit()
    flash("Tirage effectué. Envoie à chacun son lien personnel.", "ok")
    if softened:
        flash("Impossible d'éviter toutes les paires des années précédentes avec ces réglages : "
              "certaines ont été rendues très improbables.", "warn")
    return redirect(url_for("admin"))


@app.post("/admin/reset")
@admin_required
def reset_draw():
    con = db()
    con.execute("DELETE FROM draw")
    con.execute("DELETE FROM settings WHERE key='drawn_at'")
    con.commit()
    flash("Tirage réinitialisé. Les liens restent les mêmes.", "ok")
    return redirect(url_for("admin"))
