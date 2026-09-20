from flask import Flask
from flask_cors import CORS
from backend.config import Config
from backend.db import init_db_pool, close_db
from backend.model import init_model
from backend.routes import api_bp

def create_app(config_class=Config):
    """Application factory pattern for creating the Flask app instance."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Enable Cross-Origin Resource Sharing (CORS) for local frontend dev
    CORS(app)

    # Initialize Database Connection Pool
    init_db_pool(app)
    
    # Register connection teardown callback
    app.teardown_appcontext(close_db)

    # Load Model once at application startup
    init_model(app)

    # Register API Blueprints
    app.register_blueprint(api_bp)

    return app

if __name__ == "__main__":
    app = create_app()
    # Run dev server on port 5000
    app.run(host="0.0.0.0", port=5000, debug=True)