"""
main.py -- App Entry Point
===========================
Single entry point for the bundled app.

  --specialist stem|hass    Run a specialist server in a subprocess.
  (no flags)                Launch the Ollama-style chat UI.
"""

import sys


def main() -> None:
    # Subprocess dispatch (frozen build re-invokes itself for specialists)
    if "--specialist" in sys.argv:
        idx = sys.argv.index("--specialist")
        spec = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "stem"
        from specialist import run_specialist
        run_specialist(spec)
        return

    from app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
