"""Flask 应用入口"""
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = "serp-news-secret-key"

from routes.views import views_bp
from routes.admin import admin_bp

app.register_blueprint(views_bp)
app.register_blueprint(admin_bp)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
