# backend/app.py
"""
Flask Application Factory
"""
from flask import Flask, render_template
from backend.api.routes import api_bp
import os


def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
        static_folder=os.path.join(os.path.dirname(__file__), 'static'),
    )

    # Register API blueprint (all /api/* routes defined in routes.py)
    app.register_blueprint(api_bp)

    # Dashboard page
    @app.route('/')
    def index():
        return render_template('dashboard.html')

    return app