# Ahripi
my rpi server
Ahripi

Ahripi is a personal development repository used for experimenting with
self-hosted services, backend applications, and simple web interfaces.
The project is primarily intended to run on a Raspberry Pi but can also
be used on any Linux system.

It serves as a general-purpose playground for learning Python, Flask,
Linux server management, and Git-based workflows.

-----------------------------------------------------------------------

Project Structure

ahripi-dev/

backend/
    Backend applications and APIs (e.g. Flask services)

collectors/
    Background scripts and data collection tools

data/
    Stored data, generated files, or logs

web/
    Web frontend files (HTML, CSS, JavaScript)

web2/
    Alternative or newer version of the web frontend

.env
    Environment variables (not intended for version control)

requirements.txt
    Python dependencies

venv/
    Local Python virtual environment

-----------------------------------------------------------------------

Requirements

- Python 3.9 or newer
- pip
- Linux-based system (Raspberry Pi recommended)
- Optional: Python virtual environment (venv)

-----------------------------------------------------------------------

Setup

1. Clone the repository

   git clone https://github.com/<your-username>/Ahripi.git
   cd Ahripi

2. Create and activate a virtual environment

   python -m venv venv
   source venv/bin/activate

3. Install dependencies

   pip install -r requirements.txt

4. Configure environment variables

   Create a file named .env if required and define necessary variables,
   for example:

   FLASK_ENV=production
   FLASK_DEBUG=0

-----------------------------------------------------------------------

Running the Application

Depending on the backend setup, a Flask application can be started with:

   python backend/app.py

or

   flask run --host=0.0.0.0

The service will then be available via the Raspberry Pi's IP address and
the configured port.

-----------------------------------------------------------------------

Purpose

This repository is intended for:

- Learning backend development with Python and Flask
- Hosting small personal web services
- Running background scripts and collectors
- Experimenting with self-hosting on a Raspberry Pi
- Practicing Git workflows and Linux server management

This is a development and learning project, not a production-ready system.

-----------------------------------------------------------------------

Security Notes

- Do not commit secrets or credentials
- Keep the .env file out of version control
- If exposing services to the internet, additional measures such as
  firewalls, reverse proxies, and HTTPS should be used

-----------------------------------------------------------------------

License

Private / personal project.
No warranty or guarantees are provided.
