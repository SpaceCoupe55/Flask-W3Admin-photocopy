import os

from w3admin import create_app

app = create_app()

if __name__ == '__main__':
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, port=5000)

