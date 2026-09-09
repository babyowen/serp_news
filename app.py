"""Flask 应用入口"""
import os
import secrets
from flask import Flask, g
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
if not os.getenv("FLASK_SECRET_KEY"):
    app.logger.warning("未设置 FLASK_SECRET_KEY，正在使用临时会话密钥；重启会使旧表单失效，多 worker 必须配置相同的稳定密钥，否则会出现会话和 CSRF 校验失败。")

from runtime_config import begin_request, end_request
from config_schema import ConfigError, ConfigVersionError, ConfigVersionNotFound


@app.before_request
def bind_configuration():
    g.config_token = begin_request()


@app.teardown_request
def release_configuration(error=None):
    token = g.pop("config_token", None)
    if token is not None:
        end_request(token)


@app.errorhandler(ConfigError)
def configuration_error(error):
    return "配置不可用：" + str(error), 503


@app.errorhandler(ConfigVersionError)
def configuration_version_error(error):
    return str(error), (404 if isinstance(error, ConfigVersionNotFound) else 400)


from routes.views import views_bp
from routes.admin import admin_bp

app.register_blueprint(views_bp)
app.register_blueprint(admin_bp)

if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5001"))
    app.run(debug=debug, host=host, port=port)
