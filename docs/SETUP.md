# Setup notes

Install the dependencies listed in `requirements.txt` inside an isolated virtual environment. This portfolio version has no required environment variables and intentionally contains no deployment credentials, analytics identifiers or live infrastructure configuration.

For a production deployment, configure a WSGI server such as Gunicorn, enforce upload size/type limits, use short-lived storage for generated files, and add monitoring and abuse protections.
