#!/usr/bin/env python3
"""
Daytrading Analyse Dashboard — Starter Script
Installiert Abhaengigkeiten und startet Server + Dashboard.
"""

import subprocess
import sys
import os
import webbrowser
import time
import threading

DIR = os.path.dirname(os.path.abspath(__file__))

def install_deps():
    print("\n[1/3] Installiere Abhaengigkeiten...")
    req = os.path.join(DIR, "requirements.txt")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req, "-q"])
    print("      Fertig!")

def start_server():
    print("\n[2/3] Starte Server auf http://localhost:5000 ...")
    server = os.path.join(DIR, "server.py")
    subprocess.Popen([sys.executable, server], cwd=DIR)

def open_dashboard():
    time.sleep(2)
    dashboard = os.path.join(DIR, "dashboard.html")
    url = f"file://{dashboard}"
    print(f"\n[3/3] Oeffne Dashboard: {url}")
    webbrowser.open(url)

if __name__ == "__main__":
    print("=" * 50)
    print("  DAYTRADING ANALYSE DASHBOARD")
    print("=" * 50)

    install_deps()
    start_server()

    t = threading.Thread(target=open_dashboard)
    t.start()

    print("\n Server laeuft! Dashboard oeffnet sich gleich...")
    print(" Druecke Ctrl+C zum Beenden.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nServer wird beendet...")
        sys.exit(0)
