"""Flask 应用入口"""
import os
import secrets
from flask import Flask, g, request, session, abort
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

from runtime_config import begin_request, end_request
from config_schema import ConfigError


@app.before_request
def bind_configuration():
    g.config_token = begin_request()
    # Configuration writes use optimistic versions and a CSRF token.
    session.setdefault("config_csrf", secrets.token_urlsafe(32))
    if request.method == "POST" and request.path in ("/admin/keywords", "/admin/config-restore"):
        supplied = request.form.get("config_csrf", "")
        if not supplied.isascii() or not secrets.compare_digest(supplied, session["config_csrf"]):
            abort(400, "表单已过期，请刷新页面后重新提交")


@app.teardown_request
def release_configuration(error=None):
    token = g.pop("config_token", None)
    if token is not None:
        end_request(token)


@app.errorhandler(ConfigError)
def configuration_error(error):
    return "配置不可用：" + str(error), 503


from routes.views import views_bp
from routes.admin import admin_bp

app.register_blueprint(views_bp)
app.register_blueprint(admin_bp)

if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5001"))
    app.run(debug=debug, host=host, port=port)
