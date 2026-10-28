"""Delivery Hub: gestión centralizada de incidencias de Glovo, Uber Eats y Just Eat.

Todo sale del mismo servidor (servidor.py, puerto 8080):
- Web clásica (plantillas Jinja): /            (incluye Importar Excel en /importar)
- Frontend separado (carpeta frontend/): /app/
- API REST que usa el frontend: /api/          (ver api.py)
"""
import json
import os
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash

import db
import security
import services as svc
from constants import (ALERT_DAYS, DT_FMT, INCIDENT_TYPES, OPEN_STATUSES,
                       PLATFORMS, PRIORITIES, STATUSES)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, instance_path=os.path.join(BASE_DIR, "instance"))
os.makedirs(app.instance_path, exist_ok=True)
app.config["DATABASE"] = os.path.join(app.instance_path, "incidencias.db")
app.config["UPLOAD_DIR"] = os.path.join(app.instance_path, "uploads")
os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)


def _load_secret_key():
    """Usa SECRET_KEY del entorno o genera una persistente en instance/."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(app.instance_path, "secret_key")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


# Detrás de un túnel o proxy (Cloudflare, Render…) la IP real del visitante llega en X-Forwarded-For.
# Activar SOLO cuando la app esté detrás de ese proxy: TRUST_PROXY=1
if os.environ.get("TRUST_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.config.update(
    SECRET_KEY=_load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Con HTTPS (túnel o nube) la cookie de sesión solo viaja cifrada: HTTPS=1
    SESSION_COOKIE_SECURE=os.environ.get("HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # 10 MB por archivo subido
)
app.json.sort_keys = False  # mantener el orden de estados, tipos y plataformas en la API
db.init_app(app)

with app.app_context():
    # Una base nueva arranca vacía (solo usuario admin). Para datos de demostración: DEMO_DATA=1
    db.init_db(demo=os.environ.get("DEMO_DATA") == "1")

import api as import_api  # noqa: E402  (necesita `app` configurada)

app.register_blueprint(import_api.bp)

# --------------------------------------------------------------------------- #
# Plantillas
# --------------------------------------------------------------------------- #
app.add_template_filter(svc.fmt_money, "money")
app.add_template_filter(svc.fmt_dt, "dt")


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.context_processor
def inject_globals():
    return {
        "PLATFORMS": PLATFORMS,
        "INCIDENT_TYPES": INCIDENT_TYPES,
        "STATUSES": STATUSES,
        "PRIORITIES": PRIORITIES,
        "OPEN_STATUSES": OPEN_STATUSES,
        "ALERT_DAYS": ALERT_DAYS,
        "csrf_token": csrf_token,
        "days_since": svc.days_since,
        "is_admin": svc.is_admin,
    }


# --------------------------------------------------------------------------- #
# Autenticación y CSRF (solo web clásica; la API usa tokens Bearer)
# --------------------------------------------------------------------------- #
@app.after_request
def security_headers(resp):
    return security.add_security_headers(resp)


# Páginas a las que se puede entrar aunque haya que cambiar la contraseña
_ALLOWED_BEFORE_PASSWORD_CHANGE = {"account", "logout", "static", "frontend_index", "frontend_files"}


@app.before_request
def load_user_and_check_csrf():
    if request.path.startswith("/api/") or request.path.startswith("/app"):
        return  # la API y el frontend separado gestionan su propia autenticación
    g.user = None
    uid = session.get("user_id")
    if uid is not None:
        user = svc.load_user(uid)
        # La sesión deja de valer si la contraseña cambió (p. ej. desde otro dispositivo)
        if user is None or session.get("pv") != svc.password_version(user):
            session.clear()
        else:
            g.user = user

    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not secrets.compare_digest(sent, expected):
            abort(400, description="Token CSRF no válido. Recarga la página e inténtalo de nuevo.")

    if g.user is not None and g.user["must_change_password"] and request.endpoint not in _ALLOWED_BEFORE_PASSWORD_CHANGE:
        flash("Por seguridad, cambia la contraseña de fábrica antes de continuar.", "warning")
        return redirect(url_for("account"))

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def get_incident_or_404(iid):
    inc = svc.get_incident(iid)
    if inc is None:
        abort(404)
    return inc


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:64]
        password = request.form.get("password", "")[:256]
        ip = request.remote_addr or "?"
        wait = security.seconds_blocked(ip, username)
        if wait:
            flash(security.blocked_message(wait), "error")
            return render_template("login.html", username=username), 429
        user = db.get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            security.register_success(ip, username)
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["pv"] = svc.password_version(user)
            nxt = request.args.get("next", "")
            if not nxt.startswith("/") or nxt.startswith("//"):  # evita open redirect
                nxt = url_for("dashboard")
            return redirect(nxt)
        security.register_failure(ip, username)
        flash("Usuario o contraseña incorrectos.", "error")
        return render_template("login.html", username=username), 401
    return render_template("login.html", username="")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/cuenta", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        err = svc.change_password(g.user, request.form.get("current_password"), request.form.get("new_password"),
                                  request.form.get("confirm_password"))
        if err:
            flash(err, "error")
            return render_template("account.html"), 422
        user = svc.load_user(g.user["id"])
        session["pv"] = svc.password_version(user)  # esta sesión sigue; las demás se cierran
        flash("Contraseña cambiada. Las sesiones abiertas en otros dispositivos se han cerrado.", "success")
        return redirect(url_for("dashboard"))
    return render_template("account.html")


# --------------------------------------------------------------------------- #
# Frontend separado servido desde el mismo servidor: http://<servidor>/app/
# (así basta con publicar una sola dirección). La web clásica usa también su hoja de estilos.
# --------------------------------------------------------------------------- #
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")


@app.get("/app")
def frontend_redirect():
    return redirect("/app/")


@app.get("/app/")
def frontend_index():
    resp = send_from_directory(FRONTEND_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/app/<path:filename>")
def frontend_files(filename):
    if filename.startswith("_"):  # archivos temporales de pruebas
        abort(404)
    resp = send_from_directory(FRONTEND_DIR, filename)
    resp.headers["Cache-Control"] = "no-cache"  # el navegador comprueba siempre si hay una versión nueva
    return resp


# --------------------------------------------------------------------------- #
# Panel y listado
# --------------------------------------------------------------------------- #
@app.get("/")
@login_required
def dashboard():
    try:
        days = int(request.args.get("dias", 30))
    except ValueError:
        days = 30
    return render_template("dashboard.html", **svc.dashboard_data(days))


@app.get("/incidencias")
@login_required
def incidents():
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    result = svc.list_incidents(request.args, page)
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    return render_template(
        "incidents.html", **result, args=args, restaurants=svc.get_restaurants(),
        page_url=lambda p: url_for("incidents", **args, page=p),
        export_url=url_for("export_csv", **args),
    )


@app.get("/incidencias/exportar.csv")
@login_required
def export_csv():
    return Response(
        svc.export_csv_text(request.args),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=incidencias_{date.today().isoformat()}.csv"},
    )


# --------------------------------------------------------------------------- #
# Alta / edición
# --------------------------------------------------------------------------- #
@app.route("/incidencias/nueva", methods=["GET", "POST"])
@login_required
def incident_new():
    if request.method == "POST":
        data, errors = svc.parse_incident_form(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("incident_form.html", form=request.form, restaurants=svc.get_restaurants(),
                                   editing=False), 400
        dup = svc.find_duplicate(data["platform"], data["order_ref"], data["restaurant_id"])
        iid = svc.create_incident(data)
        flash(f"Incidencia #{iid} registrada.", "success")
        if dup:
            flash(f"Ojo: ya existía otra incidencia para este pedido (#{dup}).", "warning")
        return redirect(url_for("incident_detail", iid=iid))

    form = {"order_date": datetime.now().strftime("%Y-%m-%dT%H:%M"), "priority": "media"}
    if request.args.get("platform") in PLATFORMS:
        form["platform"] = request.args["platform"]
    return render_template("incident_form.html", form=form, restaurants=svc.get_restaurants(), editing=False)


@app.route("/incidencias/<int:iid>/editar", methods=["GET", "POST"])
@login_required
def incident_edit(iid):
    inc = get_incident_or_404(iid)
    if request.method == "POST":
        data, errors = svc.parse_incident_form(request.form)
        if not errors:
            errors = svc.update_incident(inc, data)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("incident_form.html", form=request.form, restaurants=svc.get_restaurants(),
                                   editing=True, inc=inc), 400
        flash("Cambios guardados.", "success")
        return redirect(url_for("incident_detail", iid=iid))

    form = dict(inc)
    form["order_date"] = datetime.strptime(inc["order_date"], DT_FMT).strftime("%Y-%m-%dT%H:%M")
    form["order_amount"] = f"{inc['order_amount']:.2f}"
    form["claimed_amount"] = f"{inc['claimed_amount']:.2f}"
    form["platform_claim_ref"] = inc["platform_claim_ref"] or ""
    return render_template("incident_form.html", form=form, restaurants=svc.get_restaurants(), editing=True, inc=inc)


# --------------------------------------------------------------------------- #
# Detalle, estado, comentarios
# --------------------------------------------------------------------------- #
@app.get("/incidencias/<int:iid>")
@login_required
def incident_detail(iid):
    inc = get_incident_or_404(iid)
    try:
        source = json.loads(inc["source_data"] or "[]")
    except ValueError:
        source = []
    return render_template("incident_detail.html", inc=inc, events=svc.get_events(iid),
                           claim_text=svc.build_claim_text(inc), source=source)


# --------------------------------------------------------------------------- #
# Importar los Excel de Uber Eats, Just Eat y Glovo (misma lógica que la API, ver api.py)
# --------------------------------------------------------------------------- #
def admin_only(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not svc.is_admin():
            abort(403, description="Solo el administrador importa los Excel (traen todos los restaurantes).")
        return view(*args, **kwargs)
    return wrapped


def _render_review(path, file_id, filename, ref_date=None):
    ctx = import_api.analyse_upload(path, filename, ref_date)
    return render_template("import_review.html", r=import_api.public_preview(ctx, file_id, filename))


@app.route("/importar", methods=["GET", "POST"])
@login_required
@admin_only
def import_upload():
    if request.method == "POST":
        try:
            file_id, path, filename = import_api.save_upload(request.files.get("file"))
            return _render_review(path, file_id, filename)
        except import_api.ImportProblem as exc:
            flash(str(exc), "error")
    return render_template("import.html", batches=import_api.list_batches())


@app.post("/importar/revisar")
@login_required
@admin_only
def import_review():
    f = request.form
    try:
        return _render_review(import_api.find_upload(f.get("file_id")), f.get("file_id"), f.get("filename", ""),
                              import_api.parse_ref_date(f.get("ref_date")))
    except import_api.ImportProblem as exc:
        flash(str(exc), "error")
        return redirect(url_for("import_upload"))


@app.post("/importar/confirmar")
@login_required
@admin_only
def import_confirm():
    f = request.form
    try:
        result = import_api.run_import(import_api.find_upload(f.get("file_id")), f.get("filename", ""),
                                       import_api.parse_ref_date(f.get("ref_date")))
    except import_api.ImportProblem as exc:
        flash(str(exc), "error")
        return redirect(url_for("import_upload"))
    flash(f"{result['created']} incidencias importadas ({svc.fmt_money(result['claimed_total'])} a reclamar).", "success")
    for x in result["created_restaurants"]:
        flash(f"Local creado: {x['name']} ({x['code']}).", "success")
    return redirect(url_for("incidents", batch=result["batch_id"]))


@app.post("/incidencias/<int:iid>/estado")
@login_required
def incident_status(iid):
    inc = get_incident_or_404(iid)
    _ok, msg, level = svc.change_status(inc, request.form)
    flash(msg, level)
    return redirect(url_for("incident_detail", iid=iid))


@app.post("/incidencias/<int:iid>/comentario")
@login_required
def incident_comment(iid):
    inc = get_incident_or_404(iid)
    if svc.add_comment(inc, request.form.get("comment")):
        flash("Comentario añadido.", "success")
    else:
        flash("El comentario está vacío.", "error")
    return redirect(url_for("incident_detail", iid=iid) + "#historial")


@app.post("/incidencias/<int:iid>/eliminar")
@login_required
def incident_delete(iid):
    if not svc.is_admin():
        abort(403)
    get_incident_or_404(iid)
    svc.delete_incident(iid)
    flash(f"Incidencia #{iid} eliminada.", "success")
    return redirect(url_for("incidents"))


# --------------------------------------------------------------------------- #
# Errores (la API devuelve JSON, ver api.py)
# --------------------------------------------------------------------------- #
@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def http_error(err):
    if request.path.startswith("/api/"):
        return {"error": err.description}, err.code
    titles = {400: "Solicitud no válida", 403: "Acceso denegado", 404: "No encontrado", 413: "Archivo demasiado grande"}
    return render_template("error.html", code=err.code, title=titles.get(err.code, "Error"),
                           description=err.description), err.code


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
