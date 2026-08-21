# run.py
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.app import create_app
from backend.config import Config

app = create_app()

if __name__ == '__main__':
    print("=" * 60)
    print("AMR Fleet Analytics Dashboard")
    print("=" * 60)
    print(f"Dashboard: http://localhost:{Config.PORT}")
    print("=" * 60)
    app.run(debug=Config.DEBUG, host=Config.HOST, port=Config.PORT)