from psycopg2 import pool
from flask import g, current_app

db_pool = None

def init_db_pool(app):
    """Initializes a psycopg2 ThreadedConnectionPool using Flask config settings."""
    global db_pool
    if db_pool is None:
        db_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            host=app.config["DB_HOST"],
            port=app.config["DB_PORT"],
            dbname=app.config["DB_NAME"],
            user=app.config["DB_USER"],
            password=app.config["DB_PASSWORD"]
        )

def get_db():
    """Retrieves a database connection from the pool for the current request context."""
    if "db" not in g:
        g.db = db_pool.getconn()
    return g.db

def close_db(e=None):
    """Returns the database connection back to the pool at the end of the request."""
    db = g.pop("db", None)
    if db is not None and db_pool is not None:
        db_pool.putconn(db)